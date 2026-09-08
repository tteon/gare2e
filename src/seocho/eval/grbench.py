"""Adapters for GRBench / Graph-CoT text-attributed graphs.

GRBench's official ``graph.json`` files are property graphs encoded as JSON,
not Cypher databases.  This module makes their projection explicit and keeps
the benchmark facts unchanged: source graph properties become ``feature_*``
properties and every source adjacency becomes a directed ``GRBENCH_REL``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")


def safe_property_name(name: str) -> str:
    """Return a deterministic, Cypher-safe feature property name."""
    normalized = _SAFE_NAME.sub("_", str(name).strip()).strip("_").lower()
    return f"feature_{normalized or 'value'}"


@dataclass(frozen=True)
class GRBenchQuestion:
    qid: str
    question: str
    answer: str


def load_grbench_questions(path: str | Path, *, limit: int | None = None) -> list[GRBenchQuestion]:
    """Read the official JSONL question/answer file."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive when provided")
    rows: list[GRBenchQuestion] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            rows.append(
                GRBenchQuestion(
                    qid=str(item["qid"]),
                    question=str(item["question"]),
                    answer=str(item["answer"]),
                )
            )
            if limit and len(rows) >= limit:
                break
    return rows


def iter_grbench_nodes(graph: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield node projection rows without making a second graph-sized copy."""
    for raw_type, raw_nodes in graph.items():
        if not isinstance(raw_nodes, Mapping):
            raise ValueError(f"GRBench node group {raw_type!r} is not a mapping")
        node_type = str(raw_type).removesuffix("_nodes")
        for raw_id, raw_node in raw_nodes.items():
            node_id = str(raw_id)
            item = raw_node if isinstance(raw_node, Mapping) else {}
            features = item.get("features", {})
            if not isinstance(features, Mapping):
                features = {}
            properties: dict[str, Any] = {"node_type": node_type, "source_node_id": node_id}
            for key, value in features.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    properties[safe_property_name(str(key))] = value
                else:
                    properties[safe_property_name(str(key))] = json.dumps(
                        value, ensure_ascii=False, sort_keys=True
                    )
            yield {"id": node_id, "label": "GRBenchNode", "properties": properties}


def iter_grbench_relationships(graph: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield adjacency projection rows; source graphs guarantee global IDs."""
    for raw_type, raw_nodes in graph.items():
        if not isinstance(raw_nodes, Mapping):
            raise ValueError(f"GRBench node group {raw_type!r} is not a mapping")
        source_type = str(raw_type).removesuffix("_nodes")
        for raw_id, raw_node in raw_nodes.items():
            item = raw_node if isinstance(raw_node, Mapping) else {}
            neighbors = item.get("neighbors", {})
            if not isinstance(neighbors, Mapping):
                continue
            for relation_name, target_ids in neighbors.items():
                if not isinstance(target_ids, list):
                    continue
                for raw_target in target_ids:
                    yield {
                        "source": str(raw_id),
                        "target": str(raw_target),
                        "type": "GRBENCH_REL",
                        "source_label": "GRBenchNode",
                        "target_label": "GRBenchNode",
                        "properties": {
                            "relation_name": str(relation_name),
                            "source_node_type": source_type,
                        },
                    }


def graph_projection_rows(
    graph: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert an official in-memory GRBench graph to SEOCHO LPG write rows.

    A single stable node label and relationship type make the baseline schema
    independent of any later taxonomy choice.  Original types and adjacency
    names remain regular data properties for Text2Cypher to filter.
    """
    return list(iter_grbench_nodes(graph)), list(iter_grbench_relationships(graph))


def graph_schema_receipt(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Return a content-free schema/topology receipt for a GRBench graph."""
    # Do not call ``graph_projection_rows`` here.  The official graph files are
    # multi-gigabyte JSON objects, and materialising projected nodes/edges just
    # to produce a receipt can double their already substantial RSS footprint.
    node_types: Counter[str] = Counter()
    relation_names: Counter[str] = Counter()
    feature_names: set[str] = set()
    node_count = 0
    edge_count = 0
    for raw_type, raw_nodes in graph.items():
        if not isinstance(raw_nodes, Mapping):
            raise ValueError(f"GRBench node group {raw_type!r} is not a mapping")
        node_type = str(raw_type).removesuffix("_nodes")
        node_types[node_type] += len(raw_nodes)
        node_count += len(raw_nodes)
        for raw_node in raw_nodes.values():
            item = raw_node if isinstance(raw_node, Mapping) else {}
            features = item.get("features", {})
            if isinstance(features, Mapping):
                feature_names.update(safe_property_name(str(key)) for key in features)
            neighbors = item.get("neighbors", {})
            if not isinstance(neighbors, Mapping):
                continue
            for relation_name, target_ids in neighbors.items():
                if isinstance(target_ids, list):
                    relation_names[str(relation_name)] += len(target_ids)
                    edge_count += len(target_ids)
    topology = {
        "node_count": node_count,
        "edge_count": edge_count,
        "node_types": dict(sorted(node_types.items())),
        "relation_names": dict(sorted(relation_names.items())),
        "feature_names": sorted(feature_names),
    }
    digest = hashlib.sha256(
        json.dumps(topology, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "seocho.grbench_graph_receipt.v1",
        "projection": {
            "node_label": "GRBenchNode",
            "relationship_type": "GRBENCH_REL",
            "source_type_property": "node_type",
            "relationship_name_property": "relation_name",
        },
        "topology": topology,
        "schema_digest": digest,
    }


def batched(values: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    """Yield fixed-size write batches without retaining another full copy."""
    if size < 1:
        raise ValueError("size must be positive")
    batch: list[dict[str, Any]] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


__all__ = [
    "GRBenchQuestion",
    "batched",
    "graph_projection_rows",
    "graph_schema_receipt",
    "iter_grbench_nodes",
    "iter_grbench_relationships",
    "load_grbench_questions",
    "safe_property_name",
]
