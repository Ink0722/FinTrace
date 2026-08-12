import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from schemas.ownership import OwnershipEntity, OwnershipRelation
from tools.ownership_graph.csv_loader import CsvOwnershipDataSource
from tools.ownership_graph.sample_data import load_sample_entities, load_sample_relations


DEFAULT_ENTITIES_PATH = Path("data/ownership/entities.csv")
DEFAULT_RELATIONS_PATH = Path("data/ownership/relations.csv")


class OwnershipDataSource(Protocol):
    name: str

    def load_entities(self) -> list[OwnershipEntity]:
        ...

    def load_relations(self) -> list[OwnershipRelation]:
        ...


@dataclass
class OwnershipDataset:
    entities: list[OwnershipEntity]
    relations: list[OwnershipRelation]
    source_name: str
    warnings: list[str]
    strict: bool = False


class SampleOwnershipDataSource:
    name = "sample"

    def load_entities(self) -> list[OwnershipEntity]:
        return load_sample_entities()

    def load_relations(self) -> list[OwnershipRelation]:
        return load_sample_relations()


def load_ownership_dataset() -> OwnershipDataset:
    source = os.getenv("OWNERSHIP_DATA_SOURCE", "auto").lower()
    entities_path = Path(os.getenv("OWNERSHIP_ENTITIES_PATH", str(DEFAULT_ENTITIES_PATH)))
    relations_path = Path(os.getenv("OWNERSHIP_RELATIONS_PATH", str(DEFAULT_RELATIONS_PATH)))

    if source in {"csv", "auto"} and entities_path.exists() and relations_path.exists():
        data_source = CsvOwnershipDataSource(entities_path=entities_path, relations_path=relations_path)
        return OwnershipDataset(
            entities=data_source.load_entities(),
            relations=data_source.load_relations(),
            source_name=data_source.name,
            warnings=[f"Using CSV ownership data: {entities_path}, {relations_path}"],
            strict=True,
        )

    if source == "csv":
        return OwnershipDataset(
            entities=[],
            relations=[],
            source_name="csv",
            warnings=[f"CSV ownership files not found: {entities_path}, {relations_path}"],
            strict=True,
        )

    data_source = SampleOwnershipDataSource()
    return OwnershipDataset(
        entities=data_source.load_entities(),
        relations=data_source.load_relations(),
        source_name=data_source.name,
        warnings=["Using built-in sample graph data. Configure OWNERSHIP_DATA_SOURCE=csv for real ownership data."],
        strict=False,
    )
