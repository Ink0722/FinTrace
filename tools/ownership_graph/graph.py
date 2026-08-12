from datetime import date

import networkx as nx

from schemas.evidence import Evidence, EvidenceSource
from schemas.ownership import OwnershipEntity, OwnershipPath, OwnershipPathHop, OwnershipRelation


def is_relation_active(relation: OwnershipRelation, as_of_date: date) -> bool:
    if relation.valid_from and relation.valid_from > as_of_date:
        return False
    if relation.valid_to and relation.valid_to < as_of_date:
        return False
    return True


def build_graph(relations: list[OwnershipRelation], as_of_date: date, relation_types: set[str]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for relation in relations:
        if relation.relation_type not in relation_types:
            continue
        if not is_relation_active(relation, as_of_date):
            continue
        graph.add_edge(
            relation.source_entity_id,
            relation.target_entity_id,
            key=relation.edge_id,
            relation=relation,
        )
    return graph


def relation_evidence(relations: list[OwnershipRelation], company_id: str | None = None) -> list[Evidence]:
    evidence: list[Evidence] = []
    for relation in relations:
        evidence.append(
            Evidence(
                evidence_id=relation.evidence_id,
                evidence_type="ownership_relation",
                source=EvidenceSource(
                    document_id=relation.source_doc_id or f"DOC-{relation.evidence_id}",
                    company_id=company_id,
                    document_type="shareholder_record",
                    page=relation.page,
                    source_path=relation.source_path,
                ),
                fact={
                    "edge_id": relation.edge_id,
                    "source_entity_id": relation.source_entity_id,
                    "target_entity_id": relation.target_entity_id,
                    "relation_type": relation.relation_type,
                    "ratio": relation.ratio,
                    "valid_from": relation.valid_from.isoformat() if relation.valid_from else None,
                    "valid_to": relation.valid_to.isoformat() if relation.valid_to else None,
                },
            )
        )
    return evidence


def find_paths(
    entities: list[OwnershipEntity],
    relations: list[OwnershipRelation],
    source_entity_id: str,
    target_entity_id: str,
    as_of_date: date,
    max_depth: int,
    relation_types: set[str],
    max_paths: int = 50,
) -> list[OwnershipPath]:
    entity_by_id = {entity.entity_id: entity for entity in entities}
    relation_by_edge = {relation.edge_id: relation for relation in relations}
    graph = build_graph(relations, as_of_date, relation_types)
    if source_entity_id not in graph or target_entity_id not in graph:
        return []

    paths: list[OwnershipPath] = []
    relevant_nodes = target_relevant_nodes(graph, target_entity_id, max_depth)
    if source_entity_id not in relevant_nodes:
        return []

    for nodes in bounded_simple_paths(graph, source_entity_id, target_entity_id, max_depth=max_depth, max_paths=max_paths, allowed_nodes=relevant_nodes):
        edge_options: list[OwnershipRelation] = []
        for source, target in zip(nodes, nodes[1:]):
            edge_data = graph.get_edge_data(source, target) or {}
            relation = sorted(
                (data["relation"] for data in edge_data.values()),
                key=lambda item: item.edge_id,
            )[0]
            edge_options.append(relation)

        relation_type_values = {relation.relation_type for relation in edge_options}
        if relation_type_values == {"OWNS"}:
            path_type = "holding"
            indirect_ratio = 1.0
            for relation in edge_options:
                if relation.ratio is None:
                    indirect_ratio = None
                    break
                indirect_ratio *= relation.ratio
        elif relation_type_values <= {"CONTROLS", "VOTING_RIGHTS", "ACTS_IN_CONCERT"}:
            path_type = "control"
            indirect_ratio = None
        else:
            path_type = "mixed"
            indirect_ratio = None
        has_control_path = bool(relation_type_values & {"CONTROLS", "VOTING_RIGHTS", "ACTS_IN_CONCERT"})

        hops = [
            OwnershipPathHop(
                edge_id=relation.edge_id,
                source_entity_id=relation.source_entity_id,
                source_name=entity_by_id[relation.source_entity_id].name,
                target_entity_id=relation.target_entity_id,
                target_name=entity_by_id[relation.target_entity_id].name,
                relation_type=relation.relation_type,
                ratio=relation.ratio,
                evidence_id=relation.evidence_id,
            )
            for relation in edge_options
        ]
        paths.append(
            OwnershipPath(
                path_type=path_type,
                nodes=nodes,
                hops=hops,
                indirect_ratio=indirect_ratio,
                has_control_path=has_control_path,
                evidence_ids=[relation.evidence_id for relation in edge_options],
            )
        )

    return sorted(
        paths,
        key=path_sort_key,
    )


def target_relevant_nodes(graph: nx.MultiDiGraph, target_entity_id: str, max_depth: int) -> set[str]:
    reversed_graph = graph.reverse(copy=False)
    relevant = {target_entity_id}
    frontier = {target_entity_id}
    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier.update(reversed_graph.successors(node))
        next_frontier -= relevant
        if not next_frontier:
            break
        relevant.update(next_frontier)
        frontier = next_frontier
    return relevant


def bounded_simple_paths(
    graph: nx.MultiDiGraph,
    source_entity_id: str,
    target_entity_id: str,
    *,
    max_depth: int,
    max_paths: int,
    allowed_nodes: set[str],
) -> list[list[str]]:
    paths: list[list[str]] = []
    stack: list[tuple[str, list[str]]] = [(source_entity_id, [source_entity_id])]
    while stack and len(paths) < max_paths:
        node, path = stack.pop()
        if len(path) - 1 >= max_depth:
            continue
        successors = sorted(graph.successors(node), reverse=True)
        for successor in successors:
            if successor not in allowed_nodes or successor in path:
                continue
            next_path = [*path, successor]
            if successor == target_entity_id:
                paths.append(next_path)
                if len(paths) >= max_paths:
                    break
            else:
                stack.append((successor, next_path))
    return paths


def path_sort_key(path: OwnershipPath) -> tuple:
    evidence_complete = len(path.evidence_ids) == len(path.hops)
    return (
        not path.has_control_path,
        -(path.indirect_ratio or 0),
        len(path.hops),
        not evidence_complete,
        path.path_type,
    )


def summarize_paths(paths: list[OwnershipPath]) -> dict:
    holding_paths = [path for path in paths if path.path_type == "holding"]
    return {
        "path_count": len(paths),
        "has_control_path": any(path.has_control_path for path in paths),
        "shortest_path": paths[0].model_dump() if paths else None,
        "highest_ratio_path": max(holding_paths, key=lambda path: path.indirect_ratio or 0).model_dump()
        if holding_paths
        else None,
        "evidence_complete_path": next((path.model_dump() for path in paths if len(path.evidence_ids) == len(path.hops)), None),
    }
