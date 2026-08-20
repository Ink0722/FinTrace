import argparse
import json
import sys
from collections.abc import Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from harness.graph.workflow import run_agent
from harness.llm import QwenClient


LINE = "-" * 64

TRACE_NODE_LABELS = {
    "load_session": "会话加载",
    "resolve_request": "请求解析",
    "check_pre_answerability": "可回答性判断",
    "build_clarification": "澄清追问",
    "build_refusal": "能力边界拒绝",
    "route_mode": "路径分流",
    "plan_next_action": "下一步动作规划",
    "validate_action": "动作校验",
    "repair_action": "动作修复",
    "execute_one_tool": "工具执行",
    "validate_tool_result": "结果校验",
    "merge_evidence": "证据合并",
    "review_evidence": "证据充分性审查",
    "generate_answer": "答案生成",
    "persist_session": "会话保存",
    "structured_error": "错误收束",
}

TRACE_NODE_DESCRIPTIONS = {
    "load_session": "恢复会话上下文，支持多轮指代继承",
    "resolve_request": "解析实体、时间、任务族与约束（不选工具）",
    "check_pre_answerability": "判断能力是否存在、必要参数是否完整",
    "build_clarification": "缺少必要条件，向用户追问而不是猜测",
    "build_refusal": "请求超出系统能力边界，明确拒绝",
    "route_mode": "简单任务走确定性直连，复杂任务进入调查循环",
    "plan_next_action": "复杂任务：每轮只规划一个最有价值的下一动作",
    "validate_action": "校验动作合法性：工具、参数、预算、防重复",
    "repair_action": "对可局部修复的非法动作做一次最小修复",
    "execute_one_tool": "执行本轮唯一的工具调用，证据入账",
    "validate_tool_result": "检查工具结果结构与状态",
    "merge_evidence": "将本轮证据合并进统一证据账本",
    "review_evidence": "判断证据是否充分、是否继续调查",
    "generate_answer": "调用 Qwen 生成基于证据的金融研判",
    "persist_session": "保存会话上下文与结构化记忆",
    "structured_error": "返回结构化错误，避免在失败时编造答案",
}


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except (TypeError, ValueError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fintrace",
        description="FinTrace interactive command line interface",
    )
    parser.add_argument("query", nargs="*", help="Question to ask FinTrace")
    parser.add_argument("--session-id", default="SESSION-CLI", help="Session ID")
    parser.add_argument("--json", action="store_true", help="Print full AgentState JSON")
    parser.add_argument("--trace", action="store_true", help="Print execution path, tool calls, and evidence IDs")
    parser.add_argument("--debug-trace", action="store_true", help="Print raw LangGraph node names in trace output")
    parser.add_argument("--api-url", help="Call a running FastAPI service instead of local run_agent")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start an interactive chat loop")
    return parser


def print_answer(
    query: str,
    session_id: str,
    as_json: bool = False,
    show_trace: bool = False,
    debug_trace: bool = False,
    api_url: str | None = None,
) -> None:
    state = call_api(query=query, session_id=session_id, api_url=api_url) if api_url else run_agent(query, session_id=session_id).model_dump()
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
        return

    print()
    print("🤖 FinTrace")
    print(LINE)
    print(format_final_answer(state.get("final_answer") or "未生成回答。"))
    if show_trace:
        print_execution_path(state, debug_trace=debug_trace)
        print_tool_trace(state)
        print_evidence_trace(state)


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
        lines.append("")
        lines.append("限制说明：")
        for item in limitations:
            lines.append(f"- {item}")
    return "\n".join(lines)


def print_execution_path(state: dict, debug_trace: bool = False) -> None:
    print()
    print("🧠 可审计推理路径")
    print(LINE)
    executed_nodes = state.get("executed_nodes") or []
    if executed_nodes:
        for index, node in enumerate(executed_nodes, start=1):
            print(format_trace_node(index=index, node=node, state=state, debug_trace=debug_trace))
        return

    plan = state.get("execution_plan") or {}
    tool_calls = plan.get("tool_calls", [])
    evidence_count = len(state.get("evidence_ledger", []))
    print("1. 路由：识别问题意图，生成工具调用计划")
    if tool_calls:
        for index, call in enumerate(tool_calls, start=2):
            print(f"{index}. 工具：{call.get('tool_name')}，{call.get('reason')}")
        print(f"{len(tool_calls) + 2}. 证据：合并 {evidence_count} 条 evidence_id")
        print(f"{len(tool_calls) + 3}. 生成：调用 Qwen 生成回答；失败则输出警告和结构化摘要")
    else:
        print("2. 工具：未生成工具调用")
        print("3. 生成：未获得可用工具结果")


def format_trace_node(index: int, node: str, state: dict, debug_trace: bool = False) -> str:
    label = TRACE_NODE_LABELS.get(node, node)
    description = dynamic_trace_description(node, state) or TRACE_NODE_DESCRIPTIONS.get(node, "执行 LangGraph 工作流节点")
    status_icon = trace_status_icon(node, state)
    line = f"{index}. {status_icon} {label}：{description}"
    if debug_trace:
        line += f"\n   node: {node}"
    return line


def dynamic_trace_description(node: str, state: dict) -> str:
    tool_history = state.get("tool_call_history") or []
    tool_results = state.get("tool_results", [])
    errors = state.get("errors") or []
    warnings = state.get("warnings") or []
    evidence_count = len(state.get("evidence_ledger", []))
    parsed = state.get("parsed_request") or {}
    pre = state.get("pre_answerability") or {}

    if node == "resolve_request":
        family = parsed.get("task_family") or "unknown"
        entities = parsed.get("entities") or []
        entity_text = "、".join(entities[:3]) if entities else "未识别到主体"
        return f"任务族={family}，主体={entity_text}"
    if node == "check_pre_answerability":
        return f"判定为 {pre.get('status') or 'routeable'}：{pre.get('reason') or '可路由'}"
    if node == "route_mode":
        mode = state.get("routing_mode") or "direct"
        return "确定性直连（无需 LLM 规划）" if mode == "direct" else "进入有界调查循环"
    if node == "plan_next_action":
        action = state.get("current_action") or {}
        if action.get("action") == "finish":
            return "调查队列完成，进入证据收束"
        return f"下一动作：{action.get('tool_name')}.{action.get('operation')}（第 {state.get('step_count', 0)} 步）"
    if node == "execute_one_tool":
        tool_names = [entry.get("tool_name") for entry in tool_history if entry.get("tool_name")]
        return "已调用 " + "、".join(tool_names) if tool_names else "没有可执行工具"
    if node == "validate_tool_result":
        success_count = sum(1 for result in tool_results if result.get("status") == "success")
        failed_count = len(tool_results) - success_count
        if failed_count:
            return f"{success_count} 个工具成功，{failed_count} 个工具失败"
        return f"{success_count} 个工具均成功返回"
    if node == "review_evidence":
        gaps = state.get("evidence_gaps") or []
        status = state.get("answer_status") or ""
        if gaps:
            return f"收集到 {evidence_count} 条证据，仍有 {len(gaps)} 个证据缺口（{status}）"
        return f"收集到 {evidence_count} 条证据，证据链充分（{status}）"
    if node == "generate_answer":
        llm_status = state.get("llm_status")
        if llm_status == "success":
            return "Qwen 已生成基于证据的回答"
        if llm_status == "failed":
            return "Qwen 调用失败，进入结构化错误分支"
        return "调用 Qwen 生成基于证据的金融研判"
    if node == "structured_error":
        if errors:
            error_types = [str(error.get("error_type")) for error in errors if error.get("error_type")]
            return "返回结构化错误：" + "、".join(error_types[:3])
        return "返回结构化错误，避免在失败时编造答案"
    return ""


def trace_status_icon(node: str, state: dict) -> str:
    if node in {"build_refusal", "structured_error"}:
        return "❌"
    if node == "build_clarification":
        return "⚠️"
    if node in {"retry_tools", "evidence_warning"}:
        return "⚠️"
    if node == "generate_answer" and state.get("llm_status") == "failed":
        return "❌"
    return "✅"


def print_tool_trace(state: dict) -> None:
    print()
    print("🛠️ 工具调用")
    print(LINE)
    history = state.get("tool_call_history") or []
    results_by_tool = state.get("tool_results", [])
    if not history:
        print("本轮未调用工具。")
        print()
        return
    for index, entry in enumerate(history, start=1):
        result = results_by_tool[index - 1] if index - 1 < len(results_by_tool) else {}
        print(f"CALL-{index:03d} {entry.get('tool_name')}.{entry.get('operation')}")
        print(f"原因：{entry.get('action_reason')}")
        print(f"参数：{json.dumps(entry.get('arguments') or {}, ensure_ascii=False)}")
        print(f"状态：{entry.get('status', 'unknown')}")
        summary = summarize_tool_result(result)
        if summary:
            print(f"摘要：{summary}")
        warnings = result.get("warnings") or []
        if warnings:
            print("警告：" + "；".join(warnings))
        print()


def summarize_tool_result(result: dict) -> str:
    data = result.get("data") or {}
    tool_name = result.get("tool_name")
    if tool_name == "financial_analysis":
        return (
            f"operation={data.get('operation')}, record_count={data.get('record_count')}, "
            f"comparison_dimension={data.get('comparison_dimension')}"
        )
    if tool_name == "ownership_analysis":
        companies = data.get("companies") or []
        holder_count = sum(
            len(company.get("holders") or company.get("holdings") or []) for company in companies
        )
        return (
            f"operation={data.get('operation')}, direction={data.get('direction')}, "
            f"company_count={len(companies)}, holder_count={holder_count}, "
            f"as_of_date={data.get('as_of_date')}"
        )
    if tool_name == "document_search":
        hits = data.get("hits") or []
        top_ids = [hit.get("evidence_id") for hit in hits[:3]]
        return f"hit_count={len(hits)}, top_evidence_ids={top_ids}"
    if tool_name == "event_timeline":
        clusters = data.get("clusters") or []
        event_types = sorted({cluster.get("event_type") for cluster in clusters if cluster.get("event_type")})
        return f"cluster_count={len(clusters)}, event_types={event_types}"
    return ""


def print_evidence_trace(state: dict) -> None:
    print("📎 证据")
    print(LINE)
    evidence = state.get("evidence_ledger", [])
    if not evidence:
        print("无证据。")
        return
    for item in evidence[:30]:
        print(f"- {item.get('evidence_id')} ({item.get('evidence_type')})")
    if len(evidence) > 30:
        print(f"- ... 另有 {len(evidence) - 30} 条证据")


def call_api(query: str, session_id: str, api_url: str) -> dict:
    endpoint = api_url.rstrip("/") + "/chat"
    payload = json.dumps({"query": query, "session_id": session_id}).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except URLError as exc:
        print(f"无法连接 FastAPI 服务：{api_url}", file=sys.stderr)
        print(r"请先运行：F:\conda_envs\FinTrace\python.exe -m app.api.main", file=sys.stderr)
        raise SystemExit(2) from exc
    return json.loads(body)


def interactive_loop(
    session_id: str,
    as_json: bool = False,
    show_trace: bool = False,
    debug_trace: bool = False,
    api_url: str | None = None,
) -> None:
    qwen = QwenClient()
    print("=" * 64)
    print("🔎 FinTrace 交互式 CLI")
    print("=" * 64)
    print("输入问题后按 Enter，系统会自动路由到财务、股权、文档或事件工具。")
    print("输入 exit 或 quit 退出。启动时加 --trace 可显示可审计推理路径、工具调用和证据。")
    if api_url:
        print(f"运行模式：FastAPI HTTP ({api_url})")
        print("LLM 状态：由 FastAPI 服务端配置决定")
    else:
        print("运行模式：本地 run_agent")
        if qwen.enabled:
            print(f"LLM 状态：✅ Qwen 已启用（{qwen.model}）")
        else:
            print("LLM 状态：⚠️ 未配置 QWEN_API_KEY / DASHSCOPE_API_KEY，将返回结构化错误")
    print(LINE)
    while True:
        try:
            print("🧑 你")
            print(LINE)
            query = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in {"exit", "quit"}:
            print("👋 已退出 FinTrace。")
            break
        if not query:
            continue
        print_answer(
            query,
            session_id=session_id,
            as_json=as_json,
            show_trace=show_trace,
            debug_trace=debug_trace,
            api_url=api_url,
        )
        print()


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    query = " ".join(args.query).strip()

    if args.interactive:
        interactive_loop(
            session_id=args.session_id,
            as_json=args.json,
            show_trace=args.trace,
            debug_trace=args.debug_trace,
            api_url=args.api_url,
        )
        return 0

    if not query:
        interactive_loop(
            session_id=args.session_id,
            as_json=args.json,
            show_trace=args.trace,
            debug_trace=args.debug_trace,
            api_url=args.api_url,
        )
        return 0

    print_answer(
        query,
        session_id=args.session_id,
        as_json=args.json,
        show_trace=args.trace,
        debug_trace=args.debug_trace,
        api_url=args.api_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
