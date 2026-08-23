import pytest
from fastapi import HTTPException

from app.api.main import ChatRequest, _claim_chat_session
from harness.tracing.users import (
    claim_session, create_user, delete_session, delete_user, get_user_session_detail,
    list_user_sessions, list_users, rename_session, update_user,
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


def test_session_summary_is_lightweight_and_detail_restores_trace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "session-detail.sqlite3"))
    owner = create_user("研究员")
    other = create_user("其他用户")
    claim_session(owner["user_id"], "SESSION-RICH", "完整历史")
    import_payload({
        "run_id": "RUN-RICH", "trace_id": "TRACE-RICH",
        "user_id": owner["user_id"], "session_id": "SESSION-RICH", "turn_id": 1,
        "query": "问题", "answer": "回答", "answer_status": "answered",
        "tool_calls": [{
            "tool_call_id": "CALL-1", "tool_name": "financial_analysis",
            "operation": "metric_query", "arguments": {"company_ids": ["600519.SH"]},
            "status": "success", "action_reason": "查询财务指标",
        }],
        "tool_results": [{
            "tool_call_id": "CALL-1", "tool_name": "financial_analysis",
            "status": "success", "data": {"record_count": 1},
            "metrics": {"execution_time_ms": 12},
        }],
        "evidence": [{
            "evidence_id": "E-1", "evidence_type": "financial_statement_metric",
            "support_level": "direct", "source": {"company_id": "600519.SH"},
            "fact": {"metric_code": "REVENUE", "value": 100}, "used_by": ["CALL-1"],
        }],
        "executed_nodes": ["load_session", "execute_one_tool", "generate_answer"],
        "llm_calls": [{"prompt_id": "fintrace.final_answer", "status": "success"}],
    })

    summary = list_user_sessions(owner["user_id"])[0]
    assert summary["turn_count"] == 1
    assert summary["last_message"] == "回答"
    assert "messages" not in summary

    detail = get_user_session_detail(owner["user_id"], "SESSION-RICH")
    run = detail["runs"][0]
    assert run["query"] == "问题"
    assert run["tool_calls"][0]["arguments"]["company_ids"] == ["600519.SH"]
    assert run["tool_calls"][0]["result"]["data"]["record_count"] == 1
    assert run["evidence"][0]["fact"]["metric_code"] == "REVENUE"
    assert len(run["workflow_events"]) == 3
    assert run["llm_calls"][0]["prompt_id"] == "fintrace.final_answer"

    with pytest.raises(PermissionError):
        get_user_session_detail(other["user_id"], "SESSION-RICH")
    assert get_user_session_detail(owner["user_id"], "MISSING") is None
