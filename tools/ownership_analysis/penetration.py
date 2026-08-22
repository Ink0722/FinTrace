from __future__ import annotations

from collections import deque

from tools.ownership_analysis.repository import HolderRecord, OwnershipRepository


def find_holding_paths(
    repository: OwnershipRepository,
    *,
    source_entity_id: str,
    target_entity_id: str,
    as_of_date: str,
    knowledge_cutoff: str | None,
    max_depth: int,
    max_paths: int,
) -> tuple[list[dict], list[HolderRecord], dict]:
    queue = deque([(source_entity_id, [source_entity_id], [])])
    paths: list[dict] = []
    used_records: dict[str, HolderRecord] = {}
    expanded_nodes = 0
    max_depth_reached = False

    while queue and len(paths) < max_paths:
        node, nodes, records = queue.popleft()
        depth = len(records)
        if depth >= max_depth:
            max_depth_reached = True
            continue
        expanded_nodes += 1
        outgoing = repository.outgoing_holdings(node, as_of=as_of_date, knowledge_cutoff=knowledge_cutoff)
        for record in outgoing:
            next_node = record.target_company_id
            if next_node in nodes:
                continue
            next_nodes = [*nodes, next_node]
            next_records = [*records, record]
            if next_node == target_entity_id:
                path = _path_to_dict(repository, len(paths) + 1, next_nodes, next_records)
                paths.append(path)
                for item in next_records:
                    used_records[item.record_id] = item
                if len(paths) >= max_paths:
                    break
            else:
                queue.append((next_node, next_nodes, next_records))

    paths.sort(key=lambda item: (item["depth"], -(item["path_ratio"] or 0), item["path_id"]))
    summary = {
        "expanded_nodes": expanded_nodes,
        "max_depth_reached": max_depth_reached,
        "max_paths_reached": len(paths) >= max_paths,
    }
    return paths, list(used_records.values()), summary


def _path_to_dict(repository: OwnershipRepository, index: int, nodes: list[str], records: list[HolderRecord]) -> dict:
    ratio = 1.0
    edges = []
    for source, target, record in zip(nodes, nodes[1:], records):
        ratio *= record.holding_ratio
        edges.append(
            {
                "edge_id": record.record_id,
                "source_entity_id": source,
                "source_name": repository.node_name(source),
                "target_entity_id": target,
                "target_name": repository.node_name(target),
                "relation_type": "OWNS",
                "holding_ratio": record.holding_ratio,
                "holder_end_date": record.holder_end_date,
                "announcement_date": record.announcement_date,
                "evidence_id": record.evidence_id,
            }
        )
    return {
        "path_id": f"PATH-{index:03d}",
        "depth": len(edges),
        "nodes": [{"entity_id": node, "name": repository.node_name(node)} for node in nodes],
        "edges": edges,
        "path_ratio": ratio,
        "evidence_ids": [record.evidence_id for record in records],
    }
