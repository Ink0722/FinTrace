"""Next-action planner. Phase 1: deterministic queue derived from ParsedRequest.

Phase 2 replaces the rule brain with the LLM skill (prompts/03_next_action_planner.md);
the AgentAction contract and this module's public API stay unchanged.
"""
from __future__ import annotations

from schemas.agent_state import AgentState
from schemas.request import AgentAction, ParsedRequest

from harness.routing.direct_gate import DEFAULT_TOP_K, _compare_boundary


def plan_next_action(state: AgentState) -> AgentAction:
    """Emit exactly one action per call, skipping already-executed queue items."""
    parsed = state.parsed_request
    if parsed is None:
        return AgentAction(action="finish", reason="缺少解析结果，终止调查。")

    executed_keys = {(entry.tool_name, entry.operation) for entry in state.tool_call_history}
    for action in _action_queue(parsed):
        if action.action != "call_tool":
            continue
        key = (action.tool_name, action.operation)
        if key in executed_keys and not _arguments_gain(action, state):
            continue
        return action
    return AgentAction(action="finish", reason="规则调查队列已执行完毕。", expected_evidence=None)


def _action_queue(parsed: ParsedRequest) -> list[AgentAction]:
    queue: list[AgentAction] = []
    family = parsed.task_family
    entities = parsed.entities

    if family == "unknown":
        if entities:
            queue.append(_event_action(parsed))
            queue.append(_research_action(parsed))
            queue.append(_holding_query_action(parsed))
        elif parsed.people:
            queue.append(_holder_reverse_action(parsed))
        else:
            queue.append(_document_action(parsed))
    elif family in {"financial_investigation", "financial_metric_query", "financial_metric_compare"}:
        if family == "financial_investigation" and len(entities) == 1 and parsed.periods:
            queue.append(_risk_scan_action(parsed))
        if entities and parsed.metrics and parsed.periods:
            queue.append(_metric_query_action(parsed))
        if entities and parsed.metrics and len(parsed.periods) >= 2 and len(entities) == 1:
            queue.append(_metric_compare_action(parsed))
        if entities:
            if family == "financial_investigation":
                queue.append(_event_action(parsed))
                queue.append(_document_action(parsed, force_announcement=True))
            else:
                queue.append(_document_action(parsed))
                if parsed.document_types:
                    queue.append(_document_action(parsed, drop_type_filter=True))
                queue.append(_event_action(parsed))
        if not entities and parsed.people:
            queue.append(_holder_reverse_action(parsed))
        if not queue:
            queue.append(_document_action(parsed))
    elif family in {"ownership_snapshot", "ownership_compare", "ownership_penetration"}:
        if entities or parsed.people:
            queue.append(_holding_query_action(parsed))
        if entities and len(entities) == 1:
            start, end = _compare_boundary(parsed)
            if start and end:
                queue.append(_holding_compare_action(parsed, start, end))
        if family == "ownership_penetration" and len(entities) == 1 and len(parsed.people) == 1:
            as_of = parsed.as_of_dates[-1] if parsed.as_of_dates else parsed.end_date
            if as_of:
                queue.append(_penetration_action(parsed, parsed.people[0], entities[0], as_of))
        queue.append(_document_action(parsed))
    elif family == "event_investigation":
        if entities:
            queue.append(_event_action(parsed))
        queue.append(_document_action(parsed, force_announcement=True))
        if entities and parsed.metrics and parsed.periods:
            queue.append(_metric_query_action(parsed))
    elif family == "research_investigation":
        if entities:
            queue.append(_research_action(parsed))
        queue.append(_document_action(parsed, force_research=True))
    elif family == "research_view_query":
        if entities:
            queue.append(_research_action(parsed))
    elif family in {"event_query", "document_retrieval", "general_financial_explanation"}:
        if entities and family == "event_query":
            queue.append(_event_action(parsed))
        if family != "event_query" or not entities:
            queue.append(_document_action(parsed))
        if entities and parsed.metrics and parsed.periods and family != "event_query":
            queue.append(_metric_query_action(parsed))
    return queue


def _arguments_gain(action: AgentAction, state: AgentState) -> bool:
    """A repeated call is allowed only when its arguments differ from every executed call."""
    for entry in state.tool_call_history:
        if entry.tool_name == action.tool_name and entry.operation == action.operation:
            if _canonical_args(entry.arguments) == _canonical_args(action.arguments):
                return False
    return True


def _canonical_args(arguments: dict) -> dict:
    # `operation` is injected at execution for financial/ownership tools; exclude it so
    # LLM-proposed actions and recorded history fingerprints stay comparable.
    return {key: value for key, value in arguments.items() if key not in {"query", "reason", "operation"}}


def _metric_query_action(parsed: ParsedRequest) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="financial_metric_query",
        tool_name="financial_analysis",
        operation="metric_query",
        arguments={
            "query": parsed.raw_query,
            "operation": "metric_query",
            "company_ids": parsed.entities,
            "metric_codes": parsed.metrics,
            "report_periods": parsed.periods,
        },
        reason="优先获取结构化财务指标事实。",
        expected_evidence="指标数值、口径与来源证据",
    )


def _metric_compare_action(parsed: ParsedRequest) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="financial_metric_compare",
        tool_name="financial_analysis",
        operation="metric_compare",
        arguments={
            "query": parsed.raw_query,
            "operation": "metric_compare",
            "company_ids": parsed.entities,
            "metric_codes": parsed.metrics,
            "report_periods": parsed.periods,
            "comparison_method": "both",
        },
        reason="对多期指标做确定性比较。",
        expected_evidence="跨期序列与变化幅度",
    )


def _risk_scan_action(parsed: ParsedRequest) -> AgentAction:
    arguments = {
        "query": parsed.raw_query,
        "operation": "risk_scan",
        "company_ids": parsed.entities[:1],
        "report_periods": parsed.periods,
        "requested_periods": parsed.requested_periods,
        "target_period": parsed.target_period,
        "period_resolution_mode": parsed.period_resolution_mode,
    }
    if parsed.focus_topics:
        arguments["focus_topics"] = parsed.focus_topics
    return AgentAction(
        action="call_tool",
        capability="financial_risk_scan",
        tool_name="financial_analysis",
        operation="risk_scan",
        arguments=arguments,
        reason="执行可复核的财务风险规则扫描。",
        expected_evidence="规则输入、阈值、计算值与财务行级证据",
    )


def _holding_query_action(parsed: ParsedRequest) -> AgentAction:
    arguments: dict = {"query": parsed.raw_query, "operation": "holding_query", "top_n": 10}
    if parsed.entities:
        arguments["company_ids"] = parsed.entities
    if parsed.people:
        arguments["holder_ids"] = parsed.people
    if parsed.as_of_dates:
        arguments["as_of_date"] = parsed.as_of_dates[-1]
    return AgentAction(
        action="call_tool",
        capability="ownership_snapshot",
        tool_name="ownership_analysis",
        operation="holding_query",
        arguments=arguments,
        reason="获取主要股东快照作为股权事实基础。",
        expected_evidence="主要股东名单与持股比例",
    )


def _holding_compare_action(parsed: ParsedRequest, start: str, end: str) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="ownership_compare",
        tool_name="ownership_analysis",
        operation="holding_compare",
        arguments={
            "query": parsed.raw_query,
            "operation": "holding_compare",
            "company_ids": parsed.entities[:1],
            "start_date": start,
            "end_date": end,
        },
        reason="比较两个时点的股东变化。",
        expected_evidence="股东进入、退出与增减持",
    )


def _holder_reverse_action(parsed: ParsedRequest) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="ownership_snapshot",
        tool_name="ownership_analysis",
        operation="holding_query",
        arguments={"query": parsed.raw_query, "operation": "holding_query", "holder_ids": parsed.people, "top_n": 10},
        reason="按股东反查其持股的公司。",
        expected_evidence="股东出现的公司快照与持股比例",
    )


def _penetration_action(parsed: ParsedRequest, source: str, target: str, as_of: str) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="ownership_penetration",
        tool_name="ownership_analysis",
        operation="penetration",
        arguments={
            "query": parsed.raw_query,
            "operation": "penetration",
            "source_entity_id": source,
            "target_entity_id": target,
            "as_of_date": as_of,
            "max_depth": 4,
            "max_paths": 10,
        },
        reason="在主要股东有效快照中搜索有界持股路径。",
        expected_evidence="路径每一跳的持股比例、快照日期与证据",
    )


def _document_action(
    parsed: ParsedRequest,
    *,
    drop_type_filter: bool = False,
    force_research: bool = False,
    force_announcement: bool = False,
) -> AgentAction:
    arguments: dict = {"query": parsed.raw_query, "top_k": DEFAULT_TOP_K}
    if parsed.entities:
        arguments["company_ids"] = parsed.entities
    if parsed.start_date:
        arguments["start_date"] = parsed.start_date
    if parsed.end_date:
        arguments["end_date"] = parsed.end_date
    if force_research:
        arguments["document_types"] = ["research_report"]
    elif force_announcement:
        arguments["document_types"] = ["announcement"]
    elif parsed.document_types and not drop_type_filter:
        arguments["document_types"] = parsed.document_types
    return AgentAction(
        action="call_tool",
        capability="document_retrieval",
        tool_name="document_search",
        operation="search",
        arguments=arguments,
        reason="检索公告/研报文本证据补充解释。",
        expected_evidence="与调查主题相关的原文 Chunk",
    )


def _event_action(parsed: ParsedRequest) -> AgentAction:
    arguments: dict = {
        "query": parsed.raw_query,
        "operation": "event_query",
        "entity_ids": parsed.entities,
    }
    if parsed.event_types:
        arguments["event_types"] = parsed.event_types
    if parsed.start_date:
        arguments["start_date"] = parsed.start_date
    if parsed.end_date:
        arguments["end_date"] = parsed.end_date
    return AgentAction(
        action="call_tool",
        capability="event_query",
        tool_name="event_timeline",
        operation="event_query",
        arguments=arguments,
        reason="获取监管与风险事件时间线。",
        expected_evidence="事件节点、类型与日期",
    )


def _research_action(parsed: ParsedRequest) -> AgentAction:
    arguments: dict = {
        "query": parsed.raw_query,
        "operation": "view_query",
        "company_ids": parsed.entities,
        "limit": 20,
    }
    if parsed.start_date:
        arguments["start_date"] = parsed.start_date
    if parsed.end_date:
        arguments["end_date"] = parsed.end_date
    if parsed.institutions:
        arguments["institutions"] = parsed.institutions
    if parsed.research_claim_types:
        arguments["claim_types"] = parsed.research_claim_types
    if parsed.focus_topics:
        arguments["topics"] = parsed.focus_topics
    return AgentAction(
        action="call_tool", capability="research_view_query",
        tool_name="research_analysis", operation="view_query",
        arguments=arguments,
        reason="先定位结构化机构观点，再按需检索对应研报原文。",
        expected_evidence="带机构归因、发布日期和Chunk定位的观点证据",
    )
