import json

from app import cli
from app.cli import format_final_answer, main


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
    assert "可审计推理路径" in captured.out
    assert "意图识别" in captured.out
    assert "✅" in captured.out
    assert "工具调用" in captured.out
    assert "证据" in captured.out


def test_cli_debug_trace_outputs_raw_node_names(capsys) -> None:
    exit_code = main(["监管问询函有没有关注存货跌价准备", "--trace", "--debug-trace"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "node: route" in captured.out


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
        assert timeout == 60
        return FakeResponse()

    monkeypatch.setattr(cli, "urlopen", fake_urlopen)
    exit_code = main(["问题", "--api-url", "http://127.0.0.1:8000", "--trace"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API answer" in captured.out
    assert "限制说明" in captured.out
    assert "CALL-001" in captured.out


def test_cli_without_query_enters_interactive(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "exit")
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "FinTrace 交互式 CLI" in captured.out
    assert "LLM 状态" in captured.out
    assert "已退出 FinTrace" in captured.out
