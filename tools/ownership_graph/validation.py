from dataclasses import dataclass, field

from schemas.ownership import OwnershipEntity, OwnershipRelation


SUPPORTED_RELATION_TYPES = {"OWNS", "CONTROLS", "ACTS_IN_CONCERT", "VOTING_RIGHTS"}


@dataclass
class OwnershipValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_ownership_dataset(entities: list[OwnershipEntity], relations: list[OwnershipRelation]) -> OwnershipValidationResult:
    result = OwnershipValidationResult()
    entity_ids: set[str] = set()
    for entity in entities:
        if entity.entity_id in entity_ids:
            result.errors.append(f"Duplicate entity_id: {entity.entity_id}")
        entity_ids.add(entity.entity_id)

    for relation in relations:
        if relation.source_entity_id not in entity_ids:
            result.errors.append(f"Relation {relation.edge_id} source entity not found: {relation.source_entity_id}")
        if relation.target_entity_id not in entity_ids:
            result.errors.append(f"Relation {relation.edge_id} target entity not found: {relation.target_entity_id}")
        if relation.relation_type not in SUPPORTED_RELATION_TYPES:
            result.warnings.append(f"Relation {relation.edge_id} uses unsupported relation_type: {relation.relation_type}")
        if relation.ratio is not None and not 0 <= relation.ratio <= 1:
            result.errors.append(f"Relation {relation.edge_id} ratio must be between 0 and 1: {relation.ratio}")
        if relation.valid_from and relation.valid_to and relation.valid_from > relation.valid_to:
            result.errors.append(f"Relation {relation.edge_id} valid_from is later than valid_to")
        if not relation.evidence_id:
            result.warnings.append(f"Relation {relation.edge_id} missing evidence_id")
        if relation.source_entity_id == relation.target_entity_id:
            result.warnings.append(f"Relation {relation.edge_id} is a self-loop")
    return result
