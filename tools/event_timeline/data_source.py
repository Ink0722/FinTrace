import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from schemas.event import EventRecord
from tools.event_timeline.csv_loader import CsvEventDataSource
from tools.event_timeline.sample_data import load_sample_events


DEFAULT_EVENTS_PATH = Path("data/events/events.csv")


class EventDataSource(Protocol):
    name: str

    def load_events(self, company_id: str) -> list[EventRecord]:
        ...


@dataclass
class EventDataset:
    events: list[EventRecord]
    source_name: str
    warnings: list[str]
    strict: bool = False


class SampleEventDataSource:
    name = "sample"

    def load_events(self, company_id: str) -> list[EventRecord]:
        return [event for event in load_sample_events() if event.company_id == company_id]


def load_event_dataset(company_id: str) -> EventDataset:
    source = os.getenv("EVENT_DATA_SOURCE", "auto").lower()
    events_path = Path(os.getenv("EVENTS_PATH", str(DEFAULT_EVENTS_PATH)))

    if source in {"csv", "auto"} and events_path.exists():
        data_source = CsvEventDataSource(events_path=events_path)
        return EventDataset(
            events=data_source.load_events(company_id=company_id),
            source_name=data_source.name,
            warnings=[f"Using CSV event data: {events_path}"],
            strict=True,
        )

    if source == "csv":
        return EventDataset(
            events=[],
            source_name="csv",
            warnings=[f"CSV event records file not found: {events_path}"],
            strict=True,
        )

    data_source = SampleEventDataSource()
    return EventDataset(
        events=data_source.load_events(company_id=company_id),
        source_name=data_source.name,
        warnings=["Using built-in sample events. Configure EVENT_DATA_SOURCE=csv for real event records."],
        strict=False,
    )
