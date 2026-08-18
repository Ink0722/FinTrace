from datetime import date

from tools.ownership_analysis.graph import find_paths
from tools.ownership_analysis.sample_data import load_sample_entities, load_sample_relations


def test_find_holding_and_control_paths() -> None:
    paths = find_paths(
        entities=load_sample_entities(),
        relations=load_sample_relations(),
        source_entity_id="PERSON-001",
        target_entity_id="000001.SZ",
        as_of_date=date(2024, 12, 31),
        max_depth=5,
        relation_types={"OWNS", "CONTROLS"},
    )
    assert len(paths) == 2
    assert any(path.path_type == "holding" and round(path.indirect_ratio or 0, 3) == 0.144 for path in paths)
    assert any(path.path_type == "control" for path in paths)


def test_expired_relation_is_filtered() -> None:
    paths = find_paths(
        entities=load_sample_entities(),
        relations=load_sample_relations(),
        source_entity_id="COMPANY-099",
        target_entity_id="000001.SZ",
        as_of_date=date(2024, 12, 31),
        max_depth=5,
        relation_types={"OWNS"},
    )
    assert paths == []
