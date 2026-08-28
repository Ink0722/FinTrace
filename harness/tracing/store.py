"""SQLite-backed Agent run log used by evaluation, debugging and the frontend."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from schemas.agent_state import AgentState
from harness.runtime_db import connect_runtime, runtime_path


load_dotenv()
SCHEMA_VERSION = "4"
DEFAULT_USER_ID = "USER-DEFAULT"
SHOWCASE_USER_ID = "USER-SHOWCASE"


def observability_path() -> Path:
    """Compatibility name for callers that need the unified runtime path."""
    return runtime_path()


def persist_run(
    state: AgentState, *, run_id: str, trace_id: str, latency_ms: int,
    created_at: str | None = None,
) -> None:
    if not logging_enabled():
        return
    payload = build_run_payload(
        state, run_id=run_id, trace_id=trace_id, latency_ms=latency_ms,
        created_at=created_at,
    )
    with connect() as connection:
        _upsert_payload(connection, payload)


def build_run_payload(
    state: AgentState, *, run_id: str, trace_id: str, latency_ms: int,
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "trace_id": trace_id,
        "session_id": state.session_id,
        "turn_id": state.turn_id,
        "query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "current_context": state.current_context.model_dump(),
        "knowledge_cutoff": state.knowledge_cutoff,
        "routing_mode": state.routing_mode,
        "answer_status": state.answer_status,
        "answer": _answer_text(state.final_answer),
        "final_answer_raw": state.final_answer,
        "termination_reason": state.termination_reason,
        "workflow_status": state.workflow_status,
        "llm_status": state.llm_status,
        "warnings": state.warnings,
        "errors": state.errors,
        "latency_ms": latency_ms,
        "tool_calls": [item.model_dump() for item in state.tool_call_history],
        "tool_results": [item.model_dump() for item in state.tool_results],
        "evidence": [item.model_dump() for item in state.evidence_ledger],
        "evidence_gaps": [item.model_dump() for item in state.evidence_gaps],
        "validation": state.validation_results,
        "failed_actions": state.failed_actions,
        "llm_calls": [item.model_dump() for item in state.llm_calls],
        "executed_nodes": state.executed_nodes,
    }


def list_runs(
    *, session_id: str | None = None, user_id: str | None = None,
    answer_status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    clauses, parameters = [], []
    if session_id:
        clauses.append("session_id = ?")
        parameters.append(session_id)
    if user_id:
        clauses.append("user_id = ?")
        parameters.append(user_id)
    if answer_status:
        clauses.append("answer_status = ?")
        parameters.append(answer_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT run_id, trace_id, user_id, session_id, turn_id, created_at, query, answer,
               answer_status, routing_mode, termination_reason, workflow_status,
               llm_status, latency_ms
        FROM agent_runs {where}
        ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?
    """
    with connect(readonly=True) as connection:
        return [dict(row) for row in connection.execute(sql, [*parameters, limit, offset])]


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect(readonly=True) as connection:
        row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in (
            "parsed_request_json", "current_context_json", "warnings_json", "errors_json",
            "evidence_gaps_json", "validation_json", "failed_actions_json",
        ):
            result[name.removesuffix("_json")] = _loads(result.pop(name))
        result["tool_calls"] = _child_rows(connection, "tool_executions", run_id)
        result["evidence"] = _child_rows(connection, "evidence_records", run_id)
        result["workflow_events"] = _child_rows(connection, "workflow_events", run_id)
        result["llm_calls"] = _child_rows(connection, "llm_executions", run_id)
        return result


def connect(*, readonly: bool = False, path: Path | None = None) -> sqlite3.Connection:
    connection = connect_runtime(readonly=readonly, path=path)
    if not readonly:
        _ensure_schema(connection)
    return connection


def import_payload(payload: dict[str, Any], *, path: Path | None = None) -> None:
    with connect(path=path) as connection:
        _upsert_payload(connection, payload)


def logging_enabled() -> bool:
    return os.getenv("FINTRACE_EVAL_LOG_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS schema_info (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            current_context TEXT NOT NULL DEFAULT '{}',
            conversation_summary TEXT NOT NULL DEFAULT '',
            verified_findings TEXT NOT NULL DEFAULT '[]',
            recent_messages TEXT NOT NULL DEFAULT '[]',
            turn_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            trace_id TEXT UNIQUE,
            session_id TEXT NOT NULL,
            turn_id INTEGER,
            created_at TEXT NOT NULL,
            query TEXT NOT NULL,
            answer TEXT,
            final_answer_raw TEXT,
            answer_status TEXT,
            routing_mode TEXT,
            termination_reason TEXT,
            workflow_status TEXT,
            llm_status TEXT,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            knowledge_cutoff TEXT,
            parsed_request_json TEXT,
            current_context_json TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            errors_json TEXT NOT NULL DEFAULT '[]',
            evidence_gaps_json TEXT NOT NULL DEFAULT '[]',
            validation_json TEXT NOT NULL DEFAULT '[]',
            failed_actions_json TEXT NOT NULL DEFAULT '[]',
            legacy INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_runs_session_turn ON agent_runs(session_id, turn_id);
        CREATE INDEX IF NOT EXISTS idx_runs_created_at ON agent_runs(created_at DESC);
        CREATE TABLE IF NOT EXISTS tool_executions (
            run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            tool_call_id TEXT,
            tool_name TEXT,
            operation TEXT,
            status TEXT,
            reason TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS evidence_records (
            run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            evidence_id TEXT NOT NULL,
            tool_call_id TEXT,
            evidence_type TEXT,
            support_level TEXT,
            source_json TEXT NOT NULL DEFAULT '{}',
            fact_json TEXT NOT NULL DEFAULT '{}',
            used_by_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT,
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS workflow_events (
            run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            node_name TEXT,
            event_type TEXT NOT NULL,
            status TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS llm_executions (
            run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            prompt_id TEXT,
            prompt_version TEXT,
            model TEXT,
            status TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            avatar_color TEXT NOT NULL DEFAULT '#078b98',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            immutable INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_user_sessions_user_updated
            ON user_sessions(user_id, updated_at DESC);
    """)
    run_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
    if "user_id" not in run_columns:
        connection.execute(
            "ALTER TABLE agent_runs ADD COLUMN user_id TEXT NOT NULL DEFAULT 'USER-DEFAULT'"
        )
    session_columns = {row[1] for row in connection.execute("PRAGMA table_info(user_sessions)")}
    if "immutable" not in session_columns:
        connection.execute(
            "ALTER TABLE user_sessions ADD COLUMN immutable INTEGER NOT NULL DEFAULT 0"
        )
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT OR IGNORE INTO users(user_id, display_name, avatar_color, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (DEFAULT_USER_ID, "本地用户", "#078b98", now, now),
    )
    connection.execute(
        "INSERT OR IGNORE INTO users(user_id, display_name, avatar_color, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (SHOWCASE_USER_ID, "FinTrace 展示", "#078b98", now, now),
    )
    connection.execute("UPDATE agent_runs SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (DEFAULT_USER_ID,))
    connection.execute("""
        INSERT OR IGNORE INTO user_sessions(
            session_id, user_id, title, created_at, updated_at, immutable
        )
        SELECT session_id, ?, COALESCE(NULLIF(MIN(query), ''), '历史会话'),
               MIN(created_at), MAX(created_at), 0
        FROM agent_runs GROUP BY session_id
    """, (DEFAULT_USER_ID,))
    connection.execute(
        "INSERT OR REPLACE INTO schema_info(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )


def _upsert_payload(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
    run_id = str(payload["run_id"])
    session_id = payload.get("session_id") or "UNKNOWN"
    owner = connection.execute(
        "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,),
    ).fetchone()
    user_id = str(payload.get("user_id") or (owner["user_id"] if owner else DEFAULT_USER_ID))
    connection.execute("""
        INSERT OR REPLACE INTO agent_runs (
            run_id, trace_id, user_id, session_id, turn_id, created_at, query, answer,
            final_answer_raw, answer_status, routing_mode, termination_reason,
            workflow_status, llm_status, latency_ms, knowledge_cutoff,
            parsed_request_json, current_context_json, warnings_json, errors_json,
            evidence_gaps_json, validation_json, failed_actions_json, legacy
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        run_id, payload.get("trace_id"), user_id, session_id,
        payload.get("turn_id"), payload.get("created_at") or datetime.now(UTC).isoformat(),
        payload.get("query") or payload.get("user_query") or "", payload.get("answer") or "",
        payload.get("final_answer_raw") or payload.get("final_answer"), payload.get("answer_status"),
        payload.get("routing_mode"), payload.get("termination_reason"), payload.get("workflow_status"),
        payload.get("llm_status"), int(payload.get("latency_ms") or 0), payload.get("knowledge_cutoff"),
        _dumps(payload.get("parsed_request")), _dumps(payload.get("current_context")),
        _dumps(payload.get("warnings") or []), _dumps(payload.get("errors") or []),
        _dumps(payload.get("evidence_gaps") or []), _dumps(payload.get("validation") or []),
        _dumps(payload.get("failed_actions") or []), int(bool(payload.get("legacy"))),
    ))
    now = payload.get("created_at") or datetime.now(UTC).isoformat()
    connection.execute(
        "UPDATE user_sessions SET updated_at = ?, title = CASE WHEN title = '新会话' "
        "THEN ? ELSE title END WHERE session_id = ?",
        (now, (payload.get("query") or "新会话")[:80], session_id),
    )
    for table in ("tool_executions", "evidence_records", "workflow_events", "llm_executions"):
        connection.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
    _insert_tools(connection, run_id, payload)
    _insert_evidence(connection, run_id, payload.get("evidence") or [])
    _insert_events(connection, run_id, payload)
    _insert_llm_calls(connection, run_id, payload.get("llm_calls") or [])


def _insert_tools(connection: sqlite3.Connection, run_id: str, payload: dict[str, Any]) -> None:
    calls = payload.get("tool_calls") or payload.get("planner_actions") or []
    results = payload.get("tool_results") or payload.get("tool_results_summary") or []
    for sequence, call in enumerate(calls, 1):
        result = results[sequence - 1] if sequence <= len(results) else {}
        metrics = result.get("metrics") or {}
        connection.execute("""
            INSERT INTO tool_executions VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_id, sequence, call.get("tool_call_id"), call.get("tool_name"),
            call.get("operation") or (call.get("arguments") or {}).get("operation"),
            call.get("status") or result.get("status"), call.get("action_reason") or call.get("reason"),
            int(metrics.get("execution_time_ms") or 0), _dumps(call.get("arguments") or {}),
            _dumps(result), _dumps(call.get("evidence_ids") or []),
        ))


def _insert_evidence(connection: sqlite3.Connection, run_id: str, evidence: list[dict]) -> None:
    for sequence, item in enumerate(evidence, 1):
        used_by = item.get("used_by") or []
        connection.execute("INSERT INTO evidence_records VALUES (?,?,?,?,?,?,?,?,?,?)", (
            run_id, sequence, item.get("evidence_id") or f"UNKNOWN-{sequence}",
            used_by[0] if used_by else None, item.get("evidence_type"), item.get("support_level"),
            _dumps(item.get("source") or {}), _dumps(item.get("fact") or {}),
            _dumps(used_by), str(item.get("created_at")) if item.get("created_at") else None,
        ))


def _insert_events(connection: sqlite3.Connection, run_id: str, payload: dict[str, Any]) -> None:
    for sequence, node in enumerate(payload.get("executed_nodes") or [], 1):
        connection.execute("INSERT INTO workflow_events VALUES (?,?,?,?,?,?)", (
            run_id, sequence, node, "node.completed", "completed", "{}",
        ))


def _insert_llm_calls(connection: sqlite3.Connection, run_id: str, calls: list[dict]) -> None:
    for sequence, item in enumerate(calls, 1):
        connection.execute("INSERT INTO llm_executions VALUES (?,?,?,?,?,?,?)", (
            run_id, sequence, item.get("prompt_id"), item.get("prompt_version"),
            item.get("model"), item.get("status"), _dumps(item),
        ))


def _child_rows(connection: sqlite3.Connection, table: str, run_id: str) -> list[dict]:
    rows = [dict(row) for row in connection.execute(
        f"SELECT * FROM {table} WHERE run_id = ? ORDER BY sequence", (run_id,),
    )]
    for row in rows:
        row.pop("run_id", None)
        for key in list(row):
            if key.endswith("_json"):
                row[key.removesuffix("_json")] = _loads(row.pop(key))
    return rows


def _answer_text(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    return str(parsed.get("answer") or raw) if isinstance(parsed, dict) else raw


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    return json.loads(value) if value else None
