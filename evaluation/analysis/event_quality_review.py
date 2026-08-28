"""Evaluate event timelines on the frozen specialty benchmark cases.

The script never creates new companies or synthetic events. ``prepare`` reads
the existing 30 event cases, reruns the same tool arguments only to materialize
the event, cluster and relation details that the performance benchmark omitted,
and writes a review packet for an external LLM. ``aggregate`` validates that
review packet and computes deterministic metrics without calling an LLM.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from evaluation.analysis.report_batch import DEFAULT_OUTPUT_ROOT
from schemas.enums import ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from tools.event_timeline.interface import event_timeline


BENCHMARK_FILENAME = "specialty_tool_benchmark.json"
INPUT_FILENAME = "event_llm_review_input.jsonl"
PROMPT_FILENAME = "event_llm_review_prompt.md"
REVIEW_FILENAME = "event_llm_review_result.jsonl"
SCORES_FILENAME = "event_quality_scores.csv"
SUMMARY_FILENAME = "event_quality_summary.json"
PROMPT_VERSION = "event-quality-review-v1"

SCORE_COLUMNS = (
    "case_id",
    "company_id",
    "event_count",
    "key_event_count",
    "key_event_hit_count",
    "key_event_recall",
    "pair_tp",
    "pair_fp",
    "pair_fn",
    "pairwise_precision",
    "pairwise_recall",
    "pairwise_f1",
    "supported_relation_count",
    "unsupported_relation_count",
    "uncertain_relation_count",
    "severe_temporal_break",
    "deterministic_temporal_violation_count",
    "cutoff_violation_count",
    "review_notes",
)

REVIEW_PROMPT = """# FinTrace事件脉络外部复核提示词

你是金融公告事件脉络的独立复核员。请逐行读取
`event_llm_review_input.jsonl`，每一行对应一个既有实验案例。不得联网，
不得使用模型记忆补充输入中不存在的事实，也不得新增、删除或改写事件ID。

本次复核只评价现有事件索引中的事件组织质量，不评价系统是否从全部原始公告中
漏抽了事件。系统事件簇仅供比较，不能直接作为参考划分。

对每个案例依次完成以下工作：

1. 先阅读 `events`，识别对理解该公司事件发展过程不可缺少的关键事件，将其
   `event_id` 写入 `key_event_ids`。不要因为事件标题醒目就机械标为关键节点。
2. 根据事件主题、公告文号、涉及主体、事件阶段和时间连续性，独立形成
   `reference_clusters`。每个事件必须且只能出现一次；不能确认有关联时应拆成
   单例簇，不能仅因日期接近就合并。
3. 逐项复核 `system_relations`。只有输入中的标题、摘要、公告文号或阶段能够明确
   支持关系时才写 `supported`；明确不支持时写 `unsupported`；证据不足时写
   `uncertain`。时间先后本身只能支持 `FOLLOWED_BY`，不能证明回复、整改或解决。
4. 若事件顺序或关系方向存在会改变事件发展含义的明显错误，将
   `severe_temporal_break` 设为 true。普通日期精度不足不属于严重断裂。

每个案例仅输出一个JSON对象，每行一个对象，保存到
`event_llm_review_result.jsonl`。不要输出Markdown代码块或额外说明。格式如下：

{
  "case_id": "EVENT-QUALITY-001",
  "company_id": "603377.SH",
  "key_event_ids": ["EVENT-001"],
  "reference_clusters": [
    {
      "reference_cluster_id": "REF-CLUSTER-001",
      "event_ids": ["EVENT-001", "EVENT-002"],
      "reason": "两条记录分别为监管问询及对应回复"
    }
  ],
  "relation_reviews": [
    {
      "source_event_id": "EVENT-002",
      "target_event_id": "EVENT-001",
      "relation_type": "RESPONDS_TO",
      "verdict": "supported",
      "reason": "公告文号与事件阶段相互印证"
    }
  ],
  "severe_temporal_break": false,
  "review_notes": "简短说明判断边界"
}

严格要求：

- `case_id` 和 `company_id` 必须原样返回；
- `key_event_ids` 只能引用该案例 `events` 中的ID，可以为空；
- `reference_clusters` 必须完整覆盖全部事件，且不得重复；
- `reference_cluster_id` 在当前案例内不得重复；
- `relation_reviews` 必须逐条覆盖全部 `system_relations`，不得新增关系；
- `verdict` 只能是 `supported`、`unsupported` 或 `uncertain`；
- 判断不充分时从严选择 `uncertain`，不得猜测因果关系。
"""


def prepare_inputs(
    batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    output_dir = output_root / batch_id
    benchmark_path = output_dir / BENCHMARK_FILENAME
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    cutoff = str(benchmark.get("knowledge_cutoff") or "")
    if not cutoff:
        raise ValueError("specialty benchmark has no knowledge_cutoff")

    query_cases = benchmark.get("event_query", {}).get("cases") or []
    cluster_cases = benchmark.get("event_cluster", {}).get("cases") or []
    if not query_cases or len(query_cases) != len(cluster_cases):
        raise ValueError("event_query and event_cluster benchmark cases are incomplete")
    cluster_by_company = {str(item["company_id"]): item for item in cluster_cases}
    if len(cluster_by_company) != len(cluster_cases):
        raise ValueError("event_cluster benchmark contains duplicate companies")

    records: list[dict[str, Any]] = []
    for sequence, query_case in enumerate(query_cases, start=1):
        company_id = str(query_case["company_id"])
        cluster_case = cluster_by_company.get(company_id)
        if cluster_case is None:
            raise ValueError(f"missing event_cluster case for {company_id}")
        query_data = _run_existing_case(
            company_id=company_id,
            operation="event_query",
            cutoff=cutoff,
            sequence=sequence,
        )
        cluster_data = _run_existing_case(
            company_id=company_id,
            operation="event_cluster",
            cutoff=cutoff,
            sequence=sequence,
        )
        query_events = query_data["events"]
        cluster_events = cluster_data["events"]
        query_ids = [str(item["event_id"]) for item in query_events]
        cluster_ids = [str(item["event_id"]) for item in cluster_events]
        if query_ids != cluster_ids:
            raise ValueError(
                f"event_query and event_cluster returned different events for {company_id}"
            )

        compact_clusters = [
            {
                "cluster_id": str(cluster["cluster_id"]),
                "event_ids": [str(item["event_id"]) for item in cluster.get("events") or []],
                "event_type": cluster.get("event_type"),
                "start_date": cluster.get("start_date"),
                "end_date": cluster.get("end_date"),
                "match_reasons": cluster.get("match_reasons") or [],
            }
            for cluster in cluster_data["clusters"]
        ]
        deterministic = _deterministic_checks(
            query_events,
            compact_clusters,
            cluster_data["relations"],
            cutoff=cutoff,
        )
        records.append(
            {
                "case_id": f"EVENT-QUALITY-{sequence:03d}",
                "company_id": company_id,
                "knowledge_cutoff": cutoff,
                "case_source": {
                    "benchmark_file": BENCHMARK_FILENAME,
                    "available_event_count": query_case.get("available_event_count"),
                    "start_date": query_case.get("start_date"),
                    "end_date": query_case.get("end_date"),
                    "event_query_execution_time_ms": query_case.get("execution_time_ms"),
                    "event_cluster_execution_time_ms": cluster_case.get("execution_time_ms"),
                },
                "events": query_events,
                "system_clusters": compact_clusters,
                "system_relations": cluster_data["relations"],
                "deterministic_checks": deterministic,
            }
        )

    input_path = output_dir / INPUT_FILENAME
    prompt_path = output_dir / PROMPT_FILENAME
    _write_jsonl(input_path, records)
    prompt_path.write_text(REVIEW_PROMPT, encoding="utf-8")
    return {
        "batch_id": batch_id,
        "case_count": len(records),
        "event_count": sum(len(item["events"]) for item in records),
        "cluster_count": sum(len(item["system_clusters"]) for item in records),
        "relation_count": sum(len(item["system_relations"]) for item in records),
        "input_path": str(input_path),
        "prompt_path": str(prompt_path),
        "expected_review_path": str(output_dir / REVIEW_FILENAME),
    }


def aggregate_results(
    batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    output_dir = output_root / batch_id
    inputs = _read_jsonl(output_dir / INPUT_FILENAME)
    reviews = _read_jsonl(output_dir / REVIEW_FILENAME)
    if not inputs:
        raise ValueError(f"event review input not found or empty: {output_dir / INPUT_FILENAME}")
    review_by_id = _index_unique(reviews, label="event LLM review")
    input_by_id = _index_unique(inputs, label="event review input")
    missing = sorted(set(input_by_id) - set(review_by_id))
    extra = sorted(set(review_by_id) - set(input_by_id))
    if missing or extra:
        raise ValueError(
            f"event review case mismatch; missing={missing[:5]}, extra={extra[:5]}"
        )

    rows: list[dict[str, Any]] = []
    for case_id, item in input_by_id.items():
        review = review_by_id[case_id]
        validated = validate_review(item, review)
        rows.append(score_case(item, validated))

    with (output_dir / SCORES_FILENAME).open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    totals = {
        "key": sum(int(item["key_event_count"]) for item in rows),
        "hit": sum(int(item["key_event_hit_count"]) for item in rows),
        "tp": sum(int(item["pair_tp"]) for item in rows),
        "fp": sum(int(item["pair_fp"]) for item in rows),
        "fn": sum(int(item["pair_fn"]) for item in rows),
    }
    precision, recall, f1 = _pairwise_metrics(
        totals["tp"], totals["fp"], totals["fn"]
    )
    benchmark = json.loads(
        (output_dir / BENCHMARK_FILENAME).read_text(encoding="utf-8")
    )
    event_query_p95 = benchmark.get("event_query", {}).get("p95_ms")
    event_cluster_p95 = benchmark.get("event_cluster", {}).get("p95_ms")
    p95_values = [float(value) for value in (event_query_p95, event_cluster_p95) if value is not None]
    summary = {
        "batch_id": batch_id,
        "prompt_version": PROMPT_VERSION,
        "case_source": BENCHMARK_FILENAME,
        "case_count": len(rows),
        "key_event_count": totals["key"],
        "key_event_hit_count": totals["hit"],
        "key_event_recall": _safe_ratio(totals["hit"], totals["key"]),
        "cluster_pairwise_precision": precision,
        "cluster_pairwise_recall": recall,
        "cluster_pairwise_f1": f1,
        "supported_relation_count": sum(int(item["supported_relation_count"]) for item in rows),
        "unsupported_relation_count": sum(int(item["unsupported_relation_count"]) for item in rows),
        "uncertain_relation_count": sum(int(item["uncertain_relation_count"]) for item in rows),
        "severe_temporal_break_count": sum(item["severe_temporal_break"] == "yes" for item in rows),
        "deterministic_temporal_violation_count": sum(int(item["deterministic_temporal_violation_count"]) for item in rows),
        "cutoff_violation_count": sum(int(item["cutoff_violation_count"]) for item in rows),
        "event_query_p95_ms": event_query_p95,
        "event_cluster_p95_ms": event_cluster_p95,
        "p95_ms": max(p95_values) if p95_values else None,
        "scope_note": (
            "Key-event recall measures preservation within the frozen event index; "
            "it does not measure extraction recall over all raw announcements."
        ),
        "validation": {"complete": len(rows) == len(inputs)},
        "provenance": {
            "benchmark_sha256": _sha256(output_dir / BENCHMARK_FILENAME),
            "inputs_sha256": _sha256(output_dir / INPUT_FILENAME),
            "reviews_sha256": _sha256(output_dir / REVIEW_FILENAME),
        },
    }
    (output_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def validate_review(case_input: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case_input["case_id"])
    if str(review.get("case_id") or "") != case_id:
        raise ValueError(f"{case_id}: review case_id mismatch")
    if str(review.get("company_id") or "") != str(case_input["company_id"]):
        raise ValueError(f"{case_id}: review company_id mismatch")

    event_ids = {str(item["event_id"]) for item in case_input["events"]}
    key_event_ids = _string_list(review.get("key_event_ids"), f"{case_id}.key_event_ids")
    if len(set(key_event_ids)) != len(key_event_ids):
        raise ValueError(f"{case_id}: duplicate key_event_ids")
    unknown_keys = sorted(set(key_event_ids) - event_ids)
    if unknown_keys:
        raise ValueError(f"{case_id}: unknown key_event_ids {unknown_keys[:5]}")

    reference_clusters = review.get("reference_clusters")
    if not isinstance(reference_clusters, list) or not reference_clusters:
        raise ValueError(f"{case_id}: reference_clusters must be a non-empty list")
    assigned: list[str] = []
    cluster_ids: list[str] = []
    normalized_clusters: list[dict[str, Any]] = []
    for index, cluster in enumerate(reference_clusters, start=1):
        if not isinstance(cluster, dict):
            raise ValueError(f"{case_id}: reference cluster {index} is not an object")
        cluster_id = str(cluster.get("reference_cluster_id") or "").strip()
        if not cluster_id:
            raise ValueError(f"{case_id}: reference cluster {index} has no id")
        members = _string_list(
            cluster.get("event_ids"), f"{case_id}.reference_clusters[{index}].event_ids"
        )
        if not members:
            raise ValueError(f"{case_id}: reference cluster {cluster_id} is empty")
        cluster_ids.append(cluster_id)
        assigned.extend(members)
        normalized_clusters.append(
            {
                "reference_cluster_id": cluster_id,
                "event_ids": members,
                "reason": str(cluster.get("reason") or ""),
            }
        )
    if len(set(cluster_ids)) != len(cluster_ids):
        raise ValueError(f"{case_id}: duplicate reference_cluster_id values")
    if len(set(assigned)) != len(assigned):
        raise ValueError(f"{case_id}: an event appears in multiple reference clusters")
    if set(assigned) != event_ids:
        missing = sorted(event_ids - set(assigned))
        unknown = sorted(set(assigned) - event_ids)
        raise ValueError(
            f"{case_id}: reference cluster coverage mismatch; "
            f"missing={missing[:5]}, unknown={unknown[:5]}"
        )

    expected_relations = {
        _relation_key(item) for item in case_input.get("system_relations") or []
    }
    relation_reviews = review.get("relation_reviews")
    if not isinstance(relation_reviews, list):
        raise ValueError(f"{case_id}: relation_reviews must be a list")
    normalized_relations: list[dict[str, Any]] = []
    actual_relation_keys: list[tuple[str, str, str]] = []
    for relation in relation_reviews:
        if not isinstance(relation, dict):
            raise ValueError(f"{case_id}: relation review is not an object")
        key = _relation_key(relation)
        verdict = str(relation.get("verdict") or "")
        if verdict not in {"supported", "unsupported", "uncertain"}:
            raise ValueError(f"{case_id}: invalid relation verdict {verdict!r}")
        actual_relation_keys.append(key)
        normalized_relations.append(
            {
                "source_event_id": key[0],
                "target_event_id": key[1],
                "relation_type": key[2],
                "verdict": verdict,
                "reason": str(relation.get("reason") or ""),
            }
        )
    if len(set(actual_relation_keys)) != len(actual_relation_keys):
        raise ValueError(f"{case_id}: duplicate relation reviews")
    if set(actual_relation_keys) != expected_relations:
        missing = sorted(expected_relations - set(actual_relation_keys))
        extra = sorted(set(actual_relation_keys) - expected_relations)
        raise ValueError(
            f"{case_id}: relation review coverage mismatch; "
            f"missing={missing[:3]}, extra={extra[:3]}"
        )
    if not isinstance(review.get("severe_temporal_break"), bool):
        raise ValueError(f"{case_id}: severe_temporal_break must be boolean")
    return {
        "key_event_ids": key_event_ids,
        "reference_clusters": normalized_clusters,
        "relation_reviews": normalized_relations,
        "severe_temporal_break": review["severe_temporal_break"],
        "review_notes": str(review.get("review_notes") or ""),
    }


def score_case(case_input: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    key_ids = set(review["key_event_ids"])
    timeline_ids = {
        str(event_id)
        for cluster in case_input["system_clusters"]
        for event_id in cluster["event_ids"]
    }
    key_hits = len(key_ids & timeline_ids)
    system_pairs = _cluster_pairs(case_input["system_clusters"], "event_ids")
    reference_pairs = _cluster_pairs(review["reference_clusters"], "event_ids")
    tp = len(system_pairs & reference_pairs)
    fp = len(system_pairs - reference_pairs)
    fn = len(reference_pairs - system_pairs)
    precision, recall, f1 = _pairwise_metrics(tp, fp, fn)
    verdicts = [item["verdict"] for item in review["relation_reviews"]]
    deterministic = case_input["deterministic_checks"]
    return {
        "case_id": case_input["case_id"],
        "company_id": case_input["company_id"],
        "event_count": len(case_input["events"]),
        "key_event_count": len(key_ids),
        "key_event_hit_count": key_hits,
        "key_event_recall": _safe_ratio(key_hits, len(key_ids)),
        "pair_tp": tp,
        "pair_fp": fp,
        "pair_fn": fn,
        "pairwise_precision": precision,
        "pairwise_recall": recall,
        "pairwise_f1": f1,
        "supported_relation_count": verdicts.count("supported"),
        "unsupported_relation_count": verdicts.count("unsupported"),
        "uncertain_relation_count": verdicts.count("uncertain"),
        "severe_temporal_break": _yes_no(review["severe_temporal_break"]),
        "deterministic_temporal_violation_count": deterministic["temporal_violation_count"],
        "cutoff_violation_count": deterministic["cutoff_violation_count"],
        "review_notes": review["review_notes"],
    }


def _run_existing_case(
    *, company_id: str, operation: str, cutoff: str, sequence: int
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "operation": operation,
        "entity_ids": [company_id],
        "knowledge_cutoff": cutoff,
        "limit": 100,
    }
    if operation == "event_cluster":
        arguments["window_days"] = 30
    result = event_timeline(
        ToolCall(
            tool_call_id=f"EVENT-QUALITY-{operation}-{sequence:03d}",
            tool_name=ToolName.EVENT_TIMELINE,
            reason="Materialize the frozen specialty event benchmark result.",
            arguments=arguments,
        )
    )
    if result.status != ToolStatus.SUCCESS:
        error = result.error.message if result.error else "unknown event tool failure"
        raise RuntimeError(f"{company_id} {operation} failed: {error}")
    data = result.data if isinstance(result.data, dict) else {}
    return {
        "events": data.get("events") or [],
        "clusters": data.get("clusters") or [],
        "relations": data.get("relations") or [],
    }


def _deterministic_checks(
    events: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    *,
    cutoff: str,
) -> dict[str, Any]:
    event_ids = [str(item["event_id"]) for item in events]
    dates = [(str(item.get("event_date") or ""), str(item["event_id"])) for item in events]
    clustered = [str(event_id) for cluster in clusters for event_id in cluster["event_ids"]]
    temporal_violations = sum(left > right for left, right in zip(dates, dates[1:]))
    cutoff_violations = sum(
        bool(item.get("announcement_date")) and str(item["announcement_date"]) > cutoff
        for item in events
    )
    event_dates = {str(item["event_id"]): str(item.get("event_date") or "") for item in events}
    relation_direction_violations = 0
    for relation in relations:
        source_date = event_dates.get(str(relation.get("source_event_id")), "")
        target_date = event_dates.get(str(relation.get("target_event_id")), "")
        relation_type = str(relation.get("relation_type") or "")
        if relation_type == "FOLLOWED_BY" and source_date > target_date:
            relation_direction_violations += 1
        if relation_type != "FOLLOWED_BY" and source_date < target_date:
            relation_direction_violations += 1
    return {
        "event_ids_unique": len(set(event_ids)) == len(event_ids),
        "all_events_clustered_once": len(clustered) == len(set(clustered)) and set(clustered) == set(event_ids),
        "temporal_violation_count": temporal_violations + relation_direction_violations,
        "cutoff_violation_count": cutoff_violations,
    }


def _cluster_pairs(clusters: list[dict[str, Any]], member_key: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for cluster in clusters:
        members = sorted(set(str(item) for item in cluster.get(member_key) or []))
        pairs.update(combinations(members, 2))
    return pairs


def _pairwise_metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else (1.0 if fp == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 6), round(recall, 6), round(f1, 6)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _relation_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("source_event_id") or ""),
        str(item.get("target_event_id") or ""),
        str(item.get("relation_type") or ""),
    )


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return [str(item) for item in value]


def _index_unique(records: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        case_id = str(record.get("case_id") or "")
        if not case_id:
            raise ValueError(f"{label} contains an empty case_id")
        if case_id in indexed:
            raise ValueError(f"{label} contains duplicate case_id {case_id}")
        indexed[case_id] = record
    return indexed


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record at {path}:{line_number} is not an object")
            records.append(record)
    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--stage", choices=("prepare", "aggregate"), required=True)
    args = parser.parse_args()
    if args.stage == "prepare":
        result = prepare_inputs(args.batch_id, args.output_root)
    else:
        result = aggregate_results(args.batch_id, args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
