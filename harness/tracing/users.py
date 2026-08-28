"""Local user workspaces and their session ownership."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from harness.tracing.store import DEFAULT_USER_ID, connect


class ReadOnlySessionError(PermissionError):
    """Raised when a mutation targets an immutable showcase session."""


def list_users() -> list[dict]:
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT user_id, display_name, avatar_color, created_at, updated_at "
            "FROM users ORDER BY created_at, user_id"
        )]


def create_user(display_name: str, avatar_color: str = "#078b98") -> dict:
    now = datetime.now(UTC).isoformat()
    user_id = f"USER-{uuid4().hex[:12].upper()}"
    with connect() as connection:
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
            (user_id, display_name.strip(), avatar_color, now, now),
        )
    return get_user(user_id)


def ensure_user(user_id: str, display_name: str, avatar_color: str = "#078b98") -> dict:
    existing = get_user(user_id)
    if existing:
        return existing
    now = datetime.now(UTC).isoformat()
    with connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?)",
            (user_id, display_name.strip(), avatar_color, now, now),
        )
    return get_user(user_id)


def get_user(user_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_user(user_id: str, display_name: str, avatar_color: str | None = None) -> dict | None:
    now = datetime.now(UTC).isoformat()
    with connect() as connection:
        connection.execute(
            "UPDATE users SET display_name = ?, avatar_color = COALESCE(?, avatar_color), "
            "updated_at = ? WHERE user_id = ?",
            (display_name.strip(), avatar_color, now, user_id),
        )
    return get_user(user_id)


def delete_user(user_id: str) -> bool:
    if user_id == DEFAULT_USER_ID:
        return False
    with connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count <= 1:
            return False
        exists = connection.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if exists is None:
            return False
        session_ids = [row["session_id"] for row in connection.execute(
            "SELECT session_id FROM user_sessions WHERE user_id = ?", (user_id,),
        )]
        # Agent child records cascade from agent_runs; remove the local workspace as one unit.
        connection.execute("DELETE FROM agent_runs WHERE user_id = ?", (user_id,))
        connection.executemany(
            "DELETE FROM sessions WHERE session_id = ?",
            [(session_id,) for session_id in session_ids],
        )
        cursor = connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0


def delete_session(user_id: str, session_id: str) -> bool:
    """Delete one owned session and all of its persisted runtime records."""
    with connect() as connection:
        owner = connection.execute(
            "SELECT user_id, immutable FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner is None:
            return False
        if owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        if owner["immutable"]:
            raise ReadOnlySessionError("Read-only showcase sessions cannot be deleted")

        # Child trace records cascade from agent_runs. Keep all three roots in
        # this transaction so a session cannot be left partially visible.
        connection.execute(
            "DELETE FROM agent_runs WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
        connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        cursor = connection.execute(
            "DELETE FROM user_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
        return cursor.rowcount > 0


def rename_session(user_id: str, session_id: str, title: str) -> dict | None:
    """Rename one owned session without changing its activity timestamp."""
    cleaned = title.strip()
    if not cleaned:
        raise ValueError("Session title cannot be empty")
    with connect() as connection:
        owner = connection.execute(
            "SELECT user_id, immutable FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner is None:
            return None
        if owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        if owner["immutable"]:
            raise ReadOnlySessionError("Read-only showcase sessions cannot be renamed")
        connection.execute(
            "UPDATE user_sessions SET title = ? WHERE user_id = ? AND session_id = ?",
            (cleaned[:80], user_id, session_id),
        )
        row = connection.execute(
            "SELECT session_id, title, created_at, updated_at, immutable FROM user_sessions "
            "WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        return dict(row)


def claim_session(user_id: str, session_id: str, title: str = "新会话") -> None:
    now = datetime.now(UTC).isoformat()
    with connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None:
            raise LookupError("User not found")
        owner = connection.execute(
            "SELECT user_id, immutable FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner and owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        if owner and owner["immutable"]:
            raise ReadOnlySessionError("Read-only showcase sessions cannot accept new messages")
        connection.execute(
            "INSERT OR IGNORE INTO user_sessions("
            "session_id, user_id, title, created_at, updated_at, immutable"
            ") VALUES (?, ?, ?, ?, ?, 0)",
            (session_id, user_id, title[:80] or "新会话", now, now),
        )


def list_user_sessions(user_id: str) -> list[dict]:
    with connect() as connection:
        sessions = [dict(row) for row in connection.execute("""
            SELECT us.session_id, us.title, us.created_at, us.updated_at, us.immutable,
                   COUNT(ar.run_id) AS turn_count,
                   COALESCE((
                       SELECT answer FROM agent_runs latest
                       WHERE latest.user_id = us.user_id
                         AND latest.session_id = us.session_id
                       ORDER BY latest.turn_id DESC, latest.created_at DESC LIMIT 1
                   ), '') AS last_message
              FROM user_sessions us
              LEFT JOIN agent_runs ar
                ON ar.user_id = us.user_id AND ar.session_id = us.session_id
             WHERE us.user_id = ?
             GROUP BY us.session_id, us.title, us.created_at, us.updated_at, us.immutable
             ORDER BY us.updated_at DESC
        """, (user_id,))]
        return sessions


def get_user_session_detail(
    user_id: str, session_id: str, *, limit: int = 50, before_turn: int | None = None,
) -> dict | None:
    """Return one owned session with batched observability children."""
    with connect(readonly=True) as connection:
        owner = connection.execute(
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner is None:
            return None
        if owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        session = connection.execute(
            "SELECT session_id, title, created_at, updated_at, immutable FROM user_sessions "
            "WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        parameters: list = [user_id, session_id]
        before_clause = ""
        if before_turn is not None:
            before_clause = "AND turn_id < ?"
            parameters.append(before_turn)
        parameters.append(limit + 1)
        rows = connection.execute(f"""
            SELECT run_id, turn_id, created_at, query, answer, answer_status,
                   routing_mode, termination_reason, workflow_status, llm_status,
                   latency_ms, knowledge_cutoff
              FROM agent_runs
             WHERE user_id = ? AND session_id = ? {before_clause}
             ORDER BY turn_id DESC, created_at DESC LIMIT ?
        """, parameters).fetchall()
        has_more = len(rows) > limit
        runs = [dict(row) for row in reversed(rows[:limit])]
        run_ids = [run["run_id"] for run in runs]
        children = _session_children(connection, run_ids)
        for run in runs:
            run_id = run["run_id"]
            run["tool_calls"] = children["tool_executions"].get(run_id, [])
            run["evidence"] = children["evidence_records"].get(run_id, [])
            run["workflow_events"] = children["workflow_events"].get(run_id, [])
            run["llm_calls"] = children["llm_executions"].get(run_id, [])
        return {
            **dict(session), "runs": runs, "has_more": has_more,
            "oldest_turn": runs[0]["turn_id"] if runs else None,
        }


def set_session_immutable(user_id: str, session_id: str, immutable: bool = True) -> bool:
    """Mark a session read-only without changing its title or activity timestamp."""
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE user_sessions SET immutable = ? WHERE user_id = ? AND session_id = ?",
            (int(immutable), user_id, session_id),
        )
        return cursor.rowcount > 0


def _session_children(connection, run_ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    tables = ("tool_executions", "evidence_records", "workflow_events", "llm_executions")
    grouped = {table: {} for table in tables}
    if not run_ids:
        return grouped
    placeholders = ",".join("?" for _ in run_ids)
    for table in tables:
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE run_id IN ({placeholders}) ORDER BY run_id, sequence",
            run_ids,
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            run_id = row.pop("run_id")
            for key in list(row):
                if key.endswith("_json"):
                    value = row.pop(key)
                    row[key.removesuffix("_json")] = json.loads(value) if value else None
            grouped[table].setdefault(run_id, []).append(row)
    return grouped
