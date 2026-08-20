"""Shared company entity resolver backed by the offline alias index (docs/13 §7)."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from data_pipeline.entity_alias.build_index import normalize_alias

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "indexes" / "entity_alias" / "company_aliases.sqlite"

WINDCODE_PATTERN = re.compile(r"\b(\d{6}\.(?:SZ|SH|BJ))\b", flags=re.IGNORECASE)
BARE_CODE_PATTERN = re.compile(r"\b(\d{6})\b")


@dataclass(frozen=True)
class CompanyResolution:
    status: str  # resolved | ambiguous | not_found
    company_id: str | None = None
    candidates: list[dict] = field(default_factory=list)


class EntityResolver:
    """Resolves company terms (windcode, bare code, or Chinese name) to canonical ids.

    Never guesses: unknown terms stay unresolved. The alias table is memory-cached
    keyed by index mtime so rebuilds are picked up without a process restart.
    """

    def __init__(self, index_path: Path | None = None):
        self.index_path = index_path or self._configured_path()

    @staticmethod
    def _configured_path() -> Path:
        raw = os.getenv("FINTRACE_ENTITY_ALIAS_INDEX_PATH")
        if not raw:
            return DEFAULT_INDEX_PATH
        path = Path(raw).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    def available(self) -> bool:
        return self.index_path.is_file()

    def resolve_company(self, term: str) -> CompanyResolution:
        table = self._alias_table()
        if table is None:
            # Degraded mode without the index: windcodes pass through, names stay unresolved.
            normalized = term.strip().upper()
            if WINDCODE_PATTERN.fullmatch(normalized):
                return CompanyResolution(status="resolved", company_id=normalized)
            return CompanyResolution(status="not_found", candidates=[{"term": term, "company_id": None, "name": None}])

        normalized_term = normalize_alias(term)
        if WINDCODE_PATTERN.fullmatch(term.strip().upper()):
            if term.strip().upper() in table["companies"]:
                return CompanyResolution(status="resolved", company_id=term.strip().upper())
            return CompanyResolution(status="not_found", candidates=[])
        if re.fullmatch(r"\d{6}", normalized_term):
            suffix_matches = [
                company_id
                for company_id in (f"{normalized_term}.SZ", f"{normalized_term}.SH", f"{normalized_term}.BJ")
                if company_id in table["companies"]
            ]
            if len(suffix_matches) == 1:
                return CompanyResolution(status="resolved", company_id=suffix_matches[0])
            if len(suffix_matches) > 1:
                return CompanyResolution(
                    status="ambiguous",
                    candidates=[{"company_id": cid, "name": table["companies"][cid]} for cid in suffix_matches],
                )
            return CompanyResolution(status="not_found", candidates=[])

        company_ids = table["by_alias"].get(normalized_term, [])
        if len(company_ids) == 1:
            return CompanyResolution(status="resolved", company_id=company_ids[0])
        if len(company_ids) > 1:
            return CompanyResolution(
                status="ambiguous",
                candidates=[{"company_id": cid, "name": table["companies"].get(cid)} for cid in company_ids],
            )
        suggestions = self._suggest(normalized_term, table)
        return CompanyResolution(status="not_found", candidates=suggestions)

    def find_company_names_in_text(self, text: str) -> list[str]:
        """Substring-scan the alias vocabulary; returns matched alias terms found in the text."""
        table = self._alias_table()
        if table is None:
            return []
        hits: list[str] = []
        for alias in table["aliases_by_length"]:
            if alias in text:
                hits.append(alias)
        return hits

    def company_name(self, company_id: str) -> str | None:
        table = self._alias_table()
        if table is None:
            return None
        return table["companies"].get(company_id)

    def _suggest(self, normalized_term: str, table: dict, limit: int = 5) -> list[dict]:
        if len(normalized_term) < 2:
            return []
        suggestions = []
        for alias, company_ids in table["by_alias"].items():
            if normalized_term in alias or alias in normalized_term:
                for company_id in company_ids:
                    suggestions.append({"company_id": company_id, "name": table["companies"].get(company_id)})
                    break
                if len(suggestions) >= limit:
                    break
        return suggestions

    def _alias_table(self) -> dict | None:
        if not self.available():
            return None
        try:
            mtime = self.index_path.stat().st_mtime_ns
        except OSError:
            return None
        with _TABLE_LOCK:
            cached = _TABLE_CACHE.get(str(self.index_path))
            if cached is not None and cached[0] == mtime:
                return cached[1]
        table = self._load_table()
        with _TABLE_LOCK:
            _TABLE_CACHE[str(self.index_path)] = (mtime, table)
        return table

    def _load_table(self) -> dict:
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            companies = {
                row["company_id"]: row["canonical_name"]
                for row in connection.execute("SELECT company_id, canonical_name FROM companies")
            }
            by_alias: dict[str, list[str]] = {}
            for row in connection.execute("SELECT normalized_alias, company_id FROM aliases"):
                by_alias.setdefault(row["normalized_alias"], []).append(row["company_id"])
        aliases_by_length = sorted(by_alias.keys(), key=len, reverse=True)
        return {
            "companies": companies,
            "by_alias": by_alias,
            "aliases_by_length": aliases_by_length,
        }


_TABLE_CACHE: dict[str, tuple[int, dict | None]] = {}
_TABLE_LOCK = threading.Lock()


def clear_entity_cache() -> None:
    with _TABLE_LOCK:
        _TABLE_CACHE.clear()
