from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KB_PATH = PROJECT_ROOT / "data" / "indexes" / "document_search" / "fintrace_kb.sqlite"


@dataclass(frozen=True)
class DocumentSearchConfig:
    kb_path: Path
    bm25_index_path: Path
    demo_mode: bool
    default_mode: str
    default_top_k: int
    max_top_k: int
    max_pool_k: int
    rrf_k: int
    max_chunks_per_document: int
    exact_search_batch_size: int

    @classmethod
    def from_env(cls) -> "DocumentSearchConfig":
        kb_path = _configured_path("FINTRACE_KB_PATH", DEFAULT_KB_PATH)
        return cls(
            kb_path=kb_path,
            bm25_index_path=_configured_path(
                "FINTRACE_BM25_INDEX_PATH", kb_path.parent / "bm25_index.sqlite"
            ),
            demo_mode=_env_bool("FINTRACE_DOCUMENT_SEARCH_DEMO_MODE", False),
            default_mode=os.getenv("FINTRACE_DOCUMENT_SEARCH_DEFAULT_MODE", "hybrid").strip().lower(),
            default_top_k=_env_int("FINTRACE_DOCUMENT_SEARCH_DEFAULT_TOP_K", 8, minimum=1),
            max_top_k=_env_int("FINTRACE_DOCUMENT_SEARCH_MAX_TOP_K", 20, minimum=1),
            max_pool_k=_env_int("FINTRACE_DOCUMENT_SEARCH_MAX_POOL_K", 500, minimum=1),
            rrf_k=_env_int("FINTRACE_DOCUMENT_SEARCH_RRF_K", 60, minimum=1),
            max_chunks_per_document=_env_int(
                "FINTRACE_DOCUMENT_SEARCH_MAX_CHUNKS_PER_DOCUMENT", 3, minimum=1
            ),
            exact_search_batch_size=_env_int(
                "FINTRACE_DOCUMENT_SEARCH_EXACT_BATCH_SIZE", 4096, minimum=1
            ),
        )


def _configured_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}.")


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}.")
    return value
