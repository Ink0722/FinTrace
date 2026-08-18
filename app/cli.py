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
    "route": "意图识别",
    "validate_plan": "计划校验",
    "plan_error": "计划错误",
    "execute_tools": "工具执行",
    "validate_tool_results": "结果校验",
    "retry_tools": "工具重试",
    "tool_error": "工具错误",
    "check_evidence": "证据检查",
    "evidence_warning": "证据提醒",
    "generate_answer": "答案生成",
    "structured_error": "错误收束",
}

TRACE_NODE_DESCRIPTIONS = {
    "route": "识别问题类型，判断需要调用哪些金融工具",
    "validate_plan": "检查工具调用计划是否完整、参数是否合法",
    "plan_error": "工具调用计划未通过校验，停止继续执行",
    "execute_tools": "按计划调用财务、股权、文档或事件工具",
    "validate_tool_results": "检查工具是否成功返回，并判断是否需要重试",
    "retry_tools": "对可重试的工具失败执行一次重试",
    "tool_error": "工具失败且无法继续重试，进入错误处理",
    "check_evidence": "检查工具结果是否包含可追溯证据",
    "evidence_warning": "证据存在缺口，后续回答必须提示限制",
    "generate_answer": "调用 Qwen 生成基于证据的金融研判",
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
    limitations = parsed.get("limitations") or []
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
    plan = state.get("execution_plan") or {}
    tool_calls = plan.get("tool_calls", [])
    tool_results = state.get("tool_results", [])
    errors = state.get("errors") or []
    warnings = state.get("warnings") or []
    evidence_count = len(state.get("evidence_ledger", []))

    if node == "route":
        intent = (state.get("user_request") or {}).get("intent") or "unknown"
        return f"识别为 {intent}，生成 {len(tool_calls)} 个工具调用"
    if node == "validate_plan":
        return f"工具计划通过校验，共 {len(tool_calls)} 个工具调用" if tool_calls else "未生成工具调用计划"
    if node == "execute_tools":
        tool_names = [call.get("tool_name") for call in tool_calls if call.get("tool_name")]
        return "调用 " + "、".join(tool_names) if tool_names else "没有可执行工具"
    if node == "validate_tool_results":
        success_count = sum(1 for result in tool_results if result.get("status") == "success")
        failed_count = len(tool_results) - success_count
        if failed_count:
            return f"{success_count} 个工具成功，{failed_count} 个工具失败"
        return f"{success_count} 个工具均成功返回"
    if node == "retry_tools":
        retry_count = sum(1 for result in tool_results if result.get("retry_count", 0) > 0)
        return f"已重试 {retry_count} 个可恢复失败的工具调用"
    if node == "check_evidence":
        return f"收集到 {evidence_count} 条证据，检查证据链是否充分"
    if node == "evidence_warning":
        return f"发现 {len(warnings)} 条证据限制，最终回答需要显式提示"
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
    if node in {"plan_error", "tool_error", "structured_error"}:
        return "❌"
    if node in {"retry_tools", "evidence_warning"}:
        return "⚠️"
    if node == "generate_answer" and state.get("llm_status") == "failed":
        return "❌"
    return "✅"


def print_tool_trace(state: dict) -> None:
    print()
    print("🛠️ 工具调用")
    print(LINE)
    plan = state.get("execution_plan") or {}
    results_by_id = {result.get("tool_call_id"): result for result in state.get("tool_results", [])}
    for call in plan.get("tool_calls", []):
        result = results_by_id.get(call.get("tool_call_id"), {})
        print(f"{call.get('tool_call_id')} {call.get('tool_name')}")
        print(f"原因：{call.get('reason')}")
        print(f"参数：{json.dumps(call.get('arguments') or {}, ensure_ascii=False)}")
        print(f"状态：{result.get('status', 'unknown')}")
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
