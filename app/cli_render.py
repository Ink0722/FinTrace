"""Human-readable CLI rendering for AgentState dictionaries."""
from __future__ import annotations

import json
from typing import Any

LINE = "-" * 72
METRIC_LABELS = {
    "REVENUE": "营业收入", "INVENTORY": "存货", "TOTAL_ASSETS": "总资产",
    "OPERATING_COST": "营业成本", "NET_PROFIT_PARENT": "归母净利润",
    "OPERATING_CASH_FLOW": "经营活动现金流净额",
}
TASK_LABELS = {
    "financial_metric_query": "财务指标查询", "financial_metric_compare": "财务指标比较",
    "financial_investigation": "财务调查", "ownership_snapshot": "主要股东快照查询",
    "ownership_compare": "股东变化比较", "document_retrieval": "文档检索",
    "event_query": "事件查询", "event_investigation": "事件调查", "unknown": "待识别任务",
}
TOOL_LABELS = {
    "financial_analysis": "财务分析", "ownership_analysis": "股东分析",
    "document_search": "文档检索", "event_timeline": "事件时间线",
}
NODE_LABELS = {
    "load_session": "会话加载", "resolve_request": "请求解析",
    "check_pre_answerability": "可回答性判断", "build_clarification": "澄清追问",
    "build_refusal": "能力边界拒绝", "route_mode": "路径分流",
    "plan_next_action": "下一步动作规划", "validate_action": "动作校验",
    "repair_action": "动作修复", "execute_one_tool": "工具执行",
    "validate_tool_result": "结果校验", "merge_evidence": "证据合并",
    "review_evidence": "证据充分性审查", "generate_answer": "答案生成",
    "persist_session": "会话保存", "structured_error": "错误收束",
}
NODE_DESCRIPTIONS = {
    "load_session": "恢复本会话上下文与近期消息", "resolve_request": "解析主体、时间、任务、指标与约束",
    "check_pre_answerability": "判断是否可处理、需澄清或应拒绝", "build_clarification": "缺少唯一必要条件，向用户澄清",
    "build_refusal": "请求超出当前数据或能力边界", "route_mode": "选择确定性直连或有界调查",
    "plan_next_action": "Planner 选择一个信息增益最高的动作", "validate_action": "检查能力、参数、实体、截止日、重复与预算",
    "repair_action": "对非法动作进行一次最小修复", "execute_one_tool": "执行当前工具调用",
    "validate_tool_result": "检查工具状态与返回契约", "merge_evidence": "将新增证据并入 Evidence Ledger",
    "review_evidence": "判断证据覆盖和剩余缺口", "generate_answer": "仅根据证据生成最终回答",
    "persist_session": "保存上下文供下一轮继承", "structured_error": "模型失败后返回结构化错误",
}


def format_final_answer(raw_answer: str) -> str:
    try:
        parsed = json.loads(raw_answer)
    except (json.JSONDecodeError, TypeError):
        return raw_answer
    if not isinstance(parsed, dict) or "answer" not in parsed:
        return raw_answer
    lines = [str(parsed.get("answer") or "")]
    limitations = parsed.get("limitations") or parsed.get("limitations_disclosed") or []
    if limitations:
        lines.extend(["", "⚠️ 限制说明"])
        lines.extend(f"  - {item}" for item in limitations)
    return "\n".join(lines)


def print_compact_footer(state: dict, elapsed_ms: int) -> None:
    status = state.get("answer_status") or state.get("workflow_status") or "unknown"
    icon = "✅" if status == "answered" else "⚠️" if status in {
        "partially_answered", "insufficient_evidence", "clarification_required"
    } else "❌"
    print()
    print(
        f"{icon} {answer_status_label(status)} | 🛠️ {len(state.get('tool_call_history') or [])} 次工具 | "
        f"📎 {len(state.get('evidence_ledger') or [])} 条证据 | ⏱️ {elapsed_ms / 1000:.1f} 秒"
    )


def print_trace(state: dict, *, debug: bool = False) -> None:
    print_request_summary(state)
    print_route_summary(state)
    print_tools(state)
    print_evidence(state)
    print_gaps_and_errors(state)
    if debug:
        print_debug_nodes(state)


def print_request_summary(state: dict) -> None:
    parsed, context = state.get("parsed_request") or {}, state.get("current_context") or {}
    print(f"\n🔍 请求解析（第 {state.get('turn_id', 1)} 轮）")
    print(LINE)
    _field("主体", _join(parsed.get("entities") or context.get("company_ids")) or "未识别")
    _field("任务", TASK_LABELS.get(parsed.get("task_family"), parsed.get("task_family") or "未识别"))
    _field("报告期", _join(parsed.get("periods")) or "未指定")
    if _date_range(parsed):
        _field("时间范围", _date_range(parsed))
    _field("财务指标", _metric_names(parsed.get("metrics")) or "无")
    _field("关注主题", _join(parsed.get("focus_topics")) or "无")
    _field("文档类型", _join(parsed.get("document_types")) or "不限制")
    _field("信息截止日", state.get("knowledge_cutoff") or "未设置")
    if parsed.get("missing_slots"):
        _field("缺少条件", _join(parsed["missing_slots"]))


def print_route_summary(state: dict) -> None:
    pre, mode = state.get("pre_answerability") or {}, state.get("routing_mode")
    print("\n🧭 路由与执行摘要")
    print(LINE)
    _field("可回答性", f"{pre.get('status') or 'unknown'}：{pre.get('reason') or '无说明'}")
    _field("执行路径", "确定性直连" if mode == "direct" else "有界调查" if mode == "investigation" else "未进入工具路径")
    _field("执行节点", f"{len(state.get('executed_nodes') or [])} 个")
    _field("终止原因", state.get("termination_reason") or "正常完成")
    _field("回答状态", answer_status_label(state.get("answer_status")))


def print_tools(state: dict) -> None:
    history, results = state.get("tool_call_history") or [], state.get("tool_results") or []
    print("\n🛠️ 工具调用与结果")
    print(LINE)
    if not history:
        print("本轮未调用工具。")
        return
    for index, entry in enumerate(history, 1):
        result = results[index - 1] if index <= len(results) else {}
        name = entry.get("tool_name") or "unknown"
        status = entry.get("status") or result.get("status") or "unknown"
        icon = "✅" if status == "success" else "❌"
        print(f"\n{icon} 工具 {index}：{TOOL_LABELS.get(name, name)} / {entry.get('operation') or '默认操作'}")
        _field("目的", entry.get("action_reason") or "未说明", indent=2)
        print_arguments(entry.get("arguments") or {}, indent=2)
        _field("状态", tool_status_label(status), indent=2)
        _field("工具耗时", f"{(result.get('metrics') or {}).get('execution_time_ms', 0)} ms", indent=2)
        render_tool_result(name, result, indent=2)
        for warning in (result.get("warnings") or [])[:3]:
            print(f"  ⚠️ {warning}")
        error = result.get("error") or {}
        if error:
            _field("错误类型", error.get("error_type") or "unknown", indent=2)
            _field("错误信息", error.get("message") or "无", indent=2)


def print_arguments(arguments: dict[str, Any], *, indent: int = 0) -> None:
    labels = {
        "company_ids": "公司", "holder_ids": "股东", "entity_ids": "主体",
        "metric_codes": "指标", "report_periods": "报告期", "as_of_date": "观察日期",
        "start_date": "开始日期", "end_date": "结束日期", "document_types": "文档类型",
        "event_types": "事件类型", "query": "问题/检索词", "mode": "检索模式", "top_k": "返回数量",
    }
    visible = [(labels.get(key, key), value) for key, value in arguments.items() if key not in {"operation", "knowledge_cutoff"}]
    if not visible:
        _field("参数", "无额外参数", indent=indent)
    for label, value in visible:
        _field(label, _metric_names(value) if label == "指标" else _display(value), indent=indent)


def render_tool_result(tool_name: str, result: dict, *, indent: int) -> None:
    data = result.get("data") or {}
    if tool_name == "financial_analysis":
        _render_financial(data, indent)
    elif tool_name == "document_search":
        _render_documents(data, indent)
    elif tool_name == "ownership_analysis":
        _render_ownership(data, indent)
    elif tool_name == "event_timeline":
        _render_events(data, indent)
    _field("新增证据", f"{len(result.get('evidence') or [])} 条", indent=indent)


def _render_financial(data: dict, indent: int) -> None:
    if data.get("operation") == "risk_scan":
        coverage = data.get("coverage") or {}
        _field("规则覆盖", f"{coverage.get('evaluated_rule_count', 0)}/{coverage.get('requested_rule_count', 0)}", indent=indent)
        for signal in (data.get("signals") or [])[:8]:
            status = signal.get("status") or "unknown"
            severity = signal.get("severity")
            suffix = f" / {severity}" if severity else ""
            _field(signal.get("name") or signal.get("rule_id"), f"{status}{suffix}", indent=indent)
        if data.get("rules_skipped"):
            _field("跳过规则", len(data["rules_skipped"]), indent=indent)
        return
    _field("记录数量", data.get("record_count", 0), indent=indent)
    for item in (data.get("values") or [])[:8]:
        metric = item.get("metric_name") or METRIC_LABELS.get(item.get("metric_code"), item.get("metric_code"))
        _field(f"{item.get('report_period') or '未知期间'} {metric}", format_number(item.get("value"), item.get("currency")), indent=indent)
    comparisons = data.get("comparisons") or []
    if comparisons:
        _field("比较结果", f"{len(comparisons)} 项", indent=indent)
    if data.get("missing"):
        _field("缺失组合", f"{len(data['missing'])} 项", indent=indent)


def _render_documents(data: dict, indent: int) -> None:
    hits = data.get("hits") or []
    _field("召回模式", data.get("mode") or "unknown", indent=indent)
    _field("有效截止日", data.get("effective_end_date") or "未限制", indent=indent)
    _field("最终命中", f"{len(hits)} 条", indent=indent)
    for hit in hits[:3]:
        chunk = hit.get("chunk") or {}
        title = chunk.get("title") or chunk.get("document_id") or hit.get("evidence_id") or "未命名文档"
        print(" " * indent + f"  • {chunk.get('publish_date') or '日期未知'}｜{title}")


def _render_ownership(data: dict, indent: int) -> None:
    if data.get("operation") == "penetration":
        paths = data.get("paths") or []
        _field("可证实路径", len(paths), indent=indent)
        for path in paths[:5]:
            ratio = path.get("path_ratio")
            ratio_text = f"{ratio * 100:.4f}%" if isinstance(ratio, (int, float)) else "无法计算"
            _field(path.get("path_id") or "路径", f"{path.get('depth', 0)} 跳 / {ratio_text}", indent=indent)
            for edge in path.get("edges") or []:
                edge_ratio = edge.get("holding_ratio")
                edge_ratio_text = f"{edge_ratio * 100:.2f}%" if isinstance(edge_ratio, (int, float)) else "比例缺失"
                print(" " * indent + f"  -> {edge.get('source_name')} -> {edge.get('target_name')} | {edge_ratio_text} | {edge.get('holder_end_date')}")
        return
    _field("查询方向", data.get("direction") or data.get("operation") or "unknown", indent=indent)
    companies = data.get("companies") or []
    _field("公司数量", len(companies), indent=indent)
    for company in companies[:3]:
        snapshot = company.get("snapshot") or {}
        _field(f"{company.get('company_id')} 快照", snapshot.get("holder_end_date") or snapshot.get("announcement_date") or "未知", indent=indent)
        for holder in (company.get("holders") or company.get("holdings") or [])[:5]:
            name = holder.get("holder_name") or holder.get("name") or holder.get("holder_entity_id")
            ratio = holder.get("holding_ratio")
            suffix = f"：{ratio * 100:.2f}%" if isinstance(ratio, (int, float)) else ""
            print(" " * indent + f"  • {name}{suffix}")
    for key, label in (("entered", "新进"), ("exited", "退出"), ("increased", "增持"), ("decreased", "减持")):
        if key in data:
            _field(label, f"{len(data.get(key) or [])} 名", indent=indent)


def _render_events(data: dict, indent: int) -> None:
    summary = data.get("summary") or {}
    _field("事件数量", summary.get("event_count", len(data.get("events") or [])), indent=indent)
    _field("事件簇数量", summary.get("cluster_count", len(data.get("clusters") or [])), indent=indent)
    if summary.get("date_range"):
        _field("日期范围", _join(summary["date_range"]), indent=indent)
    for event in (data.get("events") or [])[:3]:
        print(" " * indent + f"  • {event.get('event_date')}｜{event.get('title') or event.get('event_type')}")


def print_evidence(state: dict) -> None:
    evidence = state.get("evidence_ledger") or []
    print("\n📎 关键证据")
    print(LINE)
    if not evidence:
        print("本轮没有获得可用证据。")
        return
    for index, item in enumerate(evidence[:8], 1):
        source, fact = item.get("source") or {}, item.get("fact") or {}
        print(f"{index}. {item.get('evidence_type') or 'evidence'}")
        _field("证据 ID", item.get("evidence_id"), indent=2)
        _field("主体", source.get("company_id") or fact.get("company_id") or "未标明", indent=2)
        if evidence_fact_summary(fact):
            _field("内容", evidence_fact_summary(fact), indent=2)
        location = source.get("document_id") or source.get("row_id") or source.get("source_path")
        if location:
            _field("来源定位", location, indent=2)
    if len(evidence) > 8:
        print(f"… 另有 {len(evidence) - 8} 条证据未展开。")


def print_gaps_and_errors(state: dict) -> None:
    gaps, errors, warnings = state.get("evidence_gaps") or [], state.get("errors") or [], state.get("warnings") or []
    if not gaps and not errors and not warnings:
        return
    print("\n⚠️ 缺口与异常")
    print(LINE)
    for gap in gaps[:6]:
        print(f"• [{gap.get('priority', 'medium')}] {gap.get('description')}")
    for warning in warnings[:4]:
        print(f"• 警告：{warning}")
    for error in errors[:4]:
        print(f"• 错误：{error.get('message') or error.get('error_type') or _display(error)}")


def print_debug_nodes(state: dict) -> None:
    print("\n🧩 LangGraph 完整节点")
    print(LINE)
    for index, node in enumerate(state.get("executed_nodes") or [], 1):
        icon = "❌" if node in {"build_refusal", "structured_error"} else "⚠️" if node == "build_clarification" else "✅"
        print(f"{index:>2}. {icon} {NODE_LABELS.get(node, node)}")
        print(f"    {NODE_DESCRIPTIONS.get(node, '执行工作流节点')} | node: {node}")


def print_session_status(state: dict | None, session_id: str, trace: bool, debug: bool) -> None:
    print("\n💬 当前会话")
    print(LINE)
    _field("会话 ID", session_id)
    _field("Trace", "完整调试" if debug else "开启" if trace else "关闭")
    if not state:
        _field("轮次", "尚未提问")
        return
    context = state.get("current_context") or {}
    _field("最近轮次", state.get("turn_id", 1))
    _field("当前主体", _join(context.get("company_ids")) or "无")
    _field("当前期间", _join(context.get("report_periods")) or "无")
    _field("当前主题", _join(context.get("focus_topics")) or context.get("active_topic") or "无")


def evidence_fact_summary(fact: dict[str, Any]) -> str:
    if fact.get("metric_code"):
        metric = fact.get("metric_name") or METRIC_LABELS.get(fact.get("metric_code"), fact.get("metric_code"))
        return f"{fact.get('report_period') or ''} {metric} = {format_number(fact.get('value'), fact.get('currency'))}".strip()
    text = fact.get("text") or fact.get("summary") or fact.get("title")
    if text:
        text = str(text).replace("\n", " ").strip()
        return text[:160] + ("…" if len(text) > 160 else "")
    if fact.get("holding_ratio") is not None:
        ratio = fact.get("holding_ratio")
        ratio_text = f"{ratio * 100:.2f}%" if isinstance(ratio, (int, float)) else str(ratio)
        return f"股东={fact.get('holder_name') or '未标明'}，持股比例={ratio_text}"
    return "，".join(
        f"{key}={value}" for key, value in fact.items()
        if key in {"holder_name", "holding_ratio", "event_date", "event_type"}
    )


def format_number(value: Any, currency: str | None = None) -> str:
    if not isinstance(value, (int, float)):
        return _display(value)
    if currency == "CNY" and abs(value) >= 100_000_000:
        return f"{value / 100_000_000:,.2f} 亿元"
    if currency == "CNY":
        return f"{value:,.2f} 元"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def answer_status_label(status: str | None) -> str:
    return {
        "answered": "已回答", "partially_answered": "部分回答", "insufficient_evidence": "证据不足",
        "clarification_required": "需要澄清", "unsupported": "当前不支持", "failed": "执行失败",
    }.get(status or "", status or "状态未知")


def tool_status_label(status: str) -> str:
    return {"success": "成功", "failed": "失败", "partial": "部分成功"}.get(status, status)


def _metric_names(values: Any) -> str:
    return _join([METRIC_LABELS.get(value, value) for value in (values or [])])


def _date_range(parsed: dict) -> str:
    start, end = parsed.get("start_date"), parsed.get("end_date")
    return f"{start} 至 {end}" if start and end else start or end or _join(parsed.get("as_of_dates"))


def _join(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, (str, int, float)):
        return str(values)
    return "、".join(str(value) for value in values)


def _display(value: Any) -> str:
    if value is None:
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        return _join(value) or "无"
    if isinstance(value, dict):
        return "；".join(f"{key}={_display(item)}" for key, item in value.items()) or "无"
    return str(value)


def _field(label: str, value: Any, *, indent: int = 0) -> None:
    print(" " * indent + f"{label:<10} {_display(value)}")
