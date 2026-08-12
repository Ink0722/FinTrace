import json
import os
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from harness.llm import QwenClient
from harness.routing.entities import (
    extract_company_id,
    extract_document_types,
    extract_event_types,
    extract_focus_topics,
    extract_period,
)
from schemas.enums import ToolName
from schemas.tool_calls import ExecutionPlan, ToolCall


KEYWORD_RULES: list[tuple[ToolName, tuple[str, ...]]] = [
    (ToolName.OWNERSHIP_PENETRATION, ("实控人", "股东", "持股", "穿透", "控制链", "控制")),
    (ToolName.FINANCIAL_RISK_ANALYSIS, ("利润", "现金流", "存货", "应收", "毛利率", "偿债", "财务")),
    (ToolName.DOCUMENT_SEARCH, ("问询函", "审计报告", "附注", "原文", "依据", "研报", "公告")),
    (ToolName.EVENT_TIMELINE, ("时间线", "经过", "什么时候", "舆情", "事件", "后来", "处罚", "变更")),
]

PLANNER_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "planner.md"


def build_plan(query: str) -> ExecutionPlan:
    """Build a tool plan with LLM planner first, deterministic rule planner as fallback."""
    rule_plan = build_rule_plan(query)
    llm_plan = build_llm_plan(query)
    return llm_plan or rule_plan


def build_rule_plan(query: str) -> ExecutionPlan:
    selected = select_tools_by_rules(query)
    common_args = build_common_arguments(query)
    calls: list[ToolCall] = []
    for index, tool_name in enumerate(selected, start=1):
        calls.append(
            ToolCall(
                tool_call_id=f"CALL-{index:03d}",
                tool_name=tool_name,
                arguments=build_tool_arguments(tool_name, query, common_args),
                reason=f"规则计划选择 {tool_name.value}",
            )
        )
    return ExecutionPlan(plan_id="PLAN-001", user_intent=infer_intent(selected), tool_calls=calls)


def build_llm_plan(query: str) -> ExecutionPlan | None:
    """Ask Qwen for a candidate plan; invalid or unavailable planner output falls back to rules."""
    client = build_planner_client()
    if not client.enabled:
        return None
    try:
        response = client.chat_json(
            [
                {"role": "system", "content": load_planner_prompt()},
                {"role": "user", "content": json.dumps({"query": query, "rule_plan": build_rule_plan(query).model_dump()}, ensure_ascii=False)},
            ],
            temperature=0.0,
        )
        content = response["choices"][0]["message"]["content"]
        payload = json.loads(content)
        return normalize_llm_plan(query=query, payload=payload)
    except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, ValueError):
        return None


def load_planner_prompt() -> str:
    try:
        prompt = PLANNER_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Planner prompt file is required: {PLANNER_PROMPT_PATH}") from exc
    if not prompt:
        raise RuntimeError(f"Planner prompt file is empty: {PLANNER_PROMPT_PATH}")
    return prompt


def build_planner_client() -> QwenClient:
    api_key = os.getenv("QWEN_PLANNER_API_KEY") or os.getenv("DASHSCOPE_PLANNER_API_KEY")
    base_url = os.getenv("QWEN_PLANNER_BASE_URL") or os.getenv("QWEN_BASE_URL")
    model = os.getenv("QWEN_PLANNER_MODEL") or os.getenv("QWEN_ROUTER_MODEL")
    return QwenClient(api_key=api_key or "", base_url=base_url, model=model or "qwen-plus")


def normalize_llm_plan(query: str, payload: dict[str, Any]) -> ExecutionPlan:
    common_args = build_common_arguments(query)
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("LLM planner returned no tool_calls.")

    calls: list[ToolCall] = []
    seen: set[ToolName] = set()
    for raw_call in raw_calls[:4]:
        tool_name = ToolName(str(raw_call.get("tool_name")))
        if tool_name in seen:
            continue
        seen.add(tool_name)
        arguments = build_tool_arguments(tool_name, query, common_args)
        if isinstance(raw_call.get("arguments"), dict):
            arguments.update({key: value for key, value in raw_call["arguments"].items() if value not in (None, "", [])})
        calls.append(
            ToolCall(
                tool_call_id=f"CALL-{len(calls) + 1:03d}",
                tool_name=tool_name,
                arguments=arguments,
                reason=str(raw_call.get("reason") or "LLM planner 选择工具"),
            )
        )
    if not calls:
        raise ValueError("LLM planner returned no valid tool calls.")
    return ExecutionPlan(plan_id="PLAN-001", user_intent=str(payload.get("user_intent") or infer_intent([call.tool_name for call in calls])), tool_calls=calls)


def select_tools_by_rules(query: str) -> list[ToolName]:
    selected: list[ToolName] = []
    for tool_name, keywords in KEYWORD_RULES:
        if any(keyword in query for keyword in keywords):
            selected.append(tool_name)
    if not selected:
        selected.append(ToolName.DOCUMENT_SEARCH)
    return selected[:3]


def build_common_arguments(query: str) -> dict[str, Any]:
    args: dict[str, Any] = {
        "query": query,
        "company_id": extract_company_id(query),
    }
    period = extract_period(query)
    if period:
        args["period"] = period
    focus_topics = extract_focus_topics(query)
    if focus_topics:
        args["focus_topics"] = focus_topics
    return args


def build_tool_arguments(tool_name: ToolName, query: str, common_args: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(common_args)
    if tool_name == ToolName.DOCUMENT_SEARCH:
        document_types = extract_document_types(query)
        if document_types:
            arguments["document_types"] = document_types
        arguments.setdefault("top_k", 8)
    elif tool_name == ToolName.EVENT_TIMELINE:
        event_types = extract_event_types(query)
        if event_types:
            arguments["event_types"] = event_types
    elif tool_name == ToolName.OWNERSHIP_PENETRATION:
        arguments.setdefault("target_entity_id", common_args["company_id"])
        arguments.setdefault("max_depth", 5)
        arguments.setdefault("relation_types", ["OWNS", "CONTROLS"])
    return arguments


def infer_intent(selected: list[ToolName]) -> str:
    if len(selected) > 1:
        return "multi_tool_analysis"
    return selected[0].value
