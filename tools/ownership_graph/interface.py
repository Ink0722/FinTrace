from datetime import date

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.ownership_graph.data_source import load_ownership_dataset
from tools.ownership_graph.graph import find_paths, relation_evidence, summarize_paths
from tools.ownership_graph.validation import validate_ownership_dataset


def ownership_penetration(call: ToolCall) -> ToolResult:
    dataset = load_ownership_dataset()
    entities = dataset.entities
    relations = dataset.relations
    source_entity_id = call.arguments.get("source_entity_id") or "PERSON-001"
    target_entity_id = call.arguments.get("target_entity_id") or "000001.SZ"
    as_of_date = date.fromisoformat(call.arguments.get("as_of_date") or "2024-12-31")
    max_depth = min(int(call.arguments.get("max_depth") or 5), 8)
    max_paths = min(int(call.arguments.get("max_paths") or 50), 200)
    requested_relation_types = set(call.arguments.get("relation_types") or ["OWNS", "CONTROLS", "ACTS_IN_CONCERT", "VOTING_RIGHTS"])
    warnings = list(dataset.warnings)

    validation = validate_ownership_dataset(entities, relations)
    warnings.extend(validation.warnings)
    if validation.errors:
        return failed_result(
            call=call,
            error_type=ErrorType.VALIDATION_FAILED,
            message="Ownership dataset validation failed.",
            details={"errors": validation.errors},
            warnings=warnings,
        )

    if dataset.strict and not target_has_relations(relations, target_entity_id):
        return failed_result(
            call=call,
            error_type=ErrorType.DATA_NOT_AVAILABLE,
            message=f"No ownership relations found for target_entity_id={target_entity_id}.",
            details={"target_entity_id": target_entity_id, "data_source": dataset.source_name},
            warnings=warnings,
        )

    paths = find_paths(
        entities=entities,
        relations=relations,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        as_of_date=as_of_date,
        max_depth=max_depth,
        relation_types=requested_relation_types,
        max_paths=max_paths,
    )
    truncated = len(paths) >= max_paths
    if truncated:
        warnings.append(f"Path search reached max_paths={max_paths}; results may be truncated.")
    used_evidence_ids = {evidence_id for path in paths for evidence_id in path.evidence_ids}
    evidence = [item for item in relation_evidence(relations, company_id=target_entity_id) if item.evidence_id in used_evidence_ids]
    for item in evidence:
        item.used_by = [call.tool_call_id]

    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.OWNERSHIP_PENETRATION,
        status=ToolStatus.SUCCESS,
        data={
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "as_of_date": as_of_date.isoformat(),
            "max_depth": max_depth,
            "max_paths": max_paths,
            "relation_types": sorted(requested_relation_types),
            "data_source": dataset.source_name,
            "truncated": truncated,
            "paths": [path.model_dump() for path in paths],
            "summary": summarize_paths(paths),
            "message": f"ownership_penetration executed with {dataset.source_name} graph records",
        },
        evidence=evidence,
        warnings=warnings,
        metrics=ToolMetrics(execution_time_ms=0),
    )


def target_has_relations(relations, target_entity_id: str) -> bool:
    return any(relation.target_entity_id == target_entity_id or relation.source_entity_id == target_entity_id for relation in relations)


def failed_result(call: ToolCall, error_type: ErrorType, message: str, details: dict, warnings: list[str]) -> ToolResult:
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.OWNERSHIP_PENETRATION,
        status=ToolStatus.FAILED,
        data={},
        evidence=[],
        warnings=warnings,
        error=ToolError(error_type=error_type, message=message, retryable=False, details=details),
        metrics=ToolMetrics(execution_time_ms=0),
    )
