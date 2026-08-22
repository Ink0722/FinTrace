from harness.llm import QwenClient
from harness.skills import client_for_skill
from harness.graph.workflow import run_agent
from schemas.enums import ToolStatus
from schemas.tool_results import ToolResult
from harness.streaming import JsonAnswerDeltaParser

import requests


def test_json_answer_delta_parser_hides_json_envelope() -> None:
    parser = JsonAnswerDeltaParser()
    chunks = ['{"ans', 'wer":"第一行\\n', '第二行","used_evidence_ids":[]}']
    deltas = [parser.feed(chunk) for chunk in chunks]
    assert "".join(deltas) == "第一行\n第二行"
    assert "answer" not in "".join(deltas)


def test_qwen_json_stream_yields_content_deltas(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "test-key")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=False):
            assert decode_unicode is True
            return iter([
                'data: {"choices":[{"delta":{"content":"{\\\"answer\\\":\\\"你"}}]}',
                'data: {"choices":[{"delta":{"content":"好\\\"}"}}]}',
                "data: [DONE]",
            ])

    monkeypatch.setattr("harness.llm.requests.post", lambda *args, **kwargs: Response())
    assert "".join(QwenClient().chat_json_stream([{"role": "user", "content": "json"}])) == '{"answer":"你好"}'


def test_qwen_client_accepts_dashscope_env_names(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("QWEN_CHAT_MODEL", "qwen-custom")
    client = QwenClient()
    assert client.enabled
    assert client.api_key == "test-key"
    assert client.model == "qwen-custom"


def test_qwen_client_prefers_qwen_env_names(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("QWEN_MODEL", "qwen-model")
    monkeypatch.setenv("QWEN_CHAT_MODEL", "dashscope-model")
    client = QwenClient()
    assert client.api_key == "qwen-key"
    assert client.model == "qwen-model"


def test_planner_client_uses_independent_env_names(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "main-key")
    monkeypatch.setenv("QWEN_MODEL", "main-model")
    monkeypatch.setenv("QWEN_PLANNER_API_KEY", "planner-key")
    monkeypatch.setenv("QWEN_PLANNER_BASE_URL", "https://planner.example/v1")
    monkeypatch.setenv("QWEN_PLANNER_MODEL", "planner-model")
    client = QwenClient.for_planner()
    assert client.api_key == "planner-key"
    assert client.base_url == "https://planner.example/v1"
    assert client.model == "planner-model"
    assert client_for_skill("next_action_planner").model == "planner-model"
    assert client_for_skill("evidence_reviewer").model == "planner-model"
    assert client_for_skill("final_answer").model == "main-model"


def test_missing_llm_config_returns_error_not_deterministic_answer(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    state = run_agent("analyze financial risk", session_id="TEST-NO-LLM")
    assert state.final_answer is not None
    assert "LLM_NOT_CONFIGURED" in state.final_answer
    assert "不会使用确定性模板" in state.final_answer


def test_llm_timeout_warns_without_crashing(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "test-key")

    def raise_timeout(*args, **kwargs):
        raise requests.ReadTimeout("timeout for test")

    monkeypatch.setattr("harness.llm.requests.post", raise_timeout)
    monkeypatch.setattr(
        "harness.graph.nodes.execute_tool",
        lambda call: ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            status=ToolStatus.SUCCESS,
        ),
    )
    state = run_agent("分析示例公司2023年和2024年的财务风险", session_id="TEST-LLM-TIMEOUT")
    assert state.final_answer is not None
    assert "LLM 生成失败" in state.final_answer
    assert "不补充模型推断" in state.final_answer
