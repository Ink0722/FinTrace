from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.ownership_analysis.config import OwnershipAnalysisConfig
from tools.ownership_analysis.evidence import build_holding_evidence
from tools.ownership_analysis.holdings import (
    RANK_SOURCE,
    compare_snapshots,
    concentration,
    duplicate_holder_flags,
    holder_to_dict,
    rank_holders,
)
from tools.ownership_analysis.repository import (
    HolderRecord,
    OwnershipRepository,
    validate_ownership_index_snapshot,
)


DATA_LIMITATION = (
    "本结果仅基于主要股东披露数据，可能遗漏非主要股东、非上市企业、协议控制及一致行动关系，"
    "不构成完整股权或实际控制人认定。"
)
EXIT_LIMITATION = "退出主要股东名单不等于已经清仓，只能说明该股东未出现在后续主要股东快照中。"
BUILD_COMMAND = "python -m data_pipeline.ownership.build_index"


class OwnershipAnalysisArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    query: str | None = None
    company_ids: list[str] = Field(default_factory=list)
    holder_ids: list[str] = Field(default_factory=list)
    as_of_date: date | None = None
    holder_types: list[Literal["PERSON", "COMPANY"]] | None = None
    top_n: int = Field(default=10, ge=1, le=50)
    start_date: date | None = None
    end_date: date | None = None
    change_threshold: float | None = Field(default=None, ge=0, le=100)
    knowledge_cutoff: date | None = None

    @field_validator("company_ids")
    @classmethod
    def normalize_company_ids(cls, values: list[str]) -> list[str]:
        return _normalize_collection(values, upper=True)

    @field_validator("holder_ids")
    @classmethod
    def normalize_holder_ids(cls, values: list[str]) -> list[str]:
        return _normalize_collection(values, upper=False)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "OwnershipAnalysisArguments":
        if self.operation == "holding_query":
            if not self.company_ids and not self.holder_ids:
                raise ValueError("holding_query requires at least one of company_ids or holder_ids")
            return self
        if self.operation == "holding_compare":
            if len(self.company_ids) != 1:
                raise ValueError("holding_compare requires exactly one company_id")
            if self.start_date is None or self.end_date is None:
                raise ValueError("holding_compare requires start_date and end_date")
            if self.end_date <= self.start_date:
                raise ValueError("holding_compare requires end_date later than start_date")
            return self
        raise ValueError(f"unsupported operation: {self.operation}")


def _normalize_collection(values: list[str], *, upper: bool) -> list[str]:
    normalized = [value.strip() for value in values]
    if upper:
        normalized = [value.upper() for value in normalized]
    if any(not value for value in normalized):
        raise ValueError("collection values must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("collection values must not contain duplicates")
    return normalized


@dataclass
class HolderResolution:
    per_term: list[dict] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self, entity_map: dict[str, dict]) -> dict:
        return {
            "terms": [
                {
                    "term": item["term"],
                    "entity_ids": item["entity_ids"],
                    "status": item["status"],
                    "identity_quality": entity_map[item["entity_ids"][0]].get("identity_quality")
                    if item["entity_ids"] and item["entity_ids"][0] in entity_map
                    else None,
                }
                for item in self.per_term
            ],
            "entity_ids": self.entity_ids,
        }


def ownership_analysis(call: ToolCall) -> ToolResult:
    started = time.perf_counter()
    if call.arguments.get("operation") == "penetration":
        return _failed(
            call,
            started,
            ErrorType.UNSUPPORTED_QUERY,
            "penetration is not implemented in the current ownership_analysis version.",
        )
    try:
        arguments = OwnershipAnalysisArguments.model_validate(call.arguments)
        config = OwnershipAnalysisConfig.from_env()
    except (ValidationError, TypeError, ValueError) as exc:
        return _failed(
            call,
            started,
            ErrorType.INVALID_ARGUMENT,
            f"Invalid ownership_analysis arguments or configuration: {exc}",
            details={"arguments": call.arguments},
        )

    repository = OwnershipRepository(config.index_path)
    if not repository.available():
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            f"Ownership holdings index not found: {config.index_path}",
            details={
                "build_command": BUILD_COMMAND,
                "normalized_dir": str(config.normalized_dir),
            },
        )
    snapshot_errors = validate_ownership_index_snapshot(config.index_path, config.normalized_dir)
    if snapshot_errors:
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            "Ownership holdings index is stale or incomplete; rebuild it from normalized data.",
            details={"errors": snapshot_errors, "build_command": BUILD_COMMAND},
        )

    try:
        if arguments.operation == "holding_query":
            return _holding_query(call, started, arguments, config, repository)
        return _holding_compare(call, started, arguments, config, repository)
    except sqlite3.Error as exc:
        return _failed(
            call,
            started,
            ErrorType.TEMPORARY_DATABASE_ERROR,
            f"Ownership holdings index query failed: {type(exc).__name__}: {exc}",
            retryable=isinstance(exc, sqlite3.OperationalError),
        )


def _holding_query(
    call: ToolCall,
    started: float,
    arguments: OwnershipAnalysisArguments,
    config: OwnershipAnalysisConfig,
    repository: OwnershipRepository,
) -> ToolResult:
    warnings: list[str] = []
    as_of = arguments.as_of_date.isoformat() if arguments.as_of_date else None
    cutoff = arguments.knowledge_cutoff.isoformat() if arguments.knowledge_cutoff else None
    if as_of is None:
        warnings.append(
            "as_of_date was not provided; the latest disclosed snapshot per company is used "
            "and the actual dates are returned in the results."
        )

    resolution = _resolve_holders(repository, arguments.holder_ids, has_company_scope=bool(arguments.company_ids))
    warnings.extend(resolution.warnings)
    matched_entity_ids = set(resolution.entity_ids)

    companies_out: list[dict] = []
    used_records: list[HolderRecord] = []

    if arguments.company_ids:
        direction = "cross_filter" if matched_entity_ids else "company_to_holders"
        missing_companies: list[str] = []
        for company_id in arguments.company_ids:
            meta = repository.effective_snapshot(company_id, as_of=as_of, knowledge_cutoff=cutoff)
            if meta is None:
                missing_companies.append(company_id)
                continue
            records = repository.snapshot_records(company_id, meta.holder_end_date, meta.announcement_date)
            dup_flags = duplicate_holder_flags(records)
            ranked = rank_holders(records)
            filtered = ranked
            if matched_entity_ids:
                filtered = [record for record in filtered if record.holder_entity_id in matched_entity_ids]
            if arguments.holder_types:
                allowed_types = set(arguments.holder_types)
                filtered = [record for record in filtered if record.holder_category in allowed_types]
            sliced = filtered[: arguments.top_n]
            companies_out.append(
                {
                    "company_id": company_id,
                    "snapshot": {**meta.as_dict(), "quality_flags": [*meta.quality_flags, *dup_flags]},
                    "holders": [holder_to_dict(record) for record in sliced],
                    "concentration": concentration(records),
                    "truncated": len(filtered) > len(sliced),
                }
            )
            used_records.extend(sliced)
            warnings.extend(_snapshot_warnings(company_id, meta))
        if missing_companies:
            warnings.append(
                f"No disclosed snapshot found for companies: {missing_companies}"
                + (" at the requested observation date." if as_of else ".")
            )
        if not companies_out:
            return _failed(
                call,
                started,
                ErrorType.DATA_NOT_AVAILABLE,
                "No shareholder snapshot matched the requested companies and dates.",
                details={
                    "company_ids": arguments.company_ids,
                    "as_of_date": as_of,
                    "knowledge_cutoff": cutoff,
                },
            )
    else:
        direction = "holder_to_companies"
        records = repository.reverse_holdings(resolution.entity_ids, as_of=as_of, knowledge_cutoff=cutoff)
        if not records:
            return _failed(
                call,
                started,
                ErrorType.DATA_NOT_AVAILABLE,
                "The resolved holders do not appear in any effective company snapshot.",
                details={"holder_ids": arguments.holder_ids, "as_of_date": as_of},
            )
        entity_map = repository.entity_map(resolution.entity_ids)
        if any(entity_map.get(entity_id, {}).get("identity_quality") == "unresolved" for entity_id in resolution.entity_ids):
            warnings.append(
                "部分股东主体缺少主体代码（identity_quality=unresolved），同名主体未做跨公司合并，反查结果可能不完整。"
            )
        by_company: dict[str, list[HolderRecord]] = {}
        for record in records:
            by_company.setdefault(record.target_company_id, []).append(record)
        ordered_companies = sorted(
            by_company, key=lambda company: -max(record.holding_ratio for record in by_company[company])
        )
        truncated = len(ordered_companies) > arguments.top_n
        if truncated:
            warnings.append(
                f"Reverse query matched {len(ordered_companies)} companies; only the top {arguments.top_n} by holding ratio are returned."
            )
        for company_id in ordered_companies[: arguments.top_n]:
            holdings = by_company[company_id]
            meta = repository.effective_snapshot(company_id, as_of=as_of, knowledge_cutoff=cutoff)
            companies_out.append(
                {
                    "company_id": company_id,
                    "snapshot": meta.as_dict() if meta else None,
                    "holdings": [holder_to_dict(record) for record in holdings],
                    "truncated": False,
                }
            )
            used_records.extend(holdings)
            if meta:
                warnings.extend(_snapshot_warnings(company_id, meta))

    limitations = [DATA_LIMITATION]
    if direction != "holder_to_companies":
        limitations.append(f"股东排名按同一快照内持股比例降序计算（rank_source={RANK_SOURCE}），非原始披露排名。")

    data = {
        "operation": "holding_query",
        "direction": direction,
        "as_of_date": as_of,
        "knowledge_cutoff": cutoff,
        "resolved_holders": resolution.as_dict(repository.entity_map(resolution.entity_ids)),
        "companies": companies_out,
        "record_count": len(used_records),
        "limitations": limitations,
        "message": "ownership_analysis holding_query completed",
    }
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.OWNERSHIP_ANALYSIS,
        status=ToolStatus.SUCCESS,
        data=data,
        evidence=build_holding_evidence(used_records, call.tool_call_id, config.shareholders_path),
        warnings=list(dict.fromkeys(warnings)),
        metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)),
    )


def _holding_compare(
    call: ToolCall,
    started: float,
    arguments: OwnershipAnalysisArguments,
    config: OwnershipAnalysisConfig,
    repository: OwnershipRepository,
) -> ToolResult:
    warnings: list[str] = []
    cutoff = arguments.knowledge_cutoff.isoformat() if arguments.knowledge_cutoff else None
    company_id = arguments.company_ids[0]
    start_as_of = arguments.start_date.isoformat()
    end_as_of = arguments.end_date.isoformat()

    start_meta = repository.effective_snapshot(company_id, as_of=start_as_of, knowledge_cutoff=cutoff)
    end_meta = repository.effective_snapshot(company_id, as_of=end_as_of, knowledge_cutoff=cutoff)
    missing_boundaries = [
        boundary
        for boundary, meta in (("start_date", start_meta), ("end_date", end_meta))
        if meta is None
    ]
    if missing_boundaries:
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            "No disclosed snapshot available at one or both comparison boundaries.",
            details={
                "company_id": company_id,
                "start_date": start_as_of,
                "end_date": end_as_of,
                "missing_boundaries": missing_boundaries,
            },
        )

    start_records = repository.snapshot_records(company_id, start_meta.holder_end_date, start_meta.announcement_date)
    end_records = repository.snapshot_records(company_id, end_meta.holder_end_date, end_meta.announcement_date)

    resolution = _resolve_holders(repository, arguments.holder_ids, has_company_scope=True)
    warnings.extend(resolution.warnings)
    matched_entity_ids = set(resolution.entity_ids)
    if matched_entity_ids:
        start_records = [record for record in start_records if record.holder_entity_id in matched_entity_ids]
        end_records = [record for record in end_records if record.holder_entity_id in matched_entity_ids]
        if not start_records and not end_records:
            return _failed(
                call,
                started,
                ErrorType.DATA_NOT_AVAILABLE,
                "The filtered holders do not appear in either comparison snapshot.",
                details={
                    "company_id": company_id,
                    "holder_ids": arguments.holder_ids,
                },
            )

    diff = compare_snapshots(rank_holders(start_records), rank_holders(end_records))

    below_threshold_count = 0
    if arguments.change_threshold is not None:
        threshold = arguments.change_threshold

        def meets_threshold(entry: dict) -> bool:
            change = entry.get("ratio_change_raw_pct")
            return change is not None and abs(change) >= threshold

        for key in ("increased", "decreased"):
            kept = [entry for entry in diff[key] if meets_threshold(entry)]
            below_threshold_count += len(diff[key]) - len(kept)
            diff[key] = kept
        if below_threshold_count:
            warnings.append(
                f"{below_threshold_count} changed holdings are below change_threshold={threshold} percentage points and are not listed."
            )

    for meta, boundary in ((start_meta, "start"), (end_meta, "end")):
        warnings.extend(_snapshot_warnings(company_id, meta, prefix=f"{boundary} boundary: "))

    start_by_entity = {record.holder_entity_id: record for record in start_records}
    end_by_entity = {record.holder_entity_id: record for record in end_records}
    evidence_records: list[HolderRecord] = []
    for entry in diff["entered"]:
        evidence_records.append(end_by_entity[entry["holder_entity_id"]])
    for entry in diff["exited"]:
        evidence_records.append(start_by_entity[entry["holder_entity_id"]])
    for entry in [*diff["increased"], *diff["decreased"]]:
        evidence_records.append(start_by_entity[entry["holder_entity_id"]])
        evidence_records.append(end_by_entity[entry["holder_entity_id"]])

    data = {
        "operation": "holding_compare",
        "company_id": company_id,
        "start": {"as_of_date": start_as_of, "snapshot": start_meta.as_dict()},
        "end": {"as_of_date": end_as_of, "snapshot": end_meta.as_dict()},
        "change_threshold": arguments.change_threshold,
        "entered": diff["entered"],
        "exited": diff["exited"],
        "increased": diff["increased"],
        "decreased": diff["decreased"],
        "unchanged_count": diff["unchanged_count"],
        "below_threshold_count": below_threshold_count,
        "limitations": [DATA_LIMITATION, EXIT_LIMITATION],
        "message": "ownership_analysis holding_compare completed",
    }
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.OWNERSHIP_ANALYSIS,
        status=ToolStatus.SUCCESS,
        data=data,
        evidence=build_holding_evidence(evidence_records, call.tool_call_id, config.shareholders_path),
        warnings=list(dict.fromkeys([*warnings, EXIT_LIMITATION])),
        metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)),
    )


def _resolve_holders(
    repository: OwnershipRepository, holder_ids: list[str], *, has_company_scope: bool
) -> HolderResolution:
    if not holder_ids:
        return HolderResolution()
    resolution = HolderResolution(per_term=repository.resolve_holder_terms(holder_ids))
    for item in resolution.per_term:
        if item["status"] == "not_found":
            resolution.warnings.append(f"holder_ids 项 “{item['term']}” 无法解析为已知股东主体。")
        elif item["status"] == "ambiguous":
            resolution.warnings.append(
                f"holder_ids 项 “{item['term']}” 匹配到 {len(item['entity_ids'])} 个主体，已全部纳入查询。"
            )
        resolution.entity_ids.extend(item["entity_ids"])
    resolution.entity_ids = list(dict.fromkeys(resolution.entity_ids))
    if not resolution.entity_ids and has_company_scope:
        resolution.warnings.append(
            "holder_ids 未匹配到任何已知股东主体；本次查询未按股东过滤，返回的是完整快照结果。"
        )
    return resolution


def _snapshot_warnings(company_id: str, meta, *, prefix: str = "") -> list[str]:
    warnings: list[str] = []
    if meta.snapshot_scope != "top_ten":
        warnings.append(
            f"{prefix}{company_id} 快照 scope={meta.snapshot_scope}（{meta.record_count} 条记录），"
            "与标准十大股东口径比较需谨慎。"
        )
    if "snapshot_ratio_sum_over_100" in meta.quality_flags:
        warnings.append(f"{prefix}{company_id} 快照持股比例合计为 {meta.ratio_sum:.4%}，超过 100%，数据存在异常。")
    return warnings


def _failed(
    call: ToolCall,
    started: float,
    error_type: ErrorType,
    message: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
) -> ToolResult:
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.OWNERSHIP_ANALYSIS,
        status=ToolStatus.FAILED,
        error=ToolError(
            error_type=error_type,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
        metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
