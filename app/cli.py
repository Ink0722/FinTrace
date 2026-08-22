import argparse
import json
import sys
import time
from collections.abc import Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.cli_render import LINE, format_final_answer, print_compact_footer, print_session_status, print_trace
from harness.graph.workflow import run_agent
from harness.llm import QwenClient


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except (TypeError, ValueError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fintrace", description="FinTrace interactive command line interface")
    parser.add_argument("query", nargs="*", help="Question to ask FinTrace")
    parser.add_argument("--session-id", default="SESSION-CLI", help="Session ID")
    parser.add_argument("--json", action="store_true", help="Machine-readable AgentState output (development only)")
    parser.add_argument("--trace", action="store_true", help="Show parsed request, routing, tool results and evidence")
    parser.add_argument("--debug-trace", action="store_true", help="Also show every LangGraph node")
    parser.add_argument("--api-url", help="Call a running FastAPI service instead of local run_agent")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start a multi-turn interactive session")
    return parser


def ask_agent(query: str, session_id: str, api_url: str | None = None) -> tuple[dict, int]:
    started = time.perf_counter()
    state = (
        call_api(query=query, session_id=session_id, api_url=api_url)
        if api_url
        else run_agent(query, session_id=session_id).model_dump()
    )
    return state, max(0, round((time.perf_counter() - started) * 1000))


def print_answer(
    query: str,
    session_id: str,
    as_json: bool = False,
    show_trace: bool = False,
    debug_trace: bool = False,
    api_url: str | None = None,
) -> dict:
    state, elapsed_ms = ask_agent(query, session_id, api_url)
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
        return state
    print("\n🤖 FinTrace")
    print(LINE)
    print(format_final_answer(state.get("final_answer") or "未生成回答。"))
    print_compact_footer(state, elapsed_ms)
    if show_trace or debug_trace:
        print_trace(state, debug=debug_trace)
    return state


def call_api(query: str, session_id: str, api_url: str) -> dict:
    endpoint = api_url.rstrip("/") + "/chat"
    payload = json.dumps({"query": query, "session_id": session_id}).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except URLError as exc:
        print(f"❌ 无法连接 FastAPI 服务：{api_url}", file=sys.stderr)
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
    if as_json:
        raise SystemExit("交互模式不支持 --json；请使用默认可读界面或单轮机器输出。")
    qwen = QwenClient()
    current_session, last_state, turn = session_id, None, 1
    print("=" * 72)
    print("🔎 FinTrace 多轮金融问答")
    print("=" * 72)
    print("直接输入问题；/help 查看命令；exit 或 quit 退出。")
    print(f"运行模式：{'FastAPI ' + api_url if api_url else '本地 run_agent'}")
    print(f"会话 ID：{current_session}")
    if not api_url:
        print(f"LLM：{'✅ ' + qwen.model if qwen.enabled else '⚠️ 未配置，将返回结构化错误'}")
    print(LINE)
    while True:
        try:
            query = input(f"[{turn}] 🧑 你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        command = query.lower()
        if command in {"exit", "quit"}:
            print("👋 已退出 FinTrace。")
            break
        if not query:
            continue
        if command == "/help":
            print_help()
            continue
        if command == "/status":
            print_session_status(last_state, current_session, show_trace, debug_trace)
            continue
        if command == "/clear":
            current_session, last_state, turn = f"SESSION-CLI-{uuid4().hex[:8].upper()}", None, 1
            print(f"🧹 已开启新会话：{current_session}")
            continue
        if command in {"/trace on", "/trace off"}:
            show_trace = command.endswith("on")
            if not show_trace:
                debug_trace = False
            print(f"🧭 Trace 已{'开启' if show_trace else '关闭'}。")
            continue
        if command in {"/debug on", "/debug off"}:
            debug_trace = command.endswith("on")
            show_trace = show_trace or debug_trace
            print(f"🧩 Debug Trace 已{'开启' if debug_trace else '关闭'}。")
            continue
        last_state = print_answer(
            query,
            session_id=current_session,
            show_trace=show_trace,
            debug_trace=debug_trace,
            api_url=api_url,
        )
        turn = int(last_state.get("turn_id") or turn) + 1
        print()


def print_help() -> None:
    print("\n💡 会话命令")
    print(LINE)
    print("/status      查看当前会话、主体、期间和 Trace 状态")
    print("/trace on    开启可读的请求、工具、证据流程")
    print("/trace off   关闭流程展示")
    print("/debug on    展示完整 LangGraph 节点")
    print("/debug off   关闭完整节点展示")
    print("/clear       开启一个不继承旧上下文的新会话")
    print("exit / quit  退出 FinTrace")


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    query = " ".join(args.query).strip()
    if args.interactive or not query:
        interactive_loop(
            session_id=args.session_id,
            as_json=args.json,
            show_trace=args.trace or args.debug_trace,
            debug_trace=args.debug_trace,
            api_url=args.api_url,
        )
        return 0
    print_answer(
        query,
        session_id=args.session_id,
        as_json=args.json,
        show_trace=args.trace or args.debug_trace,
        debug_trace=args.debug_trace,
        api_url=args.api_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
