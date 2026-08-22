from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tools.ownership_analysis.config import OWNERSHIP_MAPPING_VERSION, SHAREHOLDERS_FILENAME


NO_BOUND = "9999-12-31"

_RECORD_COLUMNS = """
    record_id, target_company_id, announcement_date, holder_end_date,
    holder_entity_id, holder_name, holder_category, holding_quantity,
    holding_ratio, holding_ratio_raw, restricted_quantity,
    share_category_code, share_category_name, quality_flags_json, evidence_id
"""


@dataclass(frozen=True)
class HolderRecord:
    record_id: str
    target_company_id: str
    announcement_date: str
    holder_end_date: str
    holder_entity_id: str
    holder_name: str
    holder_category: str
    holding_quantity: int | None
    holding_ratio: float
    holding_ratio_raw: float
    restricted_quantity: int | None
    share_category_code: str | None
    share_category_name: str | None
    quality_flags: tuple[str, ...]
    evidence_id: str
    calculated_rank: int | None = None


@dataclass(frozen=True)
class SnapshotMeta:
    target_company_id: str
    holder_end_date: str
    announcement_date: str
    record_count: int
    ratio_sum: float
    snapshot_scope: str
    quality_flags: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "target_company_id": self.target_company_id,
            "holder_end_date": self.holder_end_date,
            "announcement_date": self.announcement_date,
            "record_count": self.record_count,
            "ratio_sum": self.ratio_sum,
            "snapshot_scope": self.snapshot_scope,
            "quality_flags": list(self.quality_flags),
        }


def snapshot_scope(record_count: int) -> str:
    if record_count == 10:
        return "top_ten"
    if record_count < 10:
        return "partial"
    return "extended_roster"


class OwnershipRepository:
    def __init__(self, index_path: Path):
        self.index_path = index_path

    def available(self) -> bool:
        return self.index_path.is_file()

    def effective_snapshot(
        self,
        company_id: str,
        *,
        as_of: str | None = None,
        knowledge_cutoff: str | None = None,
    ) -> SnapshotMeta | None:
        """Select the latest holder snapshot knowable at the observation point.

        Rules (docs/03-股东快照设计.md §10): announcement_date <= bound prevents
        look-ahead bias; holder_end_date <= as_of keeps the business point in time;
        the latest holder_end_date wins and the latest announcement for that end
        date is the effective version.
        """
        clauses = ["target_company_id = ?"]
        params: list[str] = [company_id]
        announcement_bound = knowledge_cutoff or as_of
        if announcement_bound:
            clauses.append("announcement_date <= ?")
            params.append(announcement_bound)
        if as_of:
            clauses.append("holder_end_date <= ?")
            params.append(as_of)
        sql = f"""
            SELECT holder_end_date, MAX(announcement_date) AS announcement_date
            FROM holder_records
            WHERE {' AND '.join(clauses)}
            GROUP BY holder_end_date
            ORDER BY holder_end_date DESC
            LIMIT 1
        """
        row = self._fetch_one(sql, params)
        if row is None:
            return None
        holder_end_date, announcement_date = row
        stats = self._fetch_one(
            """
            SELECT COUNT(*), SUM(holding_ratio)
            FROM holder_records
            WHERE target_company_id = ? AND holder_end_date = ? AND announcement_date = ?
            """,
            [company_id, holder_end_date, announcement_date],
        )
        record_count = int(stats[0]) if stats else 0
        ratio_sum = round(float(stats[1] or 0.0), 6) if stats else 0.0
        flags: list[str] = []
        if record_count < 10:
            flags.append("snapshot_less_than_ten")
        if record_count > 10:
            flags.append("snapshot_more_than_ten")
        if ratio_sum > 1.0:
            flags.append("snapshot_ratio_sum_over_100")
        return SnapshotMeta(
            target_company_id=company_id,
            holder_end_date=holder_end_date,
            announcement_date=announcement_date,
            record_count=record_count,
            ratio_sum=ratio_sum,
            snapshot_scope=snapshot_scope(record_count),
            quality_flags=tuple(flags),
        )

    def snapshot_records(
        self, company_id: str, holder_end_date: str, announcement_date: str
    ) -> list[HolderRecord]:
        sql = f"""
            SELECT {_RECORD_COLUMNS}
            FROM holder_records
            WHERE target_company_id = ? AND holder_end_date = ? AND announcement_date = ?
            ORDER BY holding_ratio DESC, holder_name ASC
        """
        return [_row_to_record(row) for row in self._fetch_all(sql, [company_id, holder_end_date, announcement_date])]

    def reverse_holdings(
        self,
        holder_entity_ids: list[str],
        *,
        as_of: str | None = None,
        knowledge_cutoff: str | None = None,
    ) -> list[HolderRecord]:
        """Find the given holders' positions in each company's effective snapshot."""
        if not holder_entity_ids:
            return []
        announcement_bound = knowledge_cutoff or as_of or NO_BOUND
        end_bound = as_of or NO_BOUND
        placeholders = ",".join("?" for _ in holder_entity_ids)
        record_columns = ", ".join(f"r.{column.strip()}" for column in _RECORD_COLUMNS.split(","))
        sql = f"""
            WITH relevant_company AS (
                SELECT DISTINCT target_company_id
                FROM holder_records
                WHERE holder_entity_id IN ({placeholders})
            ),
            candidate AS (
                SELECT r.target_company_id, r.holder_end_date,
                       MAX(r.announcement_date) AS announcement_date
                FROM holder_records r
                JOIN relevant_company rc ON rc.target_company_id = r.target_company_id
                WHERE r.announcement_date <= ? AND r.holder_end_date <= ?
                GROUP BY r.target_company_id, r.holder_end_date
            ),
            effective AS (
                SELECT target_company_id, holder_end_date, announcement_date
                FROM (
                    SELECT candidate.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY target_company_id
                               ORDER BY holder_end_date DESC
                           ) AS snapshot_rank
                    FROM candidate
                )
                WHERE snapshot_rank = 1
            )
            SELECT {record_columns},
                (
                    SELECT COUNT(*) + 1
                    FROM holder_records r2
                    WHERE r2.target_company_id = r.target_company_id
                      AND r2.holder_end_date = r.holder_end_date
                      AND r2.announcement_date = r.announcement_date
                      AND r2.holding_ratio > r.holding_ratio
                ) AS calculated_rank
            FROM holder_records r
            JOIN effective e
              ON r.target_company_id = e.target_company_id
             AND r.holder_end_date = e.holder_end_date
             AND r.announcement_date = e.announcement_date
            WHERE r.holder_entity_id IN ({placeholders})
            ORDER BY r.holding_ratio DESC, r.holder_name ASC
        """
        rows = self._fetch_all(
            sql, [*holder_entity_ids, announcement_bound, end_bound, *holder_entity_ids]
        )
        return [_row_to_record(row) for row in rows]

    def resolve_holder_terms(self, terms: list[str]) -> list[dict]:
        """Resolve each term as an exact entity name or a direct entity id."""
        resolution: list[dict] = []
        for term in terms:
            entity_ids = self._entity_ids_by_name(term)
            if not entity_ids and ":" in term:
                entity_ids = self._entity_ids_by_id(term)
            status = "not_found" if not entity_ids else ("ambiguous" if len(entity_ids) > 1 else "resolved")
            resolution.append({"term": term, "entity_ids": entity_ids, "status": status})
        return resolution

    def entity_map(self, holder_entity_ids: list[str]) -> dict[str, dict]:
        if not holder_entity_ids:
            return {}
        placeholders = ",".join("?" for _ in holder_entity_ids)
        rows = self._fetch_all(
            f"""
            SELECT holder_entity_id, name, entity_type, compcode, identity_quality
            FROM holder_entities
            WHERE holder_entity_id IN ({placeholders})
            """,
            holder_entity_ids,
        )
        return {
            row["holder_entity_id"]: {
                "name": row["name"],
                "entity_type": row["entity_type"],
                "compcode": row["compcode"],
                "identity_quality": row["identity_quality"],
            }
            for row in rows
        }

    def outgoing_holdings(
        self, node_id: str, *, as_of: str, knowledge_cutoff: str | None
    ) -> list[HolderRecord]:
        """Return effective-snapshot edges owned by a holder or linked listed company."""
        holder_ids = [node_id] if self._entity_ids_by_id(node_id) else []
        linked = self._fetch_all(
            "SELECT holder_entity_id FROM holder_company_links WHERE company_id = ?",
            [node_id],
        )
        holder_ids.extend(row[0] for row in linked)
        holder_ids = list(dict.fromkeys(holder_ids))
        return self.reverse_holdings(holder_ids, as_of=as_of, knowledge_cutoff=knowledge_cutoff)

    def node_name(self, node_id: str) -> str:
        row = self._fetch_one(
            "SELECT canonical_name FROM listed_company_entities WHERE company_id = ?",
            [node_id],
        )
        if row:
            return str(row[0])
        row = self._fetch_one(
            "SELECT name FROM holder_entities WHERE holder_entity_id = ?",
            [node_id],
        )
        return str(row[0]) if row else node_id

    def holder_company_link(self, holder_entity_id: str) -> str | None:
        rows = self._fetch_all(
            "SELECT company_id FROM holder_company_links WHERE holder_entity_id = ? ORDER BY company_id",
            [holder_entity_id],
        )
        return str(rows[0][0]) if len(rows) == 1 else None

    def _entity_ids_by_name(self, name: str) -> list[str]:
        rows = self._fetch_all(
            "SELECT holder_entity_id FROM holder_entities WHERE name = ?",
            [name],
        )
        return [row[0] for row in rows]

    def _entity_ids_by_id(self, entity_id: str) -> list[str]:
        rows = self._fetch_all(
            "SELECT holder_entity_id FROM holder_entities WHERE holder_entity_id = ?",
            [entity_id],
        )
        return [row[0] for row in rows]

    def _fetch_one(self, sql: str, params: list[str]):
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql, params).fetchone()

    def _fetch_all(self, sql: str, params: list[str]) -> list[sqlite3.Row]:
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(sql, params).fetchall()


def validate_ownership_index_snapshot(
    index_path: Path, normalized_dir: Path, entity_index_path: Path
) -> list[str]:
    manifest_path = index_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return [f"Ownership index manifest not found: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Ownership index manifest is invalid: {type(exc).__name__}: {exc}"]
    errors: list[str] = []
    if manifest.get("mapping_version") != OWNERSHIP_MAPPING_VERSION:
        errors.append(
            f"mapping version mismatch: index={manifest.get('mapping_version')}, expected={OWNERSHIP_MAPPING_VERSION}"
        )
    recorded = manifest.get("source")
    if not isinstance(recorded, dict):
        return [*errors, "Ownership index manifest has no source object."]
    source_path = normalized_dir / SHAREHOLDERS_FILENAME
    if not source_path.is_file():
        errors.append(f"normalized source not found: {source_path}")
        return errors
    stat = source_path.stat()
    if int(recorded.get("size", -1)) != stat.st_size:
        errors.append(f"normalized source size changed: {source_path}")
    if int(recorded.get("mtime_ns", -1)) != stat.st_mtime_ns:
        errors.append(f"normalized source modification time changed: {source_path}")
    recorded_entity = manifest.get("entity_index")
    if not isinstance(recorded_entity, dict):
        errors.append("Ownership index manifest has no entity_index object.")
    elif not entity_index_path.is_file():
        errors.append(f"entity index not found: {entity_index_path}")
    elif recorded_entity.get("sha256") != _sha256(entity_index_path):
        errors.append(f"entity index changed: {entity_index_path}")
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_to_record(row: sqlite3.Row) -> HolderRecord:
    return HolderRecord(
        record_id=row["record_id"],
        target_company_id=row["target_company_id"],
        announcement_date=row["announcement_date"],
        holder_end_date=row["holder_end_date"],
        holder_entity_id=row["holder_entity_id"],
        holder_name=row["holder_name"],
        holder_category=row["holder_category"],
        holding_quantity=row["holding_quantity"],
        holding_ratio=float(row["holding_ratio"]),
        holding_ratio_raw=float(row["holding_ratio_raw"]),
        restricted_quantity=row["restricted_quantity"],
        share_category_code=row["share_category_code"],
        share_category_name=row["share_category_name"],
        quality_flags=tuple(json.loads(row["quality_flags_json"])),
        evidence_id=row["evidence_id"],
        calculated_rank=row["calculated_rank"] if "calculated_rank" in row.keys() else None,
    )
