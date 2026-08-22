import json

from app import cli
from app.cli import format_final_answer, main
from app.cli_render import render_tool_result


def test_format_final_answer_unwraps_qwen_json() -> None:
    raw = json.dumps(
        {
            "answer": "这是正式回答。",
            "limitations": ["限制一", "限制二"],
        },
        ensure_ascii=False,
    )
    formatted = format_final_answer(raw)
    assert "这是正式回答。" in formatted
    assert "限制说明" in formatted
    assert "限制一" in formatted
    assert not formatted.strip().startswith("{")


def test_cli_single_query_runs(capsys) -> None:
    exit_code = main(["分析一下示例公司的财务风险"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "FinTrace" in captured.out


def test_cli_trace_outputs_execution_path_tool_calls_and_evidence(capsys) -> None:
    exit_code = main(["监管问询函有没有关注存货跌价准备", "--trace"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "请求解析" in captured.out
    assert "路由与执行摘要" in captured.out
    assert "工具调用与结果" in captured.out
    assert "关键证据" in captured.out
    assert "参数：{" not in captured.out


def test_cli_debug_trace_outputs_raw_node_names(capsys) -> None:
    exit_code = main(["监管问询函有没有关注存货跌价准备", "--trace", "--debug-trace"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "node: resolve_request" in captured.out


def test_cli_api_mode_uses_http(monkeypatch, capsys) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "final_answer": json.dumps({"answer": "API answer", "limitations": ["API limitation"]}, ensure_ascii=False),
                    "tool_call_history": [
                        {
                            "tool_name": "document_search",
                            "operation": "search",
                            "arguments": {"query": "问题"},
                            "status": "success",
                            "evidence_ids": ["EVID-001"],
                            "action_reason": "test",
                        }
                    ],
                    "execution_plan": {
                        "tool_calls": [
                            {
                                "tool_call_id": "CALL-001",
                                "tool_name": "document_search",
                                "reason": "test",
                                "arguments": {"query": "问题"},
                            }
                        ]
                    },
                    "tool_results": [
                        {
                            "tool_call_id": "CALL-001",
                            "tool_name": "document_search",
                            "status": "success",
                            "data": {"hits": [{"evidence_id": "EVID-001"}]},
                        }
                    ],
                    "evidence_ledger": [{"evidence_id": "EVID-001", "evidence_type": "document_chunk"}],
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:8000/chat"
        assert timeout == 180
        return FakeResponse()

    monkeypatch.setattr(cli, "urlopen", fake_urlopen)
    exit_code = main(["问题", "--api-url", "http://127.0.0.1:8000", "--trace"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API answer" in captured.out
    assert "限制说明" in captured.out
    assert "工具 1：文档检索" in captured.out
    assert "问题/检索词" in captured.out


def test_cli_without_query_enters_interactive(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "exit")
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "FinTrace 多轮金融问答" in captured.out
    assert "LLM" in captured.out
    assert "已退出 FinTrace" in captured.out


def test_interactive_commands_toggle_trace_and_clear(monkeypatch, capsys) -> None:
    inputs = iter(["/status", "/trace on", "/debug on", "/clear", "/help", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    assert main(["--interactive", "--session-id", "TEST-CLI-MULTI"]) == 0
    output = capsys.readouterr().out
    assert "当前会话" in output
    assert "Trace 已开启" in output
    assert "Debug Trace 已开启" in output
    assert "已开启新会话" in output
    assert "会话命令" in output


def test_ownership_ratio_is_rendered_as_percentage(capsys) -> None:
    render_tool_result(
        "ownership_analysis",
        {
            "data": {
                "direction": "company_to_holders",
                "companies": [
                    {
                        "company_id": "600519.SH",
                        "snapshot": {"holder_end_date": "2026-03-31"},
                        "holders": [{"holder_name": "测试股东", "holding_ratio": 0.544}],
                    }
                ],
            },
            "evidence": [],
        },
        indent=2,
    )
    output = capsys.readouterr().out
    assert "54.40%" in output
    assert "0.54%" not in output


def test_risk_scan_is_rendered_without_raw_json(capsys) -> None:
    render_tool_result(
        "financial_analysis",
        {
            "data": {
                "operation": "risk_scan",
                "coverage": {"evaluated_rule_count": 1, "requested_rule_count": 2},
                "signals": [
                    {"rule_id": "CASH_PROFIT_DIVERGENCE", "name": "利润与经营现金流背离", "status": "triggered", "severity": "medium"},
                    {"rule_id": "LIQUIDITY_PRESSURE", "name": "短期偿债压力", "status": "insufficient_data", "severity": None},
                ],
                "rules_skipped": [{"rule_id": "LIQUIDITY_PRESSURE"}],
            },
            "evidence": [],
        },
        indent=2,
    )
    output = capsys.readouterr().out
    assert "规则覆盖" in output
    assert "triggered / medium" in output
    assert "{'rule_id'" not in output


def test_penetration_path_is_rendered_hop_by_hop(capsys) -> None:
    render_tool_result(
        "ownership_analysis",
        {
            "data": {
                "operation": "penetration",
                "paths": [
                    {
                        "path_id": "PATH-001",
                        "depth": 2,
                        "path_ratio": 0.0065,
                        "edges": [
                            {"source_name": "主体甲", "target_name": "中间公司", "holding_ratio": 0.05, "holder_end_date": "2024-12-31"},
                            {"source_name": "中间公司", "target_name": "目标公司", "holding_ratio": 0.13, "holder_end_date": "2024-12-31"},
                        ],
                    }
                ],
            },
            "evidence": [],
        },
        indent=2,
    )
    output = capsys.readouterr().out
    assert "0.6500%" in output
    assert "主体甲 -> 中间公司" in output
    assert "中间公司 -> 目标公司" in output
