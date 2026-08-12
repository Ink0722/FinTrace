import json
from pathlib import Path
from typing import Any

import requests

from harness.llm import QwenClient
from schemas.agent_state import AgentState


DEFAULT_SYSTEM_PROMPT = "只能基于工具结果回答；证据不足必须说明；不得编造事实。"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system.md"


def load_system_prompt() -> str:
    try:
        prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    return prompt or DEFAULT_SYSTEM_PROMPT


def generate_answer_with_status(state: AgentState) -> tuple[str, str, dict[str, Any] | None]:
    payload = {
        "user_query": state.user_request.raw_query,
        "execution_plan": state.execution_plan.model_dump() if state.execution_plan else None,
        "tool_results": [result.model_dump() for result in state.tool_results],
        "evidence_ids": [evidence.evidence_id for evidence in state.evidence_ledger],
        "warnings": state.warnings,
    }
    client = QwenClient()
    if not client.enabled:
        error = {
            "stage": "generate_answer",
            "error_type": "LLM_NOT_CONFIGURED",
            "message": "未检测到 QWEN_API_KEY 或 DASHSCOPE_API_KEY。",
            "retryable": False,
        }
        return _structured_llm_error_text(state, error), "failed", error

    try:
        response = client.chat_json(
            [
                {"role": "system", "content": load_system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.0,
        )
    except requests.RequestException as exc:
        error = {
            "stage": "generate_answer",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "retryable": True,
        }
        return _structured_llm_error_text(state, error), "failed", error

    try:
        return response["choices"][0]["message"]["content"], "success", None
    except (KeyError, IndexError, TypeError):
        error = {
            "stage": "generate_answer",
            "error_type": "LLM_BAD_RESPONSE",
            "message": "Qwen returned an unexpected response shape.",
            "retryable": False,
        }
        return _structured_llm_error_text(state, error), "failed", error


def build_structured_error_answer(state: AgentState) -> str:
    lines = [
        "⚠️ 工作流未能生成正常研判。",
        "",
        "错误：",
    ]
    if state.errors:
        for error in state.errors:
            lines.append(f"- [{error.get('stage')}] {error.get('error_type')}: {error.get('message')}")
    else:
        lines.append("- UNKNOWN_ERROR: 未记录具体错误。")

    if any(error.get("stage") == "generate_answer" for error in state.errors):
        lines.append("")
        lines.append("LLM 生成失败，未生成自然语言研判。")
        lines.append("为避免误导，系统不会使用确定性模板伪装成模型回答。")
        lines.append("下面只展示已完成的结构化工具结果摘要，不补充模型推断。")

    if state.warnings:
        lines.append("")
        lines.append("警告：")
        for warning in state.warnings:
            lines.append(f"- {warning}")

    lines.extend(_completed_tool_summary_lines(state))
    return "\n".join(lines)


def _structured_llm_error_text(state: AgentState, error: dict[str, Any]) -> str:
    lines = [
        "⚠️ LLM 生成失败，未生成自然语言研判。",
        f"错误类型：{error.get('error_type')}",
        f"错误信息：{error.get('message')}",
        "",
        "为避免误导，下面只展示已完成的结构化工具结果摘要，不补充模型推断。",
    ]
    lines.extend(_completed_tool_summary_lines(state))
    return "\n".join(lines)


def _completed_tool_summary_lines(state: AgentState) -> list[str]:
    lines: list[str] = []
    if state.execution_plan and state.execution_plan.tool_calls:
        lines.append("")
        lines.append("工具调用：")
        for call in state.execution_plan.tool_calls:
            lines.append(f"- {call.tool_call_id} {call.tool_name.value}")

    for result in state.tool_results:
        data = result.data
        if result.tool_name.value == "financial_risk_analysis":
            lines.append("")
            lines.append(
                f"financial_risk_analysis：period={data.get('period')}, "
                f"risk_level={data.get('risk_level')}, "
                f"risk_score={data.get('risk_score')}, "
                f"triggered_rule_ids={data.get('triggered_rule_ids', [])}"
            )
        elif result.tool_name.value == "ownership_penetration":
            summary = data.get("summary", {})
            lines.append("")
            lines.append(
                f"ownership_penetration：path_count={summary.get('path_count')}, "
                f"as_of_date={data.get('as_of_date')}"
            )
        elif result.tool_name.value == "document_search":
            lines.append("")
            lines.append(f"document_search：hit_count={len(data.get('hits', []))}")
        elif result.tool_name.value == "event_timeline":
            lines.append("")
            lines.append(f"event_timeline：cluster_count={len(data.get('clusters', []))}")

    if state.evidence_ledger:
        lines.append("")
        lines.append("证据 ID：")
        for evidence in state.evidence_ledger[:20]:
            lines.append(f"- {evidence.evidence_id} ({evidence.evidence_type})")
        if len(state.evidence_ledger) > 20:
            lines.append(f"- ... 另有 {len(state.evidence_ledger) - 20} 条证据")
    return lines
