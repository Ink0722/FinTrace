from harness.llm import QwenClient
from harness.graph.workflow import run_agent

import requests


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
    state = run_agent("分析一下示例公司的财务风险", session_id="TEST-LLM-TIMEOUT")
    assert state.final_answer is not None
    assert "LLM 生成失败" in state.final_answer
    assert "不补充模型推断" in state.final_answer
