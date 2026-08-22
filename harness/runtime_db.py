"""Shared SQLite location and connection policy for all mutable runtime state."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_PATH = PROJECT_ROOT / "runtime" / "fintrace.sqlite3"


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def runtime_path() -> Path:
    raw = (os.getenv("FINTRACE_RUNTIME_DB") or "").strip()
    configured = Path(raw).expanduser() if raw else DEFAULT_RUNTIME_PATH
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def connect_runtime(
    *, readonly: bool = False, path: Path | None = None,
) -> sqlite3.Connection:
    target = path or runtime_path()
    if readonly and not target.exists():
        raise FileNotFoundError(f"Runtime database not found: {target}")
    if not readonly:
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        connection.execute("PRAGMA journal_mode = WAL")
    return connection
