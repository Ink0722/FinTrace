"""Strictly score the existing ownership penetration benchmark cases.

The reference graph is rebuilt independently from frozen SQLite rows.  This
module never calls ``find_holding_paths`` when constructing or scoring the
reference paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any

from evaluation.analysis.report_batch import DEFAULT_OUTPUT_ROOT
from evaluation.analysis.tool_llm_review import parse_llm_json
from harness.llm import QwenClient
from tools.ownership_analysis.config import OwnershipAnalysisConfig


BENCHMARK_FILENAME = "specialty_tool_benchmark.json"
INPUT_FILENAME = "ownership_strict_inputs.jsonl"
REVIEW_FILENAME = "ownership_strict_review.jsonl"
SCORES_FILENAME = "ownership_strict_scores.csv"
SUMMARY_FILENAME = "ownership_strict_summary.json"
PROMPT_VERSION = "ownership-strict-review-v1"
RATIO_TOLERANCE = 1e-8
MAX_PARSE_RETRIES = 2

GROUP_SPECS = (
    ("depth_3", "ownership_penetration_depth_3", "OWN-STRICT-D3"),
    ("depth_gt_3", "ownership_penetration", "OWN-STRICT-DGT3"),
)

SCORE_COLUMNS = (
    "case_id",
    "depth_group",
    "source_entity_id",
    "target_entity_id",
    "expected_depth",
    "tool_success",
    "path_count_correct",
    "nodes_correct",
    "directions_correct",
    "edge_ratios_correct",
    "path_ratios_correct",
    "dates_correct",
    "evidence_correct",
    "deterministic_pass",
    "llm_review_pass",
    "strict_pass",
    "failure_reasons",
    "review_reason",
)

SYSTEM_PROMPT = """你是FinTrace股权穿透工具的严格复核员。请以JSON对象输出结论。

输入只包含冻结股东快照生成的参考路径、工具实际路径、主体桥接信息和确定性比较结果。不得使用互联网或模型记忆补充关系。

你只复核以下语义问题：
1. 路径中的公司名称与实体ID是否一致，是否指向同一法律主体；
2. 是否把直接持股、间接持股或控制关系混淆；
3. 路径关系是否均有输入中的股东记录和桥接记录支持；
4. 是否存在输入证据不支持的额外关系。

确定性比较已经发现的节点、方向、日期、证据或比例错误不得被改判为正确。不要评价现实世界中未被前十大股东快照覆盖的关系。

输出JSON格式：
{
  "case_id": "...",
  "entity_identity_consistent": true,
  "relation_semantics_correct": true,
  "source_support_consistent": true,
  "no_unsupported_relations": true,
  "llm_review_pass": true,
  "confidence": 0.0,
  "reason": "一句可审计说明"
}
"""


def prepare_inputs(batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    output_dir = output_root / batch_id
    benchmark_path = output_dir / BENCHMARK_FILENAME
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    cutoff = str(benchmark.get("knowledge_cutoff") or "")
    if not cutoff:
        raise ValueError("specialty benchmark has no knowledge_cutoff")

    config = OwnershipAnalysisConfig.from_env()
    graph, node_names = load_reference_graph(config.index_path, cutoff=cutoff)
    review_inputs: list[dict[str, Any]] = []
    for group, benchmark_key, prefix in GROUP_SPECS:
        cases = benchmark.get(benchmark_key, {}).get("cases") or []
        for sequence, case in enumerate(cases, start=1):
            actual_paths = case.get("returned_paths")
            if not isinstance(actual_paths, list):
                raise ValueError(
                    f"{benchmark_key} case {sequence} has no returned_paths; rerun "
                    "evaluation.analysis.specialty_tool_benchmark first"
                )
            reference_paths = enumerate_reference_paths(
                graph,
                node_names,
                source_entity_id=str(case["source_entity_id"]),
                target_entity_id=str(case["target_entity_id"]),
                max_depth=int(case["expected_depth"]),
            )
            deterministic = score_paths(case, reference_paths, actual_paths)
            review_inputs.append(
                {
                    "case_id": f"{prefix}-{sequence:03d}",
                    "depth_group": group,
                    "source_entity_id": case["source_entity_id"],
                    "target_entity_id": case["target_entity_id"],
                    "expected_depth": case["expected_depth"],
                    "knowledge_cutoff": cutoff,
                    "reference_paths": reference_paths,
                    "actual_paths": actual_paths,
                    "deterministic_score": deterministic,
                }
            )

    input_path = output_dir / INPUT_FILENAME
    _write_jsonl(input_path, review_inputs)
    return {
        "batch_id": batch_id,
        "case_count": len(review_inputs),
        "group_counts": _group_counts(review_inputs),
        "deterministic_pass_count": sum(
            bool(item["deterministic_score"]["deterministic_pass"])
            for item in review_inputs
        ),
        "input_path": str(input_path),
    }


def load_reference_graph(
    index_path: Path, *, cutoff: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Build effective company-to-company edges directly from frozen rows."""
    graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, str] = {}
    sql = """
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
        SELECT link.company_id AS source_entity_id,
               source.canonical_name AS source_name,
               record.target_company_id AS target_entity_id,
               target.canonical_name AS target_name,
               record.record_id,
               record.holder_name,
               record.holding_ratio,
               record.holder_end_date,
               record.announcement_date,
               record.evidence_id,
               link.match_method
        FROM holder_records record
        JOIN effective snapshot
          ON record.target_company_id = snapshot.target_company_id
         AND record.holder_end_date = snapshot.holder_end_date
         AND record.announcement_date = snapshot.announcement_date
        JOIN holder_company_links link
          ON link.holder_entity_id = record.holder_entity_id
        LEFT JOIN listed_company_entities source
          ON source.company_id = link.company_id
        LEFT JOIN listed_company_entities target
          ON target.company_id = record.target_company_id
        ORDER BY link.company_id, record.target_company_id, record.record_id
    """
    with sqlite3.connect(index_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(sql, (cutoff, cutoff)).fetchall()
    for row in rows:
        source_id = str(row["source_entity_id"])
        target_id = str(row["target_entity_id"])
        source_name = str(row["source_name"] or source_id)
        target_name = str(row["target_name"] or target_id)
        names[source_id] = source_name
        names[target_id] = target_name
        graph[source_id].append(
            {
                "edge_id": str(row["record_id"]),
                "source_entity_id": source_id,
                "source_name": source_name,
                "target_entity_id": target_id,
                "target_name": target_name,
                "relation_type": "OWNS",
                "holding_ratio": float(row["holding_ratio"]),
                "holder_end_date": str(row["holder_end_date"]),
                "announcement_date": str(row["announcement_date"]),
                "evidence_id": str(row["evidence_id"]),
                "holder_name": str(row["holder_name"]),
                "bridge_match_method": str(row["match_method"]),
            }
        )
    return dict(graph), names


def enumerate_reference_paths(
    graph: dict[str, list[dict[str, Any]]],
    node_names: dict[str, str],
    *,
    source_entity_id: str,
    target_entity_id: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    """Enumerate every simple path within the same explicit depth bound."""
    paths: list[dict[str, Any]] = []

    def visit(node: str, nodes: list[str], edges: list[dict[str, Any]]) -> None:
        if len(edges) >= max_depth:
            return
        for edge in graph.get(node, []):
            target = edge["target_entity_id"]
            if target in nodes:
                continue
            next_nodes = [*nodes, target]
            next_edges = [*edges, edge]
            if target == target_entity_id:
                ratio = Decimal("1")
                for item in next_edges:
                    ratio *= Decimal(str(item["holding_ratio"]))
                paths.append(
                    {
                        "depth": len(next_edges),
                        "nodes": [
                            {"entity_id": item, "name": node_names.get(item, item)}
                            for item in next_nodes
                        ],
                        "edges": next_edges,
                        "path_ratio": float(ratio),
                        "evidence_ids": [item["evidence_id"] for item in next_edges],
                    }
                )
                continue
            visit(target, next_nodes, next_edges)

    visit(source_entity_id, [source_entity_id], [])
    return sorted(paths, key=_path_sort_key)


def score_paths(
    case: dict[str, Any],
    reference_paths: list[dict[str, Any]],
    actual_paths: list[dict[str, Any]],
) -> dict[str, Any]:
    references = sorted(reference_paths, key=_path_sort_key)
    actual = sorted(actual_paths, key=_path_sort_key)
    tool_success = str(case.get("status")) == "success"
    path_count_correct = len(references) == len(actual)
    expected_depth = int(case["expected_depth"])
    depth_correct = path_count_correct and all(
        int(item.get("depth", -1)) == expected_depth for item in actual
    )
    nodes_correct = path_count_correct and [
        _node_ids(item) for item in references
    ] == [_node_ids(item) for item in actual]
    directions_correct = nodes_correct and all(
        _edge_directions(ref) == _edge_directions(got)
        for ref, got in zip(references, actual)
    )
    edge_ratios_correct = nodes_correct and all(
        _float_sequences_close(_edge_ratios(ref), _edge_ratios(got))
        for ref, got in zip(references, actual)
    )
    path_ratios_correct = nodes_correct and all(
        _close(ref.get("path_ratio"), got.get("path_ratio"))
        for ref, got in zip(references, actual)
    )
    dates_correct = nodes_correct and all(
        _edge_dates(ref) == _edge_dates(got)
        for ref, got in zip(references, actual)
    )
    evidence_correct = nodes_correct and all(
        _evidence_ids(ref) == _evidence_ids(got)
        for ref, got in zip(references, actual)
    )
    checks = {
        "tool_success": tool_success,
        "path_count_correct": path_count_correct,
        "depth_correct": depth_correct,
        "nodes_correct": nodes_correct,
        "directions_correct": directions_correct,
        "edge_ratios_correct": edge_ratios_correct,
        "path_ratios_correct": path_ratios_correct,
        "dates_correct": dates_correct,
        "evidence_correct": evidence_correct,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {**checks, "deterministic_pass": not failures, "failure_reasons": failures}


def run_llm_review(
    batch_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    limit: int | None = None,
    concurrency: int = 3,
) -> dict[str, Any]:
    output_dir = output_root / batch_id
    inputs = _read_jsonl(output_dir / INPUT_FILENAME)
    output_path = output_dir / REVIEW_FILENAME
    existing = _deduplicate_review_file(output_path)
    completed = {item["case_id"] for item in existing}
    pending = [item for item in inputs if item["case_id"] not in completed]
    if limit is not None:
        pending = pending[:limit]
    if not _evaluator_client().enabled:
        raise RuntimeError("QWEN_EVALUATOR_API_KEY or QWEN_API_KEY is not configured")
    print(
        f"batch={batch_id} total={len(inputs)} done={len(completed)} pending={len(pending)}",
        file=sys.stderr,
    )
    processed = 0
    failures = 0
    with output_path.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(
        max_workers=concurrency
    ) as pool:
        futures = {pool.submit(_judge_case, item): item["case_id"] for item in pending}
        for future in as_completed(futures):
            record = future.result()
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            processed += 1
            if record.get("evaluation_error"):
                failures += 1
            if processed % 10 == 0:
                print(
                    f"progress={processed}/{len(pending)} failures={failures}",
                    file=sys.stderr,
                )
    return {"evaluated": processed, "failures": failures, "remaining": len(pending) - processed}


def aggregate_results(batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    output_dir = output_root / batch_id
    inputs = _read_jsonl(output_dir / INPUT_FILENAME)
    reviews = _deduplicate_review_file(output_dir / REVIEW_FILENAME)
    review_by_id = {item["case_id"]: item for item in reviews}
    if len(review_by_id) != len(reviews):
        raise ValueError("ownership strict review contains duplicate case_id values")
    missing = [item["case_id"] for item in inputs if item["case_id"] not in review_by_id]
    if missing:
        raise ValueError(f"ownership strict review is incomplete; missing {missing[:5]}")

    score_rows: list[dict[str, Any]] = []
    for item in inputs:
        deterministic = item["deterministic_score"]
        review = review_by_id[item["case_id"]]
        llm_pass = bool(review.get("llm_review_pass")) and not review.get("evaluation_error")
        strict_pass = bool(deterministic["deterministic_pass"]) and llm_pass
        score_rows.append(
            {
                "case_id": item["case_id"],
                "depth_group": item["depth_group"],
                "source_entity_id": item["source_entity_id"],
                "target_entity_id": item["target_entity_id"],
                "expected_depth": item["expected_depth"],
                "tool_success": _yes_no(deterministic["tool_success"]),
                "path_count_correct": _yes_no(deterministic["path_count_correct"]),
                "nodes_correct": _yes_no(deterministic["nodes_correct"]),
                "directions_correct": _yes_no(deterministic["directions_correct"]),
                "edge_ratios_correct": _yes_no(deterministic["edge_ratios_correct"]),
                "path_ratios_correct": _yes_no(deterministic["path_ratios_correct"]),
                "dates_correct": _yes_no(deterministic["dates_correct"]),
                "evidence_correct": _yes_no(deterministic["evidence_correct"]),
                "deterministic_pass": _yes_no(deterministic["deterministic_pass"]),
                "llm_review_pass": _yes_no(llm_pass),
                "strict_pass": _yes_no(strict_pass),
                "failure_reasons": json.dumps(
                    deterministic.get("failure_reasons") or [], ensure_ascii=False
                ),
                "review_reason": str(review.get("reason") or ""),
            }
        )

    with (output_dir / SCORES_FILENAME).open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_COLUMNS)
        writer.writeheader()
        writer.writerows(score_rows)

    groups: dict[str, dict[str, Any]] = {}
    for group, _, _ in GROUP_SPECS:
        rows = [item for item in score_rows if item["depth_group"] == group]
        passed = sum(item["strict_pass"] == "yes" for item in rows)
        groups[group] = {
            "sample_count": len(rows),
            "strict_pass_count": passed,
            "strict_accuracy": round(passed / len(rows), 6) if rows else None,
            "deterministic_pass_count": sum(
                item["deterministic_pass"] == "yes" for item in rows
            ),
            "llm_review_pass_count": sum(item["llm_review_pass"] == "yes" for item in rows),
        }
    summary = {
        "batch_id": batch_id,
        "prompt_version": PROMPT_VERSION,
        "evaluation_model": _evaluator_client().model,
        "case_source": BENCHMARK_FILENAME,
        "case_count": len(score_rows),
        "groups": groups,
        "validation": {
            "complete": len(score_rows) == len(inputs) == 60,
            "missing_reviews": missing,
            "evaluation_errors": sum(bool(item.get("evaluation_error")) for item in reviews),
        },
        "provenance": {
            "benchmark_sha256": _sha256(output_dir / BENCHMARK_FILENAME),
            "inputs_sha256": _sha256(output_dir / INPUT_FILENAME),
            "reviews_sha256": _sha256(output_dir / REVIEW_FILENAME),
            "reference": "independent SQL effective snapshots plus DFS; does not call find_holding_paths",
        },
    }
    (output_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _judge_case(case_input: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "请复核以下股权穿透案例并输出JSON：\n"
            + json.dumps(case_input, ensure_ascii=False),
        },
    ]
    last_error = ""
    for attempt in range(MAX_PARSE_RETRIES + 1):
        try:
            payload = _evaluator_client().chat_json(messages, temperature=0.0)
            choices = payload.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content", "")
            return _normalise_review(parse_llm_json(text), case_input)
        except Exception as exc:  # noqa: BLE001 - API and response failures are retried
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_PARSE_RETRIES:
                time.sleep(2)
    return {
        "case_id": case_input["case_id"],
        "entity_identity_consistent": False,
        "relation_semantics_correct": False,
        "source_support_consistent": False,
        "no_unsupported_relations": False,
        "llm_review_pass": False,
        "confidence": 0.0,
        "reason": "评审调用失败，不能形成严格通过结论。",
        "evaluation_error": last_error,
    }


def _normalise_review(record: dict[str, Any], case_input: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "entity_identity_consistent": bool(record.get("entity_identity_consistent")),
        "relation_semantics_correct": bool(record.get("relation_semantics_correct")),
        "source_support_consistent": bool(record.get("source_support_consistent")),
        "no_unsupported_relations": bool(record.get("no_unsupported_relations")),
    }
    llm_pass = all(checks.values()) and bool(record.get("llm_review_pass"))
    try:
        confidence = min(1.0, max(0.0, float(record.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "case_id": case_input["case_id"],
        **checks,
        "llm_review_pass": llm_pass,
        "confidence": round(confidence, 2),
        "reason": str(record.get("reason") or "")[:240],
    }


def _evaluator_client() -> QwenClient:
    max_tokens_raw = os.getenv("QWEN_EVALUATOR_MAX_OUTPUT_TOKENS")
    max_tokens = int(max_tokens_raw) if max_tokens_raw else 2048
    return QwenClient(
        api_key=os.getenv("QWEN_EVALUATOR_API_KEY") or None,
        base_url=os.getenv("QWEN_EVALUATOR_BASE_URL") or None,
        model=os.getenv("QWEN_EVALUATOR_MODEL") or None,
        max_output_tokens=max_tokens,
    )


def _path_sort_key(path: dict[str, Any]) -> tuple[Any, ...]:
    return (_node_ids(path), tuple(str(item.get("edge_id")) for item in path.get("edges") or []))


def _node_ids(path: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item.get("entity_id")) for item in path.get("nodes") or [])


def _edge_directions(path: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(item.get("source_entity_id")), str(item.get("target_entity_id")))
        for item in path.get("edges") or []
    )


def _edge_ratios(path: dict[str, Any]) -> tuple[float, ...]:
    return tuple(float(item.get("holding_ratio")) for item in path.get("edges") or [])


def _edge_dates(path: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(item.get("holder_end_date")), str(item.get("announcement_date")))
        for item in path.get("edges") or []
    )


def _evidence_ids(path: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item.get("evidence_id")) for item in path.get("edges") or [])


def _float_sequences_close(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return len(left) == len(right) and all(_close(a, b) for a, b in zip(left, right))


def _close(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=RATIO_TOLERANCE)
    except (TypeError, ValueError):
        return False


def _group_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        group: sum(item["depth_group"] == group for item in items)
        for group, _, _ in GROUP_SPECS
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _deduplicate_review_file(path: Path) -> list[dict[str, Any]]:
    records = _read_jsonl(path)
    if not records:
        return records
    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        case_id = str(record.get("case_id") or "")
        if not case_id:
            continue
        if case_id not in by_id:
            order.append(case_id)
            by_id[case_id] = record
            continue
        if by_id[case_id].get("evaluation_error") and not record.get("evaluation_error"):
            by_id[case_id] = record
    deduplicated = [by_id[case_id] for case_id in order]
    if len(deduplicated) != len(records):
        temporary = path.with_suffix(path.suffix + ".tmp")
        _write_jsonl(temporary, deduplicated)
        temporary.replace(path)
    return deduplicated


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
    parser.add_argument("--stage", choices=("prepare", "judge", "aggregate", "all"), required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    if args.stage in {"prepare", "all"}:
        print(json.dumps(prepare_inputs(args.batch_id, args.output_root), ensure_ascii=False, indent=2))
    if args.stage in {"judge", "all"}:
        print(
            json.dumps(
                run_llm_review(
                    args.batch_id,
                    args.output_root,
                    limit=args.limit,
                    concurrency=args.concurrency,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    if args.stage in {"aggregate", "all"}:
        print(json.dumps(aggregate_results(args.batch_id, args.output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
