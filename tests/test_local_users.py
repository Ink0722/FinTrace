import pytest
from fastapi import HTTPException

from app.api.main import ChatRequest, _claim_chat_session
from harness.tracing.users import (
    claim_session, create_user, delete_session, delete_user, list_user_sessions, list_users,
    rename_session, update_user,
)
from harness.tracing.store import connect, import_payload


def test_local_user_lifecycle_and_session_ownership(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "users.sqlite3"))
    assert list_users()[0]["user_id"] == "USER-DEFAULT"

    first = create_user("研究员 A")
    second = create_user("研究员 B")
    renamed = update_user(first["user_id"], "研究员甲")
    assert renamed["display_name"] == "研究员甲"

    claim_session(first["user_id"], "SESSION-A", "茅台分析")
    assert list_user_sessions(first["user_id"])[0]["session_id"] == "SESSION-A"
    with pytest.raises(PermissionError):
        claim_session(second["user_id"], "SESSION-A")

    assert delete_user(first["user_id"])
    assert not delete_user("USER-DEFAULT")


def test_chat_request_rejects_cross_user_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "ownership.sqlite3"))
    first = create_user("甲")
    second = create_user("乙")
    claim_session(first["user_id"], "SHARED-SESSION")
    with pytest.raises(HTTPException) as exc_info:
        _claim_chat_session(ChatRequest(
            query="测试", session_id="SHARED-SESSION", user_id=second["user_id"],
        ))
    assert exc_info.value.status_code == 409


def test_delete_session_removes_memory_run_and_trace_children(monkeypatch, tmp_path) -> None:
    database = tmp_path / "delete-session.sqlite3"
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(database))
    user = create_user("评测用户")
    claim_session(user["user_id"], "SESSION-EVAL", "评测会话")
    import_payload({
        "run_id": "RUN-DELETE",
        "trace_id": "TRACE-DELETE",
        "user_id": user["user_id"],
        "session_id": "SESSION-EVAL",
        "turn_id": 1,
        "query": "测试问题",
        "answer": "测试回答",
        "tool_calls": [{"tool_call_id": "TOOL-1", "tool_name": "document_search", "operation": "search"}],
        "tool_results": [{"tool_call_id": "TOOL-1", "status": "success"}],
        "evidence": [{"evidence_id": "E-1", "evidence_type": "document"}],
        "workflow_events": [{"event_type": "node.completed", "node_name": "test"}],
        "llm_calls": [{"prompt_id": "06", "status": "success"}],
    })
    with connect() as connection:
        connection.execute(
            "INSERT INTO sessions(session_id, updated_at) VALUES (?, ?)",
            ("SESSION-EVAL", "2026-05-28T00:00:00+00:00"),
        )

    assert delete_session(user["user_id"], "SESSION-EVAL")
    assert list_user_sessions(user["user_id"]) == []
    with connect() as connection:
        for table in (
            "sessions", "agent_runs", "tool_executions", "evidence_records",
            "workflow_events", "llm_executions", "user_sessions",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_delete_session_enforces_ownership(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "delete-ownership.sqlite3"))
    owner = create_user("所有者")
    other = create_user("其他用户")
    claim_session(owner["user_id"], "SESSION-PRIVATE")

    with pytest.raises(PermissionError):
        delete_session(other["user_id"], "SESSION-PRIVATE")
    assert delete_session(owner["user_id"], "SESSION-PRIVATE")
    assert not delete_session(owner["user_id"], "SESSION-PRIVATE")


def test_rename_session_persists_title_without_changing_activity_time(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "rename-session.sqlite3"))
    owner = create_user("所有者")
    other = create_user("其他用户")
    claim_session(owner["user_id"], "SESSION-RENAME", "旧标题")
    before = list_user_sessions(owner["user_id"])[0]

    renamed = rename_session(owner["user_id"], "SESSION-RENAME", "  新标题  ")
    assert renamed["title"] == "新标题"
    assert renamed["updated_at"] == before["updated_at"]
    assert list_user_sessions(owner["user_id"])[0]["title"] == "新标题"

    with pytest.raises(PermissionError):
        rename_session(other["user_id"], "SESSION-RENAME", "越权标题")
    with pytest.raises(ValueError):
        rename_session(owner["user_id"], "SESSION-RENAME", "  ")
    assert rename_session(owner["user_id"], "MISSING", "不存在") is None
