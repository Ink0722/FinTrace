from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENT_MAPPING_VERSION = "announcement-events-v1"
ANNOUNCEMENTS_FILENAME = "announcements.jsonl"


@dataclass(frozen=True)
class EventTimelineConfig:
    normalized_dir: Path
    index_path: Path

    @classmethod
    def from_env(cls) -> "EventTimelineConfig":
        return cls(
            normalized_dir=_path("FINTRACE_EVENT_NORMALIZED_DIR", PROJECT_ROOT / "data" / "normalized"),
            index_path=_path("FINTRACE_EVENT_INDEX_PATH", PROJECT_ROOT / "data" / "indexes" / "event_timeline" / "events.sqlite"),
        )

    @property
    def announcements_path(self) -> Path:
        return self.normalized_dir / ANNOUNCEMENTS_FILENAME


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path

