from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


EntityType = Literal["PERSON", "COMPANY", "FUND", "ASSET_MANAGEMENT_PLAN", "LISTED_COMPANY"]
RelationType = Literal["OWNS", "CONTROLS", "ACTS_IN_CONCERT", "VOTING_RIGHTS", "LEGAL_REPRESENTATIVE", "RELATED_PARTY"]


class OwnershipEntity(BaseModel):
    entity_id: str
    name: str
    entity_type: EntityType
    company_id: str | None = None
    aliases: list[str] = Field(default_factory=list)


class OwnershipRelation(BaseModel):
    edge_id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: RelationType
    ratio: float | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    evidence_id: str
    source_doc_id: str | None = None
    source_path: str | None = None
    page: int | None = None


class OwnershipPathHop(BaseModel):
    edge_id: str
    source_entity_id: str
    source_name: str
    target_entity_id: str
    target_name: str
    relation_type: RelationType
    ratio: float | None = None
    evidence_id: str


class OwnershipPath(BaseModel):
    path_type: Literal["holding", "control", "mixed"]
    nodes: list[str]
    hops: list[OwnershipPathHop]
    indirect_ratio: float | None = None
    has_control_path: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
