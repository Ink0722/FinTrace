from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.financial_analysis.comparison import compare_companies, compare_periods
from tools.financial_analysis.config import FinancialAnalysisConfig
from tools.financial_analysis.evidence import build_financial_evidence
from tools.financial_analysis.metric_catalog import METRIC_CATALOG, period_type
from tools.financial_analysis.query import build_metric_query_result, find_missing_combinations
from tools.financial_analysis.repository import FinancialRepository, validate_index_snapshot


class FinancialAnalysisArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    query: str | None = None
    company_ids: list[str] = Field(min_length=1)
    metric_codes: list[str] = Field(min_length=1)
    report_periods: list[date] = Field(min_length=1)
    statement_types: list[
        Literal["balance_sheet", "income_statement", "cashflow_statement"]
    ] | None = None
    currency: str = "CNY"
    comparison_method: Literal["absolute", "percent", "both"] = "both"
    knowledge_cutoff: date | None = None

    @field_validator("company_ids", "metric_codes")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized):
            raise ValueError("collection values must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("collection values must not contain duplicates")
        return normalized

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if value != "CNY":
            raise ValueError("the current normalized financial dataset only supports CNY")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "FinancialAnalysisArguments":
        unknown_metrics = sorted(set(self.metric_codes) - set(METRIC_CATALOG))
        if unknown_metrics:
            raise ValueError(f"unsupported metric_codes: {unknown_metrics}")
        periods = [value.isoformat() for value in self.report_periods]
        if len(periods) != len(set(periods)):
            raise ValueError("report_periods must not contain duplicates")
        if self.operation == "metric_query":
            return self
        if self.operation == "metric_compare":
            period_comparison = len(self.company_ids) == 1 and len(self.report_periods) >= 2
            company_comparison = len(self.company_ids) >= 2 and len(self.report_periods) == 1
            if not period_comparison and not company_comparison:
                raise ValueError(
                    "metric_compare requires one company and at least two periods, "
                    "or at least two companies and one period"
                )
            if period_comparison:
                ytd_metrics = [
                    code for code in self.metric_codes if METRIC_CATALOG[code].value_nature == "year_to_date"
                ]
                period_types = {period_type(value.isoformat()) for value in self.report_periods}
                if ytd_metrics and (len(period_types) != 1 or "NON_STANDARD" in period_types):
                    raise ValueError(
                        "year_to_date metrics can only be compared across matching period types"
                    )
            return self
        raise ValueError(f"unsupported operation: {self.operation}")


def financial_analysis(call: ToolCall) -> ToolResult:
    started = time.perf_counter()
    if call.arguments.get("operation") == "risk_scan":
        return _failed(
            call,
            started,
            ErrorType.UNSUPPORTED_QUERY,
            "risk_scan is not implemented in the current financial_analysis version.",
        )
    try:
        arguments = FinancialAnalysisArguments.model_validate(call.arguments)
        config = FinancialAnalysisConfig.from_env()
    except (ValidationError, TypeError, ValueError) as exc:
        return _failed(
            call,
            started,
            ErrorType.INVALID_ARGUMENT,
            f"Invalid financial_analysis arguments or configuration: {exc}",
            details={"arguments": call.arguments},
        )

    repository = FinancialRepository(config.index_path)
    if not repository.available():
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            f"Financial metric index not found: {config.index_path}",
            details={
                "build_command": "python -m data_pipeline.financial.build_index",
                "normalized_dir": str(config.normalized_dir),
            },
        )

    snapshot_errors = validate_index_snapshot(config.index_path, config.normalized_dir)
    if snapshot_errors:
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            "Financial metric index is stale or incomplete; rebuild it from normalized data.",
            details={
                "errors": snapshot_errors,
                "build_command": "python -m data_pipeline.financial.build_index",
            },
        )

    report_periods = [value.isoformat() for value in arguments.report_periods]
    try:
        records = repository.query_metrics(
            company_ids=arguments.company_ids,
            report_periods=report_periods,
            metric_codes=arguments.metric_codes,
            statement_types=arguments.statement_types,
            currency=arguments.currency,
            knowledge_cutoff=arguments.knowledge_cutoff,
        )
    except sqlite3.Error as exc:
        return _failed(
            call,
            started,
            ErrorType.TEMPORARY_DATABASE_ERROR,
            f"Financial metric index query failed: {type(exc).__name__}: {exc}",
            retryable=isinstance(exc, sqlite3.OperationalError),
        )
    if not records:
        return _failed(
            call,
            started,
            ErrorType.DATA_NOT_AVAILABLE,
            "No financial metrics matched the requested companies, periods, metrics and cutoff.",
            details={
                "company_ids": arguments.company_ids,
                "report_periods": report_periods,
                "metric_codes": arguments.metric_codes,
                "knowledge_cutoff": arguments.knowledge_cutoff.isoformat()
                if arguments.knowledge_cutoff
                else None,
            },
        )

    warnings: list[str] = []
    if arguments.knowledge_cutoff is None:
        warnings.append(
            "knowledge_cutoff was not provided; results use all disclosures available in the normalized snapshot."
        )
    missing_combinations = find_missing_combinations(
        records,
        company_ids=arguments.company_ids,
        report_periods=report_periods,
        metric_codes=arguments.metric_codes,
    )
    if arguments.operation == "metric_query":
        values, missing = build_metric_query_result(
            records,
            company_ids=arguments.company_ids,
            report_periods=report_periods,
            metric_codes=arguments.metric_codes,
        )
        if missing:
            warnings.append(f"{len(missing)} requested company-period-metric combinations are missing.")
        data = {
            "operation": "metric_query",
            "company_ids": arguments.company_ids,
            "report_periods": report_periods,
            "metric_codes": arguments.metric_codes,
            "values": values,
            "missing": missing,
        }
    elif len(arguments.company_ids) == 1:
        if missing_combinations:
            warnings.append(
                f"{len(missing_combinations)} comparison points are missing and are returned as null."
            )
        comparisons, comparison_warnings = compare_periods(
            records,
            company_id=arguments.company_ids[0],
            report_periods=report_periods,
            metric_codes=arguments.metric_codes,
            comparison_method=arguments.comparison_method,
        )
        warnings.extend(comparison_warnings)
        data = {
            "operation": "metric_compare",
            "comparison_dimension": "period",
            "comparison_method": arguments.comparison_method,
            "comparisons": comparisons,
        }
    else:
        if missing_combinations:
            warnings.append(
                f"{len(missing_combinations)} comparison points are missing and are returned as null."
            )
        comparisons, comparison_warnings = compare_companies(
            records,
            company_ids=arguments.company_ids,
            report_period=report_periods[0],
            metric_codes=arguments.metric_codes,
            comparison_method=arguments.comparison_method,
        )
        warnings.extend(comparison_warnings)
        data = {
            "operation": "metric_compare",
            "comparison_dimension": "company",
            "comparison_method": arguments.comparison_method,
            "comparisons": comparisons,
        }
    data["knowledge_cutoff"] = (
        arguments.knowledge_cutoff.isoformat() if arguments.knowledge_cutoff else None
    )
    data["record_count"] = len(records)
    data["message"] = f"financial_analysis {arguments.operation} completed"
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.FINANCIAL_ANALYSIS,
        status=ToolStatus.SUCCESS,
        data=data,
        evidence=build_financial_evidence(records, used_by=call.tool_call_id),
        warnings=list(dict.fromkeys(warnings)),
        metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)),
    )


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
        tool_name=ToolName.FINANCIAL_ANALYSIS,
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
