"""Deterministic, no-tool and no-LLM audit for the annotated question set."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.graph.workflow import knowledge_cutoff_from_env
from harness.routing.answerability import check_answerability, is_investigation
from harness.routing.direct_gate import build_direct_action
from harness.routing.financial_period_resolver import resolve_financial_periods
from harness.routing.planner import plan_next_action
from harness.routing.request_parser import parse_request
from schemas.agent_state import AgentState, CurrentContext, UserRequest
from tools.entity_resolver import EntityResolver


def audit_dataset(dataset: Path, *, knowledge_cutoff: str) -> dict[str, Any]:
    rows = _load_rows(dataset)
    resolver = EntityResolver()
    details: list[dict[str, Any]] = []
    contexts: dict[str, CurrentContext] = defaultdict(CurrentContext)

    for row in rows:
        session_id = str(row["session_id"])
        context = contexts[session_id]
        parsed = parse_request(
            row["question"], current_context=context, resolver=resolver,
            knowledge_cutoff=knowledge_cutoff,
        )
        parsed = resolve_financial_periods(parsed, knowledge_cutoff)
        pre = check_answerability(parsed)
        route, action = _preview_action(parsed, pre.status)

        expected_entities = sorted(set(row.get("required_entities") or []))
        parsed_entities = sorted(set(parsed.entities))
        expected_tools = list(row.get("valid_tools") or [])
        predicted_tool = (
            f"{action.tool_name}.{action.operation}"
            if action and action.action == "call_tool" else None
        )
        required_date = row.get("required_date")
        parsed_dates = _parsed_dates(parsed)
        flags = []
        if expected_entities and parsed_entities != expected_entities:
            flags.append("entity_mismatch")
        if required_date and required_date not in parsed_dates:
            flags.append("date_mismatch")
        if expected_tools and predicted_tool not in expected_tools:
            flags.append("first_tool_not_in_valid_tools")
        if not expected_tools and predicted_tool:
            flags.append("tool_predicted_without_valid_tool_label")
        if row.get("answerability") is None:
            flags.append("missing_answerability_label")

        details.append({
            "case_id": row["case_id"],
            "session_id": session_id,
            "turn_id": int(row["turn_id"]),
            "question": row["question"],
            "annotation": {
                "answerability": row.get("answerability"),
                "required_entities": expected_entities,
                "required_date": required_date,
                "valid_tools": expected_tools,
                "required_chunk_ids": list(row.get("required_chunk_ids") or []),
            },
            "offline_preview": {
                "task_family": parsed.task_family,
                "entities": parsed_entities,
                "institutions": parsed.institutions,
                "periods": parsed.periods,
                "as_of_dates": parsed.as_of_dates,
                "start_date": parsed.start_date,
                "end_date": parsed.end_date,
                "time_mode": parsed.time_mode,
                "answerability": pre.status,
                "route": route,
                "first_tool": predicted_tool,
            },
            "review_flags": flags,
        })
        _update_context(context, parsed, resolver)

    return {
        "audit_type": "deterministic_offline_preview",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_path": str(dataset.resolve()),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "knowledge_cutoff": knowledge_cutoff,
        "limitations": [
            "No tools or LLM skills were called.",
            "The first-tool preview uses the deterministic fallback planner for investigation routes.",
            "Differences from valid_tools are review candidates, not automatic Agent errors.",
            "Only two dataset rows provide required_date, so time-resolution accuracy is not estimable.",
        ],
        "summary": _summarize(details),
        "details": details,
    }


def _preview_action(parsed, pre_status: str):
    if pre_status in {"unsupported", "clarification_required"}:
        return "no_tool", None
    direct = None if is_investigation(parsed) else build_direct_action(parsed)
    if direct is not None:
        return "direct", direct
    state = AgentState(
        session_id="OFFLINE-AUDIT", user_request=UserRequest(raw_query=parsed.raw_query),
        parsed_request=parsed,
    )
    return "investigation", plan_next_action(state)


def _update_context(context: CurrentContext, parsed, resolver: EntityResolver) -> None:
    if parsed.entities:
        context.company_ids = parsed.entities[-3:]
        context.company_names = [
            name for name in (resolver.company_name(item) for item in parsed.entities[-3:]) if name
        ]
    if parsed.periods:
        context.report_periods = parsed.periods[-4:]
    if parsed.focus_topics:
        context.focus_topics = parsed.focus_topics
    if parsed.task_family != "unknown":
        context.active_topic = parsed.task_family


def _parsed_dates(parsed) -> set[str]:
    return set(parsed.periods + parsed.as_of_dates) | {
        value for value in (parsed.start_date, parsed.end_date) if value
    }


def _summarize(details: list[dict[str, Any]]) -> dict[str, Any]:
    task_families = Counter(item["offline_preview"]["task_family"] for item in details)
    routes = Counter(item["offline_preview"]["route"] for item in details)
    parsed_answerability = Counter(item["offline_preview"]["answerability"] for item in details)
    annotated_answerability = Counter(
        str(item["annotation"]["answerability"]) for item in details
    )
    predicted_tools = Counter(
        item["offline_preview"]["first_tool"] for item in details
        if item["offline_preview"]["first_tool"]
    )
    flags = Counter(flag for item in details for flag in item["review_flags"])
    entity_labeled = [item for item in details if item["annotation"]["required_entities"]]
    entity_matches = sum(
        item["offline_preview"]["entities"] == item["annotation"]["required_entities"]
        for item in entity_labeled
    )
    tool_labeled = [item for item in details if item["annotation"]["valid_tools"]]
    tool_preview_matches = sum(
        item["offline_preview"]["first_tool"] in item["annotation"]["valid_tools"]
        for item in tool_labeled
    )
    return {
        "case_count": len(details),
        "session_count": len({item["session_id"] for item in details}),
        "required_chunk_labeled_count": sum(
            bool(item["annotation"]["required_chunk_ids"]) for item in details
        ),
        "task_family_counts": dict(task_families),
        "route_counts": dict(routes),
        "annotated_answerability_counts": dict(annotated_answerability),
        "parsed_answerability_counts": dict(parsed_answerability),
        "predicted_first_tool_counts": dict(predicted_tools),
        "review_flag_counts": dict(flags),
        "entity_exact_match": {
            "matched": entity_matches,
            "labeled": len(entity_labeled),
            "rate": entity_matches / len(entity_labeled) if entity_labeled else None,
        },
        "first_tool_in_valid_tools_preview": {
            "matched": tool_preview_matches,
            "labeled": len(tool_labeled),
            "rate": tool_preview_matches / len(tool_labeled) if tool_labeled else None,
        },
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    return sorted(rows, key=lambda row: (int(row["session_id"]), int(row["turn_id"])))


def write_reports(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "offline_audit.json"
    markdown_path = output_dir / "offline_audit.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(result), encoding="utf-8")
    return json_path, markdown_path


def _markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    entity = summary["entity_exact_match"]
    tool = summary["first_tool_in_valid_tools_preview"]
    lines = [
        "# FinTrace 离线预检结果", "",
        f"- 数据集：`{result['dataset_path']}`",
        f"- 知识截止日：`{result['knowledge_cutoff']}`",
        f"- 问题数：{summary['case_count']}",
        f"- 会话数：{summary['session_count']}", "",
        "## 核心结果", "",
        f"- 有主体标注的问题：{entity['matched']}/{entity['labeled']} 完全一致（{entity['rate']:.2%}）。",
        f"- 有工具标注的问题：{tool['matched']}/{tool['labeled']} 的确定性首动作位于 `valid_tools`（{tool['rate']:.2%}）。",
        "- 首动作对照仅用于发现待复核样本，不作为正式工具调用准确率。", "",
        "## 路由分布", "",
    ]
    lines.extend(f"- `{key}`：{value}" for key, value in summary["route_counts"].items())
    lines.extend(["", "## 可回答性预览", ""])
    lines.extend(f"- `{key}`：{value}" for key, value in summary["parsed_answerability_counts"].items())
    lines.extend(["", "## 待复核标记", ""])
    lines.extend(f"- `{key}`：{value}" for key, value in summary["review_flag_counts"].items())
    lines.extend(["", "## 使用限制", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path,
        default=Path("evaluation/questions/questions_annotated_v1.jsonl"),
    )
    parser.add_argument("--knowledge-cutoff", default=knowledge_cutoff_from_env())
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/evaluation/offline_audit"))
    args = parser.parse_args()
    if not args.knowledge_cutoff:
        parser.error("--knowledge-cutoff or FINTRACE_KNOWLEDGE_CUTOFF is required")
    result = audit_dataset(args.dataset, knowledge_cutoff=args.knowledge_cutoff)
    json_path, markdown_path = write_reports(result, args.output_dir)
    print(json.dumps({
        "status": "completed", "json_path": str(json_path),
        "markdown_path": str(markdown_path), "summary": result["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
