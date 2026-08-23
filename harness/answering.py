from schemas.agent_state import AgentState


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


def _completed_tool_summary_lines(state: AgentState) -> list[str]:
    lines: list[str] = []
    if state.tool_call_history:
        lines.append("")
        lines.append("工具调用：")
        for entry in state.tool_call_history:
            lines.append(f"- {entry.tool_name}.{entry.operation} status={entry.status}")

    for result in state.tool_results:
        data = result.data
        if result.tool_name.value == "financial_analysis":
            lines.append("")
            lines.append(
                f"financial_analysis：operation={data.get('operation')}, "
                f"record_count={data.get('record_count')}, "
                f"comparison_dimension={data.get('comparison_dimension')}"
            )
        elif result.tool_name.value == "ownership_analysis":
            companies = data.get("companies", [])
            lines.append("")
            lines.append(
                f"ownership_analysis：operation={data.get('operation')}, "
                f"direction={data.get('direction')}, company_count={len(companies)}, "
                f"as_of_date={data.get('as_of_date')}"
            )
        elif result.tool_name.value == "document_search":
            lines.append("")
            lines.append(f"document_search：hit_count={len(data.get('hits', []))}")
        elif result.tool_name.value == "event_timeline":
            lines.append("")
            lines.append(f"event_timeline：cluster_count={len(data.get('clusters', []))}")
        elif result.tool_name.value == "research_analysis":
            lines.append("")
            lines.append(f"research_analysis：claim_count={data.get('claim_count', 0)}")

    if state.evidence_ledger:
        lines.append("")
        lines.append("证据 ID：")
        for evidence in state.evidence_ledger[:20]:
            lines.append(f"- {evidence.evidence_id} ({evidence.evidence_type})")
        if len(state.evidence_ledger) > 20:
            lines.append(f"- ... 另有 {len(state.evidence_ledger) - 20} 条证据")
    return lines
