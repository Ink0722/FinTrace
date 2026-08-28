"""Build a compact, read-only evaluation seed from the unified runtime database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from harness.tracing.store import SHOWCASE_USER_ID, connect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "runtime" / "fintrace.sqlite3"
DEFAULT_OUTPUT = PROJECT_ROOT / "deployment" / "assets" / "fintrace-showcase-seed.sqlite3"


def build_seed(
    *, source: Path, output: Path, batch_id: str,
    showcase_user_id: str = SHOWCASE_USER_ID,
) -> dict:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("The showcase seed output must differ from the runtime source database")
    if not source.is_file():
        raise FileNotFoundError(f"Runtime database not found: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(temporary)) as target:
            source_connection.backup(target)

    try:
        # Apply current runtime migrations to the copied database before pruning it.
        migration_connection = connect(path=temporary)
        migration_connection.close()
        counts = _prune_seed(temporary, batch_id=batch_id, showcase_user_id=showcase_user_id)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    digest = sha256(output)
    manifest = {
        "status": "complete",
        "batch_id": batch_id,
        "showcase_user_id": showcase_user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source_path": display_path(source),
        "output_path": display_path(output),
        "output_size_bytes": output.stat().st_size,
        "sha256": digest,
        **counts,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    output.with_suffix(".sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    return manifest


def _prune_seed(path: Path, *, batch_id: str, showcase_user_id: str) -> dict:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        batch = connection.execute(
            "SELECT * FROM evaluation_batches WHERE batch_id = ?", (batch_id,),
        ).fetchone()
        if batch is None:
            raise LookupError(f"Evaluation batch not found: {batch_id}")
        cases = connection.execute(
            "SELECT agent_session_id, run_id FROM evaluation_cases "
            "WHERE batch_id = ? AND status = 'completed' AND run_id IS NOT NULL",
            (batch_id,),
        ).fetchall()
        if not cases:
            raise ValueError(f"Evaluation batch has no completed runs: {batch_id}")
        session_ids = sorted({row["agent_session_id"] for row in cases})
        run_ids = sorted({row["run_id"] for row in cases})

        connection.execute("PRAGMA foreign_keys = OFF")
        for child_table in (
            "tool_executions", "evidence_records", "workflow_events", "llm_executions",
        ):
            _delete_not_in(connection, child_table, "run_id", run_ids)
        _delete_not_in(connection, "agent_runs", "run_id", run_ids)
        _delete_not_in(connection, "sessions", "session_id", session_ids)
        _delete_not_in(connection, "user_sessions", "session_id", session_ids)
        connection.execute("DELETE FROM evaluation_cases WHERE batch_id != ?", (batch_id,))
        connection.execute(
            "DELETE FROM evaluation_cases WHERE batch_id = ? "
            "AND (status != 'completed' OR run_id IS NULL)",
            (batch_id,),
        )
        connection.execute("DELETE FROM evaluation_batches WHERE batch_id != ?", (batch_id,))

        now = datetime.now(UTC).isoformat()
        connection.execute("DELETE FROM users")
        connection.execute(
            "INSERT INTO users(user_id, display_name, avatar_color, created_at, updated_at) "
            "VALUES (?, 'FinTrace 展示', '#078b98', ?, ?)",
            (showcase_user_id, now, now),
        )
        connection.execute(
            "UPDATE user_sessions SET user_id = ?, immutable = 1", (showcase_user_id,),
        )
        connection.execute("UPDATE agent_runs SET user_id = ?", (showcase_user_id,))
        connection.execute(
            "UPDATE evaluation_batches SET evaluation_user_id = ? WHERE batch_id = ?",
            (showcase_user_id, batch_id),
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if foreign_key_errors:
            raise RuntimeError(f"Showcase seed foreign-key check failed: {foreign_key_errors[:5]}")
        if integrity != "ok":
            raise RuntimeError(f"Showcase seed integrity check failed: {integrity}")
        connection.execute("VACUUM")
        return {
            "session_count": len(session_ids),
            "run_count": len(run_ids),
            "case_count": len(cases),
            "integrity_check": integrity,
        }


def _delete_not_in(
    connection: sqlite3.Connection, table: str, column: str, values: list[str],
) -> None:
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"DELETE FROM {table} WHERE {column} NOT IN ({placeholders})", values)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--showcase-user-id", default=SHOWCASE_USER_ID)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_seed(
        source=args.source, output=args.output, batch_id=args.batch_id,
        showcase_user_id=args.showcase_user_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
