"""SQLite session persistence for CurrentContext carryover (docs/13 §5, layer 1/2/4 in MVP)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from schemas.agent_state import CurrentContext, Message

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSIONS_PATH = PROJECT_ROOT / "data" / "sessions" / "sessions.sqlite"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    current_context TEXT NOT NULL DEFAULT '{}',
    conversation_summary TEXT NOT NULL DEFAULT '',
    verified_findings TEXT NOT NULL DEFAULT '[]',
    recent_messages TEXT NOT NULL DEFAULT '[]',
    turn_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class SessionStore:
    def __init__(self, path: Path | None = None):
        raw = os.getenv("FINTRACE_SESSIONS_PATH")
        configured = Path(raw).expanduser() if raw else (path or DEFAULT_SESSIONS_PATH)
        self.path = configured if configured.is_absolute() else PROJECT_ROOT / configured
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(SCHEMA_SQL)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "turn_count" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN turn_count INTEGER NOT NULL DEFAULT 0")

    def load(self, session_id: str) -> dict:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT current_context, conversation_summary, verified_findings, recent_messages, turn_count "
                "FROM sessions WHERE session_id = ?",
                [session_id],
            ).fetchone()
        if row is None:
            return {"current_context": {}, "conversation_summary": "", "verified_findings": [], "recent_messages": [], "turn_count": 0}
        return {
            "current_context": json.loads(row["current_context"] or "{}"),
            "conversation_summary": row["conversation_summary"] or "",
            "verified_findings": json.loads(row["verified_findings"] or "[]"),
            "recent_messages": json.loads(row["recent_messages"] or "[]"),
            "turn_count": int(row["turn_count"] or 0),
        }

    def save(
        self,
        session_id: str,
        *,
        current_context: CurrentContext,
        conversation_summary: str,
        verified_findings: list[dict],
        recent_messages: list[Message],
        turn_count: int = 0,
    ) -> None:
        payload = (
            json.dumps(current_context.model_dump(), ensure_ascii=False),
            conversation_summary,
            json.dumps(verified_findings, ensure_ascii=False),
            json.dumps([message.model_dump() for message in recent_messages[-8:]], ensure_ascii=False),
            turn_count,
            datetime.now(UTC).isoformat(),
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO sessions (session_id, current_context, conversation_summary, verified_findings, recent_messages, turn_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    current_context = excluded.current_context,
                    conversation_summary = excluded.conversation_summary,
                    verified_findings = excluded.verified_findings,
                    recent_messages = excluded.recent_messages,
                    turn_count = excluded.turn_count,
                    updated_at = excluded.updated_at
                """,
                [session_id, *payload],
            )
            connection.commit()
