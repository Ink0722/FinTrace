"""Merge legacy session and observability databases into one runtime database."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from harness.memory.session_store import SessionStore
from harness.runtime_db import runtime_path
from harness.tracing.store import connect


def migrate(session_source: Path, observability_source: Path, target: Path) -> dict[str, Any]:
    sources = [session_source.resolve(), observability_source.resolve()]
    target = target.resolve()
    if not all(path.exists() for path in sources):
        missing = [str(path) for path in sources if not path.exists()]
        raise FileNotFoundError(f"Missing source databases: {missing}")
    if target in sources:
        raise ValueError("Target must differ from both source databases")
    if target.exists():
        raise FileExistsError(f"Target already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    _checkpoint(observability_source)
    _checkpoint(session_source)
    shutil.copy2(observability_source, temporary)
    try:
        SessionStore(path=temporary)
        with closing(sqlite3.connect(session_source)) as source:
            source.row_factory = sqlite3.Row
            rows = source.execute("""
                SELECT session_id, current_context, conversation_summary,
                       verified_findings, recent_messages, turn_count, updated_at
                FROM sessions ORDER BY session_id
            """).fetchall()
        with connect(path=temporary) as destination:
            destination.executemany("""
                INSERT OR REPLACE INTO sessions (
                    session_id, current_context, conversation_summary, verified_findings,
                    recent_messages, turn_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [(
                row["session_id"], row["current_context"], row["conversation_summary"],
                row["verified_findings"], row["recent_messages"], row["turn_count"],
                row["updated_at"],
            ) for row in rows])
            destination.commit()
        report = _validate(session_source, observability_source, temporary)
        os.replace(temporary, target)
        return {"status": "completed", "target": str(target), **report}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _checkpoint(path: Path) -> None:
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _validate(session_source: Path, observability_source: Path, target: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(session_source)) as source_sessions:
        expected_sessions = source_sessions.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    with closing(sqlite3.connect(observability_source)) as source_logs:
        tables = [row[0] for row in source_logs.execute("""
            SELECT name FROM sqlite_master WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%' ORDER BY name
        """)]
        expected = {table: source_logs.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    expected["sessions"] = expected_sessions
    with closing(sqlite3.connect(target)) as destination:
        actual = {table: destination.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in expected}
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = len(destination.execute("PRAGMA foreign_key_check").fetchall())
    strict_tables = set(expected) - {"user_sessions", "schema_info"}
    strict_match = all(actual[table] == expected[table] for table in strict_tables)
    derived_valid = actual.get("user_sessions", 0) >= expected.get("user_sessions", 0)
    if not strict_match or not derived_valid or integrity != "ok" or foreign_key_errors:
        raise RuntimeError(
            f"Migration validation failed: expected={expected}, actual={actual}, "
            f"integrity={integrity}, foreign_key_errors={foreign_key_errors}"
        )
    return {
        "table_counts": actual,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
        "sources_preserved": [str(session_source.resolve()), str(observability_source.resolve())],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions", type=Path,
        default=Path("backups/runtime-premerge/sessions.sqlite"),
    )
    parser.add_argument(
        "--observability", type=Path,
        default=Path("backups/runtime-premerge/fintrace_observability.sqlite3"),
    )
    parser.add_argument("--target", type=Path, default=runtime_path())
    args = parser.parse_args()
    print(json.dumps(migrate(args.sessions, args.observability, args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
