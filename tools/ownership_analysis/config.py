from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OWNERSHIP_MAPPING_VERSION = "ownership-holdings-v1"
SHAREHOLDERS_FILENAME = "shareholders.jsonl"


@dataclass(frozen=True)
class OwnershipAnalysisConfig:
    normalized_dir: Path
    index_path: Path

    @classmethod
    def from_env(cls) -> "OwnershipAnalysisConfig":
        return cls(
            normalized_dir=_path(
                "FINTRACE_OWNERSHIP_NORMALIZED_DIR", PROJECT_ROOT / "data" / "normalized"
            ),
            index_path=_path(
                "FINTRACE_OWNERSHIP_INDEX_PATH",
                PROJECT_ROOT / "data" / "indexes" / "ownership_analysis" / "ownership_holdings.sqlite",
            ),
        )

    @property
    def shareholders_path(self) -> Path:
        return self.normalized_dir / SHAREHOLDERS_FILENAME


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path