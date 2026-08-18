import pytest


@pytest.fixture(autouse=True)
def disable_real_llm_calls(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_PLANNER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_PLANNER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("FINTRACE_OWNERSHIP_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("FINTRACE_OWNERSHIP_INDEX_PATH", raising=False)
    monkeypatch.delenv("FINTRACE_FINANCIAL_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("FINTRACE_FINANCIAL_INDEX_PATH", raising=False)
    monkeypatch.delenv("EVENT_DATA_SOURCE", raising=False)
    monkeypatch.delenv("EVENTS_PATH", raising=False)
