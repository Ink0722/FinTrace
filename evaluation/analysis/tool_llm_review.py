"""LLM-based tool-call quality review for a completed evaluation batch.

Reads the runtime database in read-only mode, builds one review input per
case (question + resolved context + actual tool calls, never the final
answer), asks a frozen LLM evaluator to judge every call, and writes:

- ``tool_llm_review.jsonl``          one line per case, resumable by case_id
- ``tool_llm_review_conflicts.csv``  low confidence / annotation conflicts / missing calls / multi-plan
- ``tool_llm_review_summary.json``   counts, precision, conflicts, per-tool stats, provenance

The agent itself is never modified. See docs/10 §5.2 for the metric
definitions behind ``final_call_correct``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from harness.llm import QwenClient
from harness.runtime_db import PROJECT_ROOT
from harness.tracing.store import connect

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "evaluation" / "results"
PROMPT_VERSION = "tool-llm-review-v4"
KNOWLEDGE_CUTOFF_DEFAULT = "2026-05-28"
MAX_PARSE_RETRIES = 2
PROGRESS_EVERY = 25

SYSTEM_PROMPT = """你是金融智能体工具调用质量的独立评审员。你评审的对象是FinTrace智能体在一个多轮会话轮次中实际发起的工具调用，不评审最终答案文风。

## 系统能力目录（当前代码真实实现，唯一合法词表）
1. financial_analysis.metric_query —— 查询指定公司、报告期、标准指标的原始值。指标仅限：TOTAL_ASSETS, TOTAL_LIABILITIES, CURRENT_ASSETS, CURRENT_LIABILITIES, INVENTORY, ACCOUNTS_RECEIVABLE, MONETARY_CAPITAL, REVENUE, OPERATING_COST, NET_PROFIT_PARENT, OPERATING_PROFIT, R_AND_D_EXPENSE, OPERATING_CASHFLOW, CASH_RECEIVED_FROM_SALES。期间仅限报告期截止日（如2024-12-31、2025-06-30）。metric_query允许一次传入多公司、多期间、多指标批量查询，这不是超范围。
2. financial_analysis.metric_compare —— 确定性比较：恰好1公司×至少2期间（跨期），或至少2公司×恰好1期间（跨公司）。不允许多公司×多期间。
3. financial_analysis.risk_scan —— 版本化财务风险规则扫描（现金流利润背离、应收/存货与收入背离、流动性、杠杆、利润率波动、经营现金流持续为负、销售收现背离）。用户未指定期间时由工具解析全部可用年度，参数须与期间解析结果一致。
4. ownership_analysis.holding_query —— 前十大股东快照（正向按公司查股东、反向按股东查持股公司）、集中度。可选as_of_date。
5. ownership_analysis.holding_compare —— 恰好1个公司在两个观察时点之间的股东进入/退出/增减持。
6. ownership_analysis.penetration —— 现有快照范围内source到target的有界持股路径穿透，需as_of_date。
7. document_search.search —— 公告正文与研报摘要的混合检索（document_types: announcement / research_report），按公司、日期、类型过滤。同一轮最多两次实质不同的文档检索。
8. event_timeline.event_query —— 结构化事件筛选排序。事件类型仅限：regulatory_inquiry, regulatory_penalty, audit_opinion, controller_change, share_pledge, financial_restated, major_litigation, risk_warning。
9. event_timeline.event_cluster —— 相关事件聚合为事件簇。
10. research_analysis.view_query —— 机构观点、评级、盈利预测、风险提示查询（"机构怎么看/评级/目标价/盈利预测"用它）。

## 系统边界（超出边界的诉求不应调用任何工具）
- 实时或历史行情、涨跌幅、分时、盘口、资金流、龙虎榜、板块排行、龙头股排行；
- 用户自选股、个人持仓、账户资产；
- 完整基金持仓数据库、当前数据集之外的互联网事实；
- 对未来价格的预测判断。
注意：行情类问题即使文档库偶然命中相关词句，也不构成调用理由。通用金融概念解释（不依赖具体数据）无需调用工具。

## 复合问题的处理
一部分在边界内、一部分在边界外的问题（如"XX表现如何+明天走势预测"、"最新公告+当前股价"）：answerability_assessment=partially_answerable；边界内部分仍应调用相应工具，工具调用的call_needed按边界内诉求判断（parameters_correct照常检查）；仅当问题核心诉求完全在边界外、边界内部分无实质信息价值时，才整体判unanswerable且不调用工具。

## 派生比率类财务问题的处理
存货周转天数、总资产报酬率、毛利率、资产负债率、流动比率、复合增长率等派生比率：系统工具提供原始指标（如INVENTORY、OPERATING_COST、NET_PROFIT_PARENT、TOTAL_ASSETS），比率由答案层基于工具返回值计算，这类问题属于边界内（answerable或partially_answerable）。合理方案是metric_query/metric_compare取齐所需原始指标（跨公司比较可用metric_compare或多公司metric_query）；实际调用未取齐所需指标时记parameters_correct=false或列入missing_required_calls。只有当所需原始数据不在指标目录且无法由现有指标推出（如市盈率、市净率、股息率需要股价）时才判边界外。

## 判断规则
- 以整个轮次为单位：同一轮全部调用一起判断，结合会话上下文消解指代。
- 工具返回空结果或执行失败不代表选择错误；工具成功也不代表选择正确。只依据问题意图与参数匹配度判断。
- call_needed：该调用对回答本问题是否必要（边界外问题的调用一律false）。call_needed只依据问题诉求与工具领域的匹配性判断，与参数填写是否正确、结果是否为空无关：问题确实需要该工具而参数填错时，call_needed仍为true，仅在parameters_correct上记false。
- tool_correct / operation_correct：对照目录判断领域与operation是否正确。
- parameters_correct：检查主体（公司/人物/方向）、日期与报告期、时间窗口、知识截止日（2026-05-28，不得传入晚于它的end_date窗口语义）、指标代码、事件类型、文档类型、查询方向与筛选条件。对risk_scan，用户问"最近/最新"而参数用全部可用年度属于工具规定行为，不算错；但用户明确指定年份而参数用其他年份算错。
- redundant：与同轮或同会话已获得信息重复、或超出问题需要的过度调用。
- final_call_correct = 前四项全true且redundant=false。
- reason_codes使用以下固定词表（可多个）：OK, NOT_NEEDED_OUT_OF_SCOPE, NOT_NEEDED_NO_TOOL, NOT_NEEDED_INFO_ALREADY_AVAILABLE, WRONG_TOOL, WRONG_OPERATION, WRONG_ENTITY, WRONG_PERIOD, WRONG_METRIC, WRONG_EVENT_TYPE, WRONG_DOC_TYPE, WRONG_DIRECTION, WRONG_QUERY_TEXT, MISSING_PARAM, OVERSCOPE, REDUNDANT_DUPLICATE, REDUNDANT_OVERFETCH, PARTIALLY_CORRECT_PARAMS。
- answerability_assessment：answerable / partially_answerable / unanswerable / ambiguous。以系统数据边界为准，而不是以智能体是否回答为准。
- acceptable_tool_plans：给出修订后的合理调用方案（每方案是tool.operation列表，按调用顺序）。只有一种合理方案时也输出单元素列表；该轮无需任何工具时输出空方案[]。存在多种合理方案时分别列出。
- missing_required_calls：该轮应调用但未调用的tool.operation列表。
- suggested_valid_tools：修订后该轮的合理工具集合（可接受方案并集，无需调用时为[]）。
- turn_tool_route_correct：该轮整体路由是否正确（实际调用全部final_call_correct为true且无missing_required_calls时为true；无调用且无需调用也为true）。
- confidence：本case判断的置信度0~1。低于0.80时必须needs_human_review=true并填写human_review_reason。
- reason字段不超过80字，只写可审计的判断依据，不写思维链。

## 输出格式
只输出一个JSON对象，不要输出任何其他文字或代码块标记：
{
  "case_id": "...",
  "intent_summary": "不超过30字的意图概括",
  "answerability_assessment": "answerable|partially_answerable|unanswerable|ambiguous",
  "acceptable_tool_plans": [["tool.operation"]],
  "calls": [{"call_sequence": 1, "call_needed": true, "tool_correct": true, "operation_correct": true, "parameters_correct": true, "redundant": false, "final_call_correct": true, "confidence": 0.95, "reason_codes": ["OK"], "reason": "不超过80字"}],
  "missing_required_calls": [],
  "suggested_valid_tools": [],
  "turn_tool_route_correct": true,
  "needs_human_review": false,
  "human_review_reason": ""
}"""


# ---------------------------------------------------------------- extraction

PARSED_FIELDS = (
    "entities", "people", "periods", "requested_periods", "target_period",
    "period_type", "as_of_dates", "start_date", "end_date", "time_mode",
    "task_family", "metrics", "focus_topics", "document_types", "event_types",
    "research_claim_types", "institutions", "comparison_type",
    "requires_explanation", "requires_investigation", "requires_realtime",
    "requires_prediction", "capability_gaps", "missing_slots",
)


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_review_inputs(batch_id: str) -> list[dict[str, Any]]:
    """Build one compact review input per case, read-only, no final answers."""
    connection = connect(readonly=True)
    connection.row_factory = sqlite_row_factory
    try:
        cases = connection.execute(
            "SELECT case_id, source_session_id, agent_session_id, expected_turn_id, "
            "question, annotation_json, run_id FROM evaluation_cases "
            "WHERE batch_id = ? ORDER BY agent_session_id, expected_turn_id",
            (batch_id,),
        ).fetchall()
        review_inputs: list[dict[str, Any]] = []
        session_questions: dict[str, list[str]] = {}
        for case in cases:
            run = connection.execute(
                "SELECT parsed_request_json, current_context_json, routing_mode, "
                "answer_status FROM agent_runs WHERE run_id = ?",
                (case["run_id"],),
            ).fetchone()
            calls = connection.execute(
                "SELECT sequence, tool_name, operation, status, reason, arguments_json "
                "FROM tool_executions WHERE run_id = ? ORDER BY sequence",
                (case["run_id"],),
            ).fetchall()
            session_key = case["agent_session_id"]
            recent = session_questions.get(session_key, [])[-3:]
            annotation = _loads(case["annotation_json"], {})
            parsed = _loads(run["parsed_request_json"] if run else None, {})
            review_inputs.append(
                {
                    "case_id": case["case_id"],
                    "session": case["source_session_id"],
                    "turn": case["expected_turn_id"],
                    "question": case["question"],
                    "recent_prior_questions": recent,
                    "resolved_context": _loads(
                        run["current_context_json"] if run else None, {}
                    ),
                    "reference_annotation": {
                        "answerability": annotation.get("answerability"),
                        "valid_tools": annotation.get("valid_tools") or [],
                        "note": "历史参考标注，可能有遗漏/错误/旧词表，仅供参考，不是正确答案",
                    },
                    "agent_parsed_request": {
                        field: parsed.get(field) for field in PARSED_FIELDS
                    },
                    "tool_calls": [
                        {
                            "call_sequence": call["sequence"],
                            "tool": call["tool_name"],
                            "operation": call["operation"],
                            "status": call["status"],
                            "reason": call["reason"],
                            "arguments": _loads(call["arguments_json"], {}),
                        }
                        for call in calls
                    ],
                }
            )
            session_questions.setdefault(session_key, []).append(case["question"])
        return review_inputs
    finally:
        connection.close()


def sqlite_row_factory(cursor, row):
    return {desc[0]: row[idx] for idx, desc in enumerate(cursor.description)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- evaluation

JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_llm_json(text: str) -> dict[str, Any]:
    """Extract the evaluator JSON object; raises ValueError when absent."""
    match = JSON_BLOCK.search(text)
    candidate = match.group(0) if match else text
    return json.loads(candidate)


def normalise_record(record: dict[str, Any], case_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and fill the LLM record so every line matches the schema."""
    case_id = case_input["case_id"]
    if record.get("case_id") != case_id:
        record["case_id"] = case_id
    expected_sequences = {call["call_sequence"] for call in case_input["tool_calls"]}
    calls_in = record.get("calls") or []
    calls_out = []
    for call in calls_in:
        try:
            sequence = int(call.get("call_sequence"))
        except (TypeError, ValueError):
            continue
        if sequence not in expected_sequences:
            continue  # drop hallucinated calls that were never executed
        flags = {
            "call_needed": bool(call.get("call_needed", False)),
            "tool_correct": bool(call.get("tool_correct", False)),
            "operation_correct": bool(call.get("operation_correct", False)),
            "parameters_correct": bool(call.get("parameters_correct", False)),
            "redundant": bool(call.get("redundant", False)),
        }
        final = (
            flags["call_needed"]
            and flags["tool_correct"]
            and flags["operation_correct"]
            and flags["parameters_correct"]
            and not flags["redundant"]
        )
        try:
            confidence = round(min(1.0, max(0.0, float(call.get("confidence", 0.5)))), 2)
        except (TypeError, ValueError):
            confidence = 0.5
        codes = call.get("reason_codes") or []
        if isinstance(codes, str):
            codes = [codes]
        calls_out.append(
            {
                "call_sequence": sequence,
                **flags,
                "final_call_correct": bool(call.get("final_call_correct", final)) and final,
                "confidence": confidence,
                "reason_codes": [str(code) for code in codes][:6],
                "reason": str(call.get("reason", ""))[:160],
            }
        )
    answerability = record.get("answerability_assessment", "ambiguous")
    if answerability not in {"answerable", "partially_answerable", "unanswerable", "ambiguous"}:
        answerability = "ambiguous"
    plans = record.get("acceptable_tool_plans") or []
    normalised_plans = []
    for plan in plans:
        if isinstance(plan, list):
            normalised_plans.append([str(item) for item in plan])
    missing = [str(item) for item in (record.get("missing_required_calls") or [])]
    suggested = [str(item) for item in (record.get("suggested_valid_tools") or [])]
    case_confidence = _case_confidence(calls_out)
    needs_review = bool(record.get("needs_human_review")) or case_confidence < 0.80
    review_reason = str(record.get("human_review_reason", "") or "")
    if case_confidence < 0.80 and "置信度低于0.80" not in review_reason:
        review_reason = (review_reason + "；" if review_reason else "") + "置信度低于0.80"
    return {
        "case_id": case_id,
        "intent_summary": str(record.get("intent_summary", ""))[:80],
        "answerability_assessment": answerability,
        "acceptable_tool_plans": normalised_plans,
        "calls": calls_out,
        "missing_required_calls": missing,
        "suggested_valid_tools": suggested,
        "turn_tool_route_correct": bool(record.get("turn_tool_route_correct", False)),
        "needs_human_review": needs_review,
        "human_review_reason": review_reason[:200],
    }


def _case_confidence(calls: list[dict[str, Any]]) -> float:
    """Case-level confidence: minimum over call confidences, 1.0 when no calls."""
    return min((call["confidence"] for call in calls), default=1.0)


def evaluate_case(
    client: QwenClient,
    case_input: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Ask the evaluator once; returns (record, status)."""
    user_payload = json.dumps(case_input, ensure_ascii=False)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"知识截止日：{KNOWLEDGE_CUTOFF_DEFAULT}。"
                f"请评审以下轮次（含同轮全部工具调用）：\n{user_payload}"
            ),
        },
    ]
    last_error = ""
    for _ in range(MAX_PARSE_RETRIES + 1):
        try:
            reply = client.chat_json(messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - network/API failure, retry
            last_error = f"API_ERROR:{type(exc).__name__}"
            time.sleep(3)
            continue
        text = reply
        if isinstance(reply, dict):
            choices = reply.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content", "")
        try:
            record = parse_llm_json(text)
            return normalise_record(record, case_input), "ok"
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"PARSE_ERROR:{exc}"
            continue
    placeholder = normalise_record({}, case_input)
    placeholder.update(
        {
            "intent_summary": "评审模型输出解析失败",
            "needs_human_review": True,
            "human_review_reason": f"{last_error}，需人工评审",
            "_eval_error": last_error,
        }
    )
    return placeholder, "parse_failed"


def run_evaluation(
    batch_id: str,
    output_dir: Path,
    limit: int | None = None,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Evaluate every case not yet present in the output JSONL."""
    inputs = load_review_inputs(batch_id)
    input_path = output_dir / "tool_llm_review_input.jsonl"
    with open(input_path, "w", encoding="utf-8") as handle:
        for case_input in inputs:
            handle.write(json.dumps(case_input, ensure_ascii=False) + "\n")

    output_path = output_dir / "tool_llm_review.jsonl"
    done: set[str] = set()
    if output_path.exists():
        with open(output_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["case_id"])
                except (ValueError, KeyError):
                    continue
    pending = [item for item in inputs if item["case_id"] not in done]
    if limit is not None:
        pending = pending[:limit]
    print(
        f"batch={batch_id} total={len(inputs)} done={len(done)} pending={len(pending)}",
        file=sys.stderr,
    )

    client = QwenClient()
    if not client.enabled:
        raise SystemExit("QWEN/DASHSCOPE API key not configured; cannot run LLM review")

    started = time.time()
    processed = 0
    failures = 0
    with open(output_path, "a", encoding="utf-8") as handle, ThreadPoolExecutor(
        max_workers=concurrency
    ) as pool:
        futures = {
            pool.submit(evaluate_case, client, case_input): case_input["case_id"]
            for case_input in pending
        }
        for future in as_completed(futures):
            record, status = future.result()
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            processed += 1
            if status != "ok":
                failures += 1
            if processed % PROGRESS_EVERY == 0:
                elapsed = time.time() - started
                print(
                    f"progress {processed}/{len(pending)} elapsed={elapsed:.0f}s "
                    f"failures={failures}",
                    file=sys.stderr,
                )
    return {
        "evaluated": processed,
        "parse_failures": failures,
        "elapsed_seconds": round(time.time() - started, 1),
        "input_path": str(input_path),
        "output_path": str(output_path),
    }


# ---------------------------------------------------------------- aggregation

CONFLICT_COLUMNS = (
    "case_id",
    "conflict_type",
    "detail",
    "llm_confidence",
    "llm_answerability",
    "annotated_answerability",
    "llm_suggested_tools",
    "annotated_valid_tools",
    "actual_calls",
    "missing_required_calls",
    "needs_human_review",
)


def collect_conflicts(records: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case = {item["case_id"]: item for item in inputs}
    rows: list[dict[str, Any]] = []
    for record in records:
        case_input = by_case.get(record["case_id"])
        if case_input is None:
            continue
        annotation = case_input["reference_annotation"]
        annotated_tools = set(annotation["valid_tools"])
        suggested = set(record.get("suggested_valid_tools") or [])
        actual = [
            f"{call['tool']}.{call['operation']}"
            for call in case_input["tool_calls"]
        ]
        confidence = _case_confidence(record["calls"])
        conflicts: list[tuple[str, str]] = []
        if record.get("needs_human_review"):
            conflicts.append(("low_confidence_or_flagged", record.get("human_review_reason", "")))
        if record["answerability_assessment"] != (annotation["answerability"] or ""):
            conflicts.append(
                (
                    "answerability_mismatch",
                    f"llm={record['answerability_assessment']} annotated={annotation['answerability']}",
                )
            )
        if annotated_tools and annotated_tools != suggested and not (
            annotated_tools <= suggested
        ):
            conflicts.append(
                (
                    "annotation_tools_conflict",
                    f"annotated_only={sorted(annotated_tools - suggested)} "
                    f"llm_only={sorted(suggested - annotated_tools)}",
                )
            )
        if record.get("missing_required_calls"):
            conflicts.append(
                ("missing_required_calls", ",".join(record["missing_required_calls"]))
            )
        if len(record.get("acceptable_tool_plans") or []) > 1:
            conflicts.append(
                ("multiple_acceptable_plans", json.dumps(record["acceptable_tool_plans"], ensure_ascii=False))
            )
        for call in record["calls"]:
            if not call["final_call_correct"]:
                conflicts.append(
                    (
                        "incorrect_call",
                        f"seq={call['call_sequence']} {'/'.join(call['reason_codes'])} {call['reason']}",
                    )
                )
        for conflict_type, detail in conflicts:
            rows.append(
                {
                    "case_id": record["case_id"],
                    "conflict_type": conflict_type,
                    "detail": detail[:300],
                    "llm_confidence": confidence,
                    "llm_answerability": record["answerability_assessment"],
                    "annotated_answerability": annotation["answerability"] or "",
                    "llm_suggested_tools": " ".join(sorted(suggested)),
                    "annotated_valid_tools": " ".join(sorted(annotated_tools)),
                    "actual_calls": " ".join(actual),
                    "missing_required_calls": " ".join(record.get("missing_required_calls") or []),
                    "needs_human_review": "yes" if record.get("needs_human_review") else "no",
                }
            )
    return rows


def build_summary(
    batch_id: str,
    records: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    eval_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    by_case = {item["case_id"]: item for item in inputs}
    total_calls = 0
    correct_calls = 0
    per_tool: dict[str, dict[str, int]] = {}
    answerability_counts: dict[str, int] = {}
    route_correct = 0
    needs_review = 0
    for record in records:
        answerability_counts[record["answerability_assessment"]] = (
            answerability_counts.get(record["answerability_assessment"], 0) + 1
        )
        if record["turn_tool_route_correct"]:
            route_correct += 1
        if record["needs_human_review"]:
            needs_review += 1
        for call in record["calls"]:
            total_calls += 1
            case_input = by_case.get(record["case_id"])
            key = "unknown"
            if case_input:
                match = next(
                    (
                        item
                        for item in case_input["tool_calls"]
                        if item["call_sequence"] == call["call_sequence"]
                    ),
                    None,
                )
                if match:
                    key = f"{match['tool']}.{match['operation']}"
            bucket = per_tool.setdefault(
                key, {"calls": 0, "final_call_correct": 0, "call_needed_false": 0, "parameter_issues": 0}
            )
            bucket["calls"] += 1
            if call["final_call_correct"]:
                correct_calls += 1
                bucket["final_call_correct"] += 1
            if not call["call_needed"]:
                bucket["call_needed_false"] += 1
            if not call["parameters_correct"]:
                bucket["parameter_issues"] += 1
    missing_call_cases = sum(1 for record in records if record.get("missing_required_calls"))
    conflicts = collect_conflicts(records, inputs)
    precision = round(correct_calls / total_calls, 4) if total_calls else None
    return {
        "batch_id": batch_id,
        "prompt_version": PROMPT_VERSION,
        "evaluation_model": os.getenv("QWEN_MODEL") or os.getenv("QWEN_CHAT_MODEL", "qwen-plus"),
        "temperature": 0.0,
        "knowledge_cutoff": KNOWLEDGE_CUTOFF_DEFAULT,
        "case_count": len(records),
        "unique_case_ids": len({record["case_id"] for record in records}),
        "turns_with_calls": sum(1 for record in records if record["calls"]),
        "turns_without_calls": sum(1 for record in records if not record["calls"]),
        "turn_tool_route_correct": route_correct,
        "turn_route_rate": round(route_correct / len(records), 4) if records else None,
        "total_calls_judged": total_calls,
        "final_call_correct": correct_calls,
        "tool_call_precision": precision,
        "turns_with_missing_required_calls": missing_call_cases,
        "answerability_counts": answerability_counts,
        "needs_human_review": needs_review,
        "per_tool": per_tool,
        "conflict_rows": len(conflicts),
        "conflict_cases": len({row["case_id"] for row in conflicts}),
        "conflict_type_counts": dict(
            sorted(
                {
                    ctype: sum(1 for row in conflicts if row["conflict_type"] == ctype)
                    for ctype in {row["conflict_type"] for row in conflicts}
                }.items(),
                key=lambda item: -item[1],
            )
        ),
        "evaluation_run": eval_meta or {},
    }


def load_output_records(output_path: Path) -> list[dict[str, Any]]:
    records = []
    seen: set[str] = set()
    duplicates: list[str] = []
    with open(output_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record["case_id"] in seen:
                duplicates.append(record["case_id"])
            seen.add(record["case_id"])
            records.append(record)
    if duplicates:
        raise SystemExit(f"duplicate case_ids in output: {duplicates[:5]}")
    return records


def summarise(batch_id: str, output_dir: Path, eval_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    inputs = load_review_inputs(batch_id)
    records = load_output_records(output_dir / "tool_llm_review.jsonl")
    conflicts = collect_conflicts(records, inputs)
    conflict_path = output_dir / "tool_llm_review_conflicts.csv"
    with open(conflict_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONFLICT_COLUMNS)
        writer.writeheader()
        writer.writerows(conflicts)
    summary = build_summary(batch_id, records, inputs, eval_meta)
    summary["provenance"] = {
        "input_file_hashes": {
            name: file_sha256(PROJECT_ROOT / "evaluation" / "questions" / name)
            for name in ("questions_annotated_v1.jsonl",)
        },
        "database": "runtime/fintrace.sqlite3 (read-only)",
    }
    summary["validation"] = validate_batch(records, inputs)
    with open(output_dir / "tool_llm_review_summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


def validate_batch(records: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    input_ids = [item["case_id"] for item in inputs]
    record_ids = [record["case_id"] for record in records]
    expected_calls = sum(len(item["tool_calls"]) for item in inputs)
    judged_calls = sum(len(record["calls"]) for record in records)
    input_by_case = {item["case_id"]: item for item in inputs}
    sequence_mismatch = []
    for record in records:
        case_input = input_by_case.get(record["case_id"])
        if not case_input:
            continue
        expected_sequences = sorted(call["call_sequence"] for call in case_input["tool_calls"])
        judged_sequences = sorted(call["call_sequence"] for call in record["calls"])
        if expected_sequences != judged_sequences:
            sequence_mismatch.append(record["case_id"])
    return {
        "expected_cases": len(input_ids),
        "reviewed_cases": len(record_ids),
        "missing_case_ids": sorted(set(input_ids) - set(record_ids))[:20],
        "unexpected_case_ids": sorted(set(record_ids) - set(input_ids))[:20],
        "duplicate_case_ids": len(record_ids) - len(set(record_ids)),
        "expected_tool_calls": expected_calls,
        "judged_tool_calls": judged_calls,
        "call_sequence_mismatches": sequence_mismatch[:20],
        "complete": (
            set(record_ids) == set(input_ids)
            and judged_calls == expected_calls
            and not sequence_mismatch
        ),
    }


# ---------------------------------------------------------------- cli

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default="EVAL-20260825T204302Z-FA59F317-2CB3")
    parser.add_argument(
        "--stage",
        choices=("extract", "evaluate", "summarize"),
        required=True,
    )
    parser.add_argument("--limit", type=int, default=None, help="evaluate only first N pending cases")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--eval-meta", default=None, help="JSON string merged into summary evaluation_run")
    args = parser.parse_args()
    output_dir = DEFAULT_OUTPUT_ROOT / args.batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.stage == "extract":
        inputs = load_review_inputs(args.batch_id)
        input_path = output_dir / "tool_llm_review_input.jsonl"
        with open(input_path, "w", encoding="utf-8") as handle:
            for case_input in inputs:
                handle.write(json.dumps(case_input, ensure_ascii=False) + "\n")
        print(json.dumps({"cases": len(inputs), "input_path": str(input_path)}))
    elif args.stage == "evaluate":
        meta = run_evaluation(args.batch_id, output_dir, limit=args.limit, concurrency=args.concurrency)
        print(json.dumps(meta, ensure_ascii=False))
    else:
        eval_meta = json.loads(args.eval_meta) if args.eval_meta else None
        summary = summarise(args.batch_id, output_dir, eval_meta)
        print(
            json.dumps(
                {
                    "cases": summary["case_count"],
                    "calls": summary["total_calls_judged"],
                    "precision": summary["tool_call_precision"],
                    "validation": summary["validation"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
