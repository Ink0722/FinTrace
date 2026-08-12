import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from schemas.event import EventRecord


@dataclass
class CsvEventDataSource:
    events_path: Path
    name: str = "csv"

    def load_events(self, company_id: str) -> list[EventRecord]:
        with self.events_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            events: list[EventRecord] = []
            for row in rows:
                if (row.get("company_id") or "").strip() != company_id:
                    continue
                source_doc_id = (row.get("source_doc_id") or row.get("source_document_id") or "").strip()
                events.append(
                    EventRecord(
                        event_id=(row.get("event_id") or "").strip(),
                        company_id=company_id,
                        event_type=normalize_event_type(row.get("event_type")),
                        event_date=parse_date(row.get("event_date")),
                        entities=split_list(row.get("entities")),
                        title=(row.get("title") or "").strip(),
                        summary=(row.get("description") or row.get("summary") or "").strip(),
                        source_document_ids=[source_doc_id] if source_doc_id else [],
                        evidence_id=(row.get("evidence_id") or "").strip() or None,
                        source_path=(row.get("source_path") or "").strip() or None,
                        page=parse_int(row.get("page")),
                    )
                )
            return events


def normalize_event_type(value: str | None) -> str:
    aliases = {
        "control_change": "controller_change",
        "regulatory_penalty": "risk_warning",
        "pledge": "share_pledge",
        "litigation": "major_litigation",
        "public_opinion": "risk_warning",
    }
    normalized = (value or "").strip()
    return aliases.get(normalized, normalized)


def parse_date(value: str | None) -> date:
    if value is None or str(value).strip() == "":
        raise ValueError("event_date is required.")
    return date.fromisoformat(str(value).strip())


def parse_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).strip()))


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).replace("；", ";").split(";") if item.strip()]
