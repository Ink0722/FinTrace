from datetime import date

from schemas.ownership import OwnershipEntity, OwnershipRelation


def load_sample_entities() -> list[OwnershipEntity]:
    return [
        OwnershipEntity(entity_id="PERSON-001", name="张某", entity_type="PERSON", aliases=["张三"]),
        OwnershipEntity(entity_id="COMPANY-021", name="示例投资有限公司", entity_type="COMPANY", aliases=["示例投资"]),
        OwnershipEntity(entity_id="FUND-006", name="示例产业基金", entity_type="FUND", aliases=["产业基金"]),
        OwnershipEntity(entity_id="COMPANY-088", name="示例控股集团", entity_type="COMPANY", aliases=["示例控股"]),
        OwnershipEntity(entity_id="000001.SZ", name="示例上市公司", entity_type="COMPANY", aliases=["示例公司"]),
        OwnershipEntity(entity_id="COMPANY-099", name="无关投资公司", entity_type="COMPANY", aliases=[]),
    ]


def load_sample_relations() -> list[OwnershipRelation]:
    return [
        OwnershipRelation(
            edge_id="EDGE-001",
            source_entity_id="PERSON-001",
            target_entity_id="COMPANY-021",
            relation_type="OWNS",
            ratio=0.8,
            valid_from=date(2020, 1, 1),
            valid_to=None,
            evidence_id="EVID-GRAPH-001",
        ),
        OwnershipRelation(
            edge_id="EDGE-002",
            source_entity_id="COMPANY-021",
            target_entity_id="FUND-006",
            relation_type="OWNS",
            ratio=0.6,
            valid_from=date(2020, 6, 1),
            valid_to=None,
            evidence_id="EVID-GRAPH-002",
        ),
        OwnershipRelation(
            edge_id="EDGE-003",
            source_entity_id="FUND-006",
            target_entity_id="000001.SZ",
            relation_type="OWNS",
            ratio=0.3,
            valid_from=date(2021, 1, 1),
            valid_to=None,
            evidence_id="EVID-GRAPH-003",
        ),
        OwnershipRelation(
            edge_id="EDGE-004",
            source_entity_id="PERSON-001",
            target_entity_id="COMPANY-088",
            relation_type="CONTROLS",
            ratio=None,
            valid_from=date(2019, 1, 1),
            valid_to=None,
            evidence_id="EVID-GRAPH-004",
        ),
        OwnershipRelation(
            edge_id="EDGE-005",
            source_entity_id="COMPANY-088",
            target_entity_id="000001.SZ",
            relation_type="CONTROLS",
            ratio=None,
            valid_from=date(2022, 1, 1),
            valid_to=None,
            evidence_id="EVID-GRAPH-005",
        ),
        OwnershipRelation(
            edge_id="EDGE-006",
            source_entity_id="COMPANY-099",
            target_entity_id="000001.SZ",
            relation_type="OWNS",
            ratio=0.05,
            valid_from=date(2020, 1, 1),
            valid_to=date(2021, 12, 31),
            evidence_id="EVID-GRAPH-006",
        ),
    ]
