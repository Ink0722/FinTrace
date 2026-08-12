from datetime import date
from pathlib import Path
import shutil
from uuid import uuid4

from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.ownership_graph.graph import find_paths
from tools.ownership_graph.interface import ownership_penetration
from tools.ownership_graph.sample_data import load_sample_entities, load_sample_relations


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


def test_ownership_tool_returns_path_evidence() -> None:
    result = ownership_penetration(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.OWNERSHIP_PENETRATION,
            arguments={
                "source_entity_id": "PERSON-001",
                "target_entity_id": "000001.SZ",
                "as_of_date": "2024-12-31",
                "max_depth": 5,
                "relation_types": ["OWNS", "CONTROLS"],
            },
            reason="test",
        )
    )
    assert result.status.value == "success"
    assert result.data["summary"]["highest_ratio_path"]["indirect_ratio"] == 0.144
    assert {item.evidence_id for item in result.evidence} >= {"EVID-GRAPH-001", "EVID-GRAPH-002", "EVID-GRAPH-003"}


def test_ownership_tool_prefers_csv_data(monkeypatch) -> None:
    test_root = write_ownership_csv(
        entities=[
            "entity_id,entity_name,entity_type,company_id",
            "PERSON-CSV,李某,PERSON,",
            "HOLDCO-CSV,CSV控股公司,COMPANY,",
            "000777.SZ,CSV上市公司,LISTED_COMPANY,000777.SZ",
        ],
        relations=[
            "source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id,source_doc_id,source_path,page",
            "PERSON-CSV,HOLDCO-CSV,OWNS,80%,2020-01-01,,EVID-CSV-001,DOC-CSV-001,data/raw/own.pdf,10",
            "HOLDCO-CSV,000777.SZ,OWNS,0.25,2020-01-01,,EVID-CSV-002,DOC-CSV-002,data/raw/own.pdf,11",
        ],
    )
    try:
        monkeypatch.setenv("OWNERSHIP_DATA_SOURCE", "csv")
        monkeypatch.setenv("OWNERSHIP_ENTITIES_PATH", str(test_root / "entities.csv"))
        monkeypatch.setenv("OWNERSHIP_RELATIONS_PATH", str(test_root / "relations.csv"))
        result = ownership_penetration(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.OWNERSHIP_PENETRATION,
                arguments={
                    "source_entity_id": "PERSON-CSV",
                    "target_entity_id": "000777.SZ",
                    "as_of_date": "2024-12-31",
                    "relation_types": ["OWNS"],
                },
                reason="test",
            )
        )
        assert result.status.value == "success"
        assert result.data["data_source"] == "csv"
        assert result.data["summary"]["highest_ratio_path"]["indirect_ratio"] == 0.2
        assert {item.evidence_id for item in result.evidence} == {"EVID-CSV-001", "EVID-CSV-002"}
        assert result.evidence[0].source.source_path == "data/raw/own.pdf"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_ownership_csv_target_without_data_returns_error(monkeypatch) -> None:
    test_root = write_ownership_csv(
        entities=[
            "entity_id,entity_name,entity_type,company_id",
            "PERSON-CSV,李某,PERSON,",
            "000777.SZ,CSV上市公司,LISTED_COMPANY,000777.SZ",
        ],
        relations=[
            "source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id",
            "PERSON-CSV,000777.SZ,OWNS,0.2,2020-01-01,,EVID-CSV-001",
        ],
    )
    try:
        monkeypatch.setenv("OWNERSHIP_DATA_SOURCE", "csv")
        monkeypatch.setenv("OWNERSHIP_ENTITIES_PATH", str(test_root / "entities.csv"))
        monkeypatch.setenv("OWNERSHIP_RELATIONS_PATH", str(test_root / "relations.csv"))
        result = ownership_penetration(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.OWNERSHIP_PENETRATION,
                arguments={"source_entity_id": "PERSON-CSV", "target_entity_id": "000888.SZ"},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_ownership_csv_validation_error(monkeypatch) -> None:
    test_root = write_ownership_csv(
        entities=[
            "entity_id,entity_name,entity_type,company_id",
            "PERSON-CSV,李某,PERSON,",
        ],
        relations=[
            "source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id",
            "PERSON-CSV,MISSING,OWNS,1.2,2020-01-01,,EVID-CSV-001",
        ],
    )
    try:
        monkeypatch.setenv("OWNERSHIP_DATA_SOURCE", "csv")
        monkeypatch.setenv("OWNERSHIP_ENTITIES_PATH", str(test_root / "entities.csv"))
        monkeypatch.setenv("OWNERSHIP_RELATIONS_PATH", str(test_root / "relations.csv"))
        result = ownership_penetration(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.OWNERSHIP_PENETRATION,
                arguments={"source_entity_id": "PERSON-CSV", "target_entity_id": "MISSING"},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "VALIDATION_FAILED"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_ownership_path_search_respects_max_paths(monkeypatch) -> None:
    entities = ["entity_id,entity_name,entity_type,company_id", "PERSON-CSV,李某,PERSON,", "000777.SZ,CSV上市公司,LISTED_COMPANY,000777.SZ"]
    relations = ["source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id"]
    for index in range(3):
        entities.append(f"HOLDCO-{index},控股公司{index},COMPANY,")
        relations.append(f"PERSON-CSV,HOLDCO-{index},OWNS,0.5,2020-01-01,,EVID-A-{index}")
        relations.append(f"HOLDCO-{index},000777.SZ,OWNS,0.2,2020-01-01,,EVID-B-{index}")
    test_root = write_ownership_csv(entities=entities, relations=relations)
    try:
        monkeypatch.setenv("OWNERSHIP_DATA_SOURCE", "csv")
        monkeypatch.setenv("OWNERSHIP_ENTITIES_PATH", str(test_root / "entities.csv"))
        monkeypatch.setenv("OWNERSHIP_RELATIONS_PATH", str(test_root / "relations.csv"))
        result = ownership_penetration(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.OWNERSHIP_PENETRATION,
                arguments={"source_entity_id": "PERSON-CSV", "target_entity_id": "000777.SZ", "max_paths": 2},
                reason="test",
            )
        )
        assert result.status.value == "success"
        assert len(result.data["paths"]) == 2
        assert result.data["truncated"] is True
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def write_ownership_csv(entities: list[str], relations: list[str]) -> Path:
    test_root = Path(".tmp_tests") / f"ownership_csv_{uuid4().hex}"
    test_root.mkdir(parents=True, exist_ok=True)
    (test_root / "entities.csv").write_text("\n".join(entities), encoding="utf-8")
    (test_root / "relations.csv").write_text("\n".join(relations), encoding="utf-8")
    return test_root
