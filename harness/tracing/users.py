"""Local user workspaces and their session ownership."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from harness.tracing.store import DEFAULT_USER_ID, connect


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
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner is None:
            return False
        if owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")

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
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner is None:
            return None
        if owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        connection.execute(
            "UPDATE user_sessions SET title = ? WHERE user_id = ? AND session_id = ?",
            (cleaned[:80], user_id, session_id),
        )
        row = connection.execute(
            "SELECT session_id, title, created_at, updated_at FROM user_sessions "
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
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        if owner and owner["user_id"] != user_id:
            raise PermissionError("Session belongs to another local user")
        connection.execute(
            "INSERT OR IGNORE INTO user_sessions VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, title[:80] or "新会话", now, now),
        )


def list_user_sessions(user_id: str) -> list[dict]:
    with connect() as connection:
        sessions = [dict(row) for row in connection.execute("""
            SELECT session_id, title, created_at, updated_at FROM user_sessions
            WHERE user_id = ? ORDER BY updated_at DESC
        """, (user_id,))]
        for session in sessions:
            rows = connection.execute("""
                SELECT run_id, turn_id, created_at, query, answer FROM agent_runs
                WHERE user_id = ? AND session_id = ? ORDER BY turn_id, created_at
            """, (user_id, session["session_id"])).fetchall()
            messages = []
            for row in rows:
                messages.extend([
                    {"id": f"u-{row['run_id']}", "role": "user", "content": row["query"], "createdAt": row["created_at"]},
                    {"id": f"a-{row['run_id']}", "role": "assistant", "content": row["answer"] or "", "createdAt": row["created_at"]},
                ])
            session["messages"] = messages
        return sessions
