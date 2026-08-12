import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from schemas.ownership import OwnershipEntity, OwnershipRelation


@dataclass
class CsvOwnershipDataSource:
    entities_path: Path
    relations_path: Path
    name: str = "csv"

    def load_entities(self) -> list[OwnershipEntity]:
        with self.entities_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            return [
                OwnershipEntity(
                    entity_id=row["entity_id"].strip(),
                    name=(row.get("entity_name") or row.get("name") or "").strip(),
                    entity_type=normalize_entity_type(row.get("entity_type")),
                    company_id=(row.get("company_id") or "").strip() or None,
                    aliases=split_list(row.get("aliases")),
                )
                for row in rows
                if row.get("entity_id")
            ]

    def load_relations(self) -> list[OwnershipRelation]:
        with self.relations_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            relations: list[OwnershipRelation] = []
            for index, row in enumerate(rows, start=1):
                source = (row.get("source_entity_id") or "").strip()
                target = (row.get("target_entity_id") or "").strip()
                if not source or not target:
                    continue
                relations.append(
                    OwnershipRelation(
                        edge_id=(row.get("edge_id") or f"CSV-OWN-{index:06d}").strip(),
                        source_entity_id=source,
                        target_entity_id=target,
                        relation_type=normalize_relation_type(row.get("relation_type")),
                        ratio=parse_float(row.get("ratio")),
                        valid_from=parse_date(row.get("start_date") or row.get("valid_from")),
                        valid_to=parse_date(row.get("end_date") or row.get("valid_to")),
                        evidence_id=(row.get("evidence_id") or f"EVID-OWN-{index:06d}").strip(),
                        source_doc_id=(row.get("source_doc_id") or "").strip() or None,
                        source_path=(row.get("source_path") or "").strip() or None,
                        page=parse_int(row.get("page")),
                    )
                )
            return relations


def normalize_entity_type(value: str | None) -> str:
    normalized = (value or "COMPANY").strip().upper()
    aliases = {"PERSON": "PERSON", "PEOPLE": "PERSON", "NATURAL_PERSON": "PERSON", "LISTED_COMPANY": "LISTED_COMPANY"}
    return aliases.get(normalized, normalized)


def normalize_relation_type(value: str | None) -> str:
    return (value or "OWNS").strip().upper()


def parse_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100
    number = float(text)
    return number / 100 if number > 1 else number


def parse_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).strip()))


def parse_date(value: str | None) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    return date.fromisoformat(str(value).strip())


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).replace("；", ";").split(";") if item.strip()]
