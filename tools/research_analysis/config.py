from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_MAPPING_VERSION = "research-views-v1"


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class ResearchAnalysisConfig:
    research_path: Path
    chunks_path: Path
    index_path: Path

    @classmethod
    def from_env(cls) -> "ResearchAnalysisConfig":
        return cls(
            research_path=_path("FINTRACE_RESEARCH_SOURCE_PATH", PROJECT_ROOT / "data" / "normalized" / "research_reports.jsonl"),
            chunks_path=_path("FINTRACE_RESEARCH_CHUNKS_PATH", PROJECT_ROOT / "data" / "processed" / "documents" / "chunks_v2.jsonl"),
            index_path=_path("FINTRACE_RESEARCH_INDEX_PATH", PROJECT_ROOT / "data" / "indexes" / "research_analysis" / "research_views.sqlite"),
        )
