from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def isolated_session_store(monkeypatch, tmp_path):
    """Every test gets its own session database; never touch data/sessions."""
    monkeypatch.setenv("FINTRACE_SESSIONS_PATH", str(tmp_path / f"sessions_{uuid4().hex}.sqlite"))
    monkeypatch.setenv(
        "FINTRACE_OBSERVABILITY_DB", str(tmp_path / f"observability_{uuid4().hex}.sqlite3")
    )
    yield


@pytest.fixture(autouse=True)
def disable_real_llm_calls(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_PLANNER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_PLANNER_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("FINTRACE_BM25_INDEX_PATH", raising=False)
    monkeypatch.delenv("FINTRACE_KNOWLEDGE_CUTOFF", raising=False)
    monkeypatch.delenv("FINTRACE_ENTITY_ALIAS_INDEX_PATH", raising=False)
    monkeypatch.delenv("FINTRACE_OWNERSHIP_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("FINTRACE_OWNERSHIP_INDEX_PATH", raising=False)
    monkeypatch.delenv("FINTRACE_FINANCIAL_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("FINTRACE_FINANCIAL_INDEX_PATH", raising=False)
    monkeypatch.delenv("FINTRACE_EVENT_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("FINTRACE_EVENT_INDEX_PATH", raising=False)
