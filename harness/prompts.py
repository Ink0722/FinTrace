"""Prompt assembly per docs/11: global policy + one skill, versions parsed from YAML headers."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
GLOBAL_POLICY_FILE = "01_global_policy.md"


@dataclass(frozen=True)
class PromptFile:
    filename: str
    prompt_id: str
    version: str
    header: dict = field(default_factory=dict)
    body: str = ""


# skill name -> (filename, output schema name, core). Output models are bound in harness/skills.py.
SKILL_REGISTRY: dict[str, tuple[str, str, bool]] = {
    "request_parser": ("02_request_parser.md", "ParsedRequest", True),
    "next_action_planner": ("03_next_action_planner.md", "AgentAction", True),
    "evidence_reviewer": ("04_evidence_reviewer.md", "EvidenceReview", True),
    "action_repair": ("05_action_repair.md", "ActionRepairResult", True),
    "final_answer": ("06_final_answer.md", "FinalAnswer", True),
    "memory_summarizer": ("07_memory_summarizer.md", "MemoryUpdate", False),
    "search_query_rewriter": ("08_search_query_rewriter.md", "SearchQuerySpec", False),
}


class PromptFileError(RuntimeError):
    """Raised when a prompt file is missing, has no valid version header, or a dependency is absent."""


def load_prompt(filename: str) -> PromptFile:
    key = str(PROMPTS_DIR / filename)
    cached = _load_prompt_cached(key, _mtime(key))
    if cached is None:
        raise PromptFileError(f"Prompt file not found: {PROMPTS_DIR / filename}")
    return cached


def build_system_prompt(skill: str) -> str:
    """System prompt = global policy + current skill. Never includes another skill's body."""
    if skill not in SKILL_REGISTRY:
        raise PromptFileError(f"Unknown skill: {skill}")
    global_policy = load_prompt(GLOBAL_POLICY_FILE)
    skill_file = load_prompt(SKILL_REGISTRY[skill][0])
    _validate_dependency(skill_file)
    return f"{global_policy.body.strip()}\n\n--- CURRENT SKILL ---\n\n{skill_file.body.strip()}"


def core_skill_files() -> list[str]:
    """Core skill prompt files that are currently materialized (Phase 2 adds 03-06)."""
    return [
        filename
        for filename, _, core in SKILL_REGISTRY.values()
        if core and (PROMPTS_DIR / filename).is_file()
    ]


def _validate_dependency(skill_file: PromptFile) -> None:
    for dependency in skill_file.header.get("depends_on", []):
        if not dependency.startswith("fintrace.global_policy"):
            continue
        policy = load_prompt(GLOBAL_POLICY_FILE)
        if policy.prompt_id != "fintrace.global_policy":
            raise PromptFileError(f"Invalid global policy header in {GLOBAL_POLICY_FILE}")


def _mtime(key: str) -> int:
    try:
        return Path(key).stat().st_mtime_ns
    except OSError:
        return -1


@lru_cache(maxsize=32)
def _load_prompt_cached(key: str, mtime_ns: int) -> PromptFile | None:
    del mtime_ns  # part of the cache key only; a touched file reloads automatically
    path = Path(key)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    header, body = _split_frontmatter(text)
    prompt_id = str(header.get("prompt_id") or "")
    version = str(header.get("version") or "")
    if not prompt_id or not _valid_version(version):
        raise PromptFileError(
            f"Prompt file {path.name} must start with a YAML header containing prompt_id and a valid version"
        )
    return PromptFile(
        filename=path.name,
        prompt_id=prompt_id,
        version=version,
        header=header,
        body=body.strip(),
    )


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end_index = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
    if end_index < 0:
        return {}, text
    header: dict = {}
    current_key: str | None = None
    for line in lines[1:end_index]:
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            header.setdefault(current_key, [])
            if isinstance(header[current_key], list):
                header[current_key].append(stripped[2:].strip())
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            header[current_key] = value.strip() or []
    return header, "\n".join(lines[end_index + 1 :])


def _valid_version(version: str) -> bool:
    parts = version.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts) and bool(version)


def clear_prompt_cache() -> None:
    _load_prompt_cached.cache_clear()


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
