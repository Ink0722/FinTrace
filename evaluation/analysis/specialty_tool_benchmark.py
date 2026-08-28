"""Benchmark deep ownership paths and event timeline operations on frozen indexes."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from evaluation.analysis.report_batch import DEFAULT_OUTPUT_ROOT
from schemas.enums import ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from tools.event_timeline.config import EventTimelineConfig
from tools.event_timeline.interface import event_timeline
from tools.ownership_analysis.config import OwnershipAnalysisConfig
from tools.ownership_analysis.interface import ownership_analysis


DEFAULT_CUTOFF = "2026-05-28"
DEFAULT_SAMPLE_SIZE = 30
RESULT_FILENAME = "specialty_tool_benchmark.json"


def run_benchmark(
    batch_id: str,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    cutoff: str = DEFAULT_CUTOFF,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> dict[str, Any]:
    if sample_size < 3:
        raise ValueError("sample_size must be at least 3")

    ownership_config = OwnershipAnalysisConfig.from_env()
    event_config = EventTimelineConfig.from_env()
    ownership_depth_3_cases = select_ownership_cases(
        ownership_config.index_path,
        cutoff=cutoff,
        sample_size=sample_size,
        depths=(3,),
    )
    ownership_cases = select_ownership_cases(
        ownership_config.index_path,
        cutoff=cutoff,
        sample_size=sample_size,
        depths=(4, 5, 6),
    )
    event_cases = select_event_cases(
        event_config.index_path, cutoff=cutoff, sample_size=sample_size
    )
    if len(ownership_depth_3_cases) < sample_size:
        raise ValueError(
            f"Only {len(ownership_depth_3_cases)} depth-3 ownership cases are available; "
            f"{sample_size} are required."
        )
    if len(ownership_cases) < sample_size:
        raise ValueError(
            f"Only {len(ownership_cases)} deep ownership cases are available; "
            f"{sample_size} are required."
        )
    if len(event_cases) < sample_size:
        raise ValueError(
            f"Only {len(event_cases)} multi-event companies are available; "
            f"{sample_size} are required."
        )

    # Warm-up validates index availability and removes one-time filesystem effects.
    _run_ownership_case(ownership_depth_3_cases[0], cutoff=cutoff, sequence=0)
    _run_event_case(event_cases[0], operation="event_query", cutoff=cutoff, sequence=0)
    _run_event_case(event_cases[0], operation="event_cluster", cutoff=cutoff, sequence=0)

    ownership_depth_3_results = [
        _run_ownership_case(case, cutoff=cutoff, sequence=index)
        for index, case in enumerate(ownership_depth_3_cases, start=1)
    ]
    ownership_results = [
        _run_ownership_case(case, cutoff=cutoff, sequence=index)
        for index, case in enumerate(ownership_cases, start=1)
    ]
    event_query_results = [
        _run_event_case(case, operation="event_query", cutoff=cutoff, sequence=index)
        for index, case in enumerate(event_cases, start=1)
    ]
    event_cluster_results = [
        _run_event_case(case, operation="event_cluster", cutoff=cutoff, sequence=index)
        for index, case in enumerate(event_cases, start=1)
    ]

    ownership_depth_3_summary = summarize_results(ownership_depth_3_results)
    ownership_depth_3_summary["depth_counts"] = dict(
        sorted(Counter(item["expected_depth"] for item in ownership_depth_3_results).items())
    )
    ownership_summary = summarize_results(ownership_results)
    ownership_summary["depth_counts"] = dict(
        sorted(Counter(item["expected_depth"] for item in ownership_results).items())
    )
    event_query_summary = summarize_results(event_query_results)
    event_cluster_summary = summarize_results(event_cluster_results)
    result = {
        "batch_id": batch_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "knowledge_cutoff": cutoff,
        "sample_selection": {
            "ownership_depth_3": (
                f"{sample_size} unique source-target pairs with shortest depth 3"
            ),
            "ownership_depth_gt_3": (
                f"{sample_size} unique source-target pairs stratified across shortest "
                "depths 4, 5 and 6"
            ),
            "events": (
                f"{sample_size} companies with the largest multi-event sets visible "
                "at the cutoff"
            ),
            "warm_up": "one unrecorded call per operation",
            "timing_scope": "validated tool interface through complete structured result assembly; excludes Agent, LLM and frontend",
        },
        "ownership_penetration_depth_3": {
            **ownership_depth_3_summary,
            "cases": ownership_depth_3_results,
        },
        "ownership_penetration": {**ownership_summary, "cases": ownership_results},
        "event_query": {**event_query_summary, "cases": event_query_results},
        "event_cluster": {**event_cluster_summary, "cases": event_cluster_results},
        "competition_result": {
            "ownership_penetration_p95_ms": ownership_summary["p95_ms"],
            "event_timeline_p95_ms": max(
                event_query_summary["p95_ms"], event_cluster_summary["p95_ms"]
            ),
            "threshold_ms": 5000,
            "ownership_meets_threshold": _meets_threshold(ownership_summary["p95_ms"]),
            "event_timeline_meets_threshold": _meets_threshold(
                max(event_query_summary["p95_ms"], event_cluster_summary["p95_ms"])
            ),
        },
    }
    output_dir = output_root / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / RESULT_FILENAME
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def select_ownership_cases(
    index_path: Path,
    *,
    cutoff: str,
    sample_size: int,
    depths: tuple[int, ...] = (4, 5, 6),
) -> list[dict[str, Any]]:
    if not depths or any(depth < 1 for depth in depths):
        raise ValueError("depths must contain positive integers")
    graph: dict[str, set[str]] = defaultdict(set)
    with sqlite3.connect(index_path) as connection:
        rows = connection.execute(
            """
            WITH candidate AS (
                SELECT target_company_id, holder_end_date,
                       MAX(announcement_date) AS announcement_date
                FROM holder_records
                WHERE announcement_date <= ? AND holder_end_date <= ?
                GROUP BY target_company_id, holder_end_date
            ),
            effective AS (
                SELECT target_company_id, holder_end_date, announcement_date
                FROM (
                    SELECT candidate.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY target_company_id
                               ORDER BY holder_end_date DESC
                           ) AS snapshot_rank
                    FROM candidate
                )
                WHERE snapshot_rank = 1
            )
            SELECT link.company_id, record.target_company_id
            FROM holder_records record
            JOIN effective snapshot
              ON record.target_company_id = snapshot.target_company_id
             AND record.holder_end_date = snapshot.holder_end_date
             AND record.announcement_date = snapshot.announcement_date
            JOIN holder_company_links link
              ON link.holder_entity_id = record.holder_entity_id
            """,
            (cutoff, cutoff),
        )
        for source, target in rows:
            if source != target:
                graph[str(source)].add(str(target))

    by_depth: dict[int, list[dict[str, Any]]] = {depth: [] for depth in depths}
    for source in sorted(graph):
        distances = _shortest_distances(graph, source, max_depth=max(depths))
        for target, depth in sorted(distances.items()):
            if depth in by_depth:
                by_depth[depth].append(
                    {
                        "source_entity_id": source,
                        "target_entity_id": target,
                        "expected_depth": depth,
                    }
                )

    quotas = _balanced_quotas(sample_size, depths)
    selected: list[dict[str, Any]] = []
    for depth in depths:
        selected.extend(_diverse_cases(by_depth[depth], quotas[depth]))
    return selected


def select_event_cases(
    index_path: Path, *, cutoff: str, sample_size: int
) -> list[dict[str, Any]]:
    with sqlite3.connect(index_path) as connection:
        rows = connection.execute(
            """
            SELECT company_id, COUNT(*) AS event_count,
                   MIN(event_date) AS start_date, MAX(event_date) AS end_date
            FROM events
            WHERE announcement_date <= ?
            GROUP BY company_id
            HAVING COUNT(*) >= 2
            ORDER BY event_count DESC, company_id ASC
            LIMIT ?
            """,
            (cutoff, sample_size),
        ).fetchall()
    return [
        {
            "company_id": str(company_id),
            "available_event_count": int(event_count),
            "start_date": str(start_date),
            "end_date": str(end_date),
        }
        for company_id, event_count, start_date, end_date in rows
    ]


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(item["execution_time_ms"]) for item in results]
    ordered = sorted(durations)
    return {
        "sample_count": len(results),
        "success_count": sum(item["status"] == ToolStatus.SUCCESS.value for item in results),
        "failure_count": sum(item["status"] != ToolStatus.SUCCESS.value for item in results),
        "p50_ms": round(_percentile(ordered, 0.50), 2) if ordered else None,
        "p95_ms": round(_percentile(ordered, 0.95), 2) if ordered else None,
        "max_ms": round(max(ordered), 2) if ordered else None,
    }


def _run_ownership_case(case: dict[str, Any], *, cutoff: str, sequence: int) -> dict[str, Any]:
    arguments = {
        "operation": "penetration",
        "source_entity_id": case["source_entity_id"],
        "target_entity_id": case["target_entity_id"],
        "as_of_date": cutoff,
        "knowledge_cutoff": cutoff,
        "max_depth": case["expected_depth"],
        "max_paths": 10,
    }
    call = ToolCall(
        tool_call_id=f"BENCH-OWN-{sequence:03d}",
        tool_name=ToolName.OWNERSHIP_ANALYSIS,
        reason="Controlled ownership penetration performance benchmark.",
        arguments=arguments,
    )
    result = ownership_analysis(call)
    data = result.data if isinstance(result.data, dict) else {}
    return {
        **case,
        "status": result.status.value,
        "execution_time_ms": result.metrics.execution_time_ms,
        "returned_path_count": len(data.get("paths") or []),
        "request_arguments": arguments,
        "returned_paths": data.get("paths") or [],
        "search_summary": data.get("search_summary") or {},
        "error": result.error.message if result.error else None,
    }


def _run_event_case(
    case: dict[str, Any], *, operation: str, cutoff: str, sequence: int
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "operation": operation,
        "entity_ids": [case["company_id"]],
        "knowledge_cutoff": cutoff,
        "limit": 100,
    }
    if operation == "event_cluster":
        arguments["window_days"] = 30
    call = ToolCall(
        tool_call_id=f"BENCH-EVT-{operation}-{sequence:03d}",
        tool_name=ToolName.EVENT_TIMELINE,
        reason="Controlled event timeline performance benchmark.",
        arguments=arguments,
    )
    result = event_timeline(call)
    data = result.data if isinstance(result.data, dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return {
        **case,
        "operation": operation,
        "status": result.status.value,
        "execution_time_ms": result.metrics.execution_time_ms,
        "returned_event_count": int(summary.get("event_count") or 0),
        "returned_cluster_count": int(summary.get("cluster_count") or 0),
        "error": result.error.message if result.error else None,
    }


def _shortest_distances(
    graph: dict[str, set[str]], source: str, *, max_depth: int
) -> dict[str, int]:
    distances = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        depth = distances[node]
        if depth >= max_depth:
            continue
        for target in sorted(graph.get(node, ())):
            if target in distances:
                continue
            distances[target] = depth + 1
            queue.append(target)
    distances.pop(source, None)
    return distances


def _balanced_quotas(total: int, groups: Iterable[int]) -> dict[int, int]:
    keys = list(groups)
    base, remainder = divmod(total, len(keys))
    return {key: base + (index < remainder) for index, key in enumerate(keys)}


def _diverse_cases(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    for case in cases:
        if case["source_entity_id"] in used_sources:
            continue
        selected.append(case)
        used_sources.add(case["source_entity_id"])
        if len(selected) == limit:
            return selected
    for case in cases:
        if case in selected:
            continue
        selected.append(case)
        if len(selected) == limit:
            break
    return selected


def _percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _meets_threshold(value: float | None) -> bool | None:
    return None if value is None else value <= 5000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    args = parser.parse_args()
    result = run_benchmark(
        args.batch_id,
        output_root=args.output_root,
        cutoff=args.cutoff,
        sample_size=args.sample_size,
    )
    print(json.dumps({
        "batch_id": result["batch_id"],
        "ownership_penetration": {
            key: result["ownership_penetration"][key]
            for key in ("sample_count", "success_count", "p50_ms", "p95_ms", "max_ms")
        },
        "ownership_penetration_depth_3": {
            key: result["ownership_penetration_depth_3"][key]
            for key in ("sample_count", "success_count", "p50_ms", "p95_ms", "max_ms")
        },
        "event_query": {
            key: result["event_query"][key]
            for key in ("sample_count", "success_count", "p50_ms", "p95_ms", "max_ms")
        },
        "event_cluster": {
            key: result["event_cluster"][key]
            for key in ("sample_count", "success_count", "p50_ms", "p95_ms", "max_ms")
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
