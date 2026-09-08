"""Streaming identity/label-ambiguity analysis for graph-bearing GTSQA parquet."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .gtsqa import canonical_identifier


def _label(value: str) -> str:
    cleaned = " ".join(str(value).split())
    marker = cleaned.rfind(" (")
    return cleaned[:marker].casefold() if marker > 0 else cleaned.casefold()


def analyze_gtsqa_identity(path: str | Path, *, batch_size: int = 16) -> dict[str, Any]:
    """Measure label ambiguity without loading the complete graph into memory.

    Q/P IDs are the authoritative identities. A repeated surface label mapped to
    multiple IDs is an exact ambiguity signal; differing predicate neighborhoods
    are a conservative semantic-separation proxy, not a type-system assertion.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("GTSQA identity analysis requires pyarrow") from exc
    entity_label_ids: dict[str, set[str]] = defaultdict(set)
    entity_id_labels: dict[str, set[str]] = defaultdict(set)
    relation_label_ids: dict[str, set[str]] = defaultdict(set)
    relation_id_labels: dict[str, set[str]] = defaultdict(set)
    entity_relations: dict[str, set[str]] = defaultdict(set)
    graph_edges = 0
    case_count = 0
    reader = pq.ParquetFile(Path(path))
    for batch in reader.iter_batches(batch_size=batch_size, columns=["id", "graph"]):
        for row in batch.to_pylist():
            case_count += 1
            for triple in row["graph"]:
                source, relation, target = (str(value) for value in triple)
                source_id, target_id, relation_id = (
                    canonical_identifier(source), canonical_identifier(target), canonical_identifier(relation)
                )
                source_label, target_label, relation_label = _label(source), _label(target), _label(relation)
                entity_label_ids[source_label].add(source_id)
                entity_label_ids[target_label].add(target_id)
                entity_id_labels[source_id].add(source_label)
                entity_id_labels[target_id].add(target_label)
                relation_label_ids[relation_label].add(relation_id)
                relation_id_labels[relation_id].add(relation_label)
                entity_relations[source_id].add(relation_id)
                entity_relations[target_id].add(relation_id)
                graph_edges += 1

    entity_collisions = {label: ids for label, ids in entity_label_ids.items() if len(ids) > 1}
    relation_collisions = {label: ids for label, ids in relation_label_ids.items() if len(ids) > 1}
    signature_pairs = 0
    separated_pairs = 0
    for ids in entity_collisions.values():
        ordered = sorted(ids)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                signature_pairs += 1
                union = entity_relations[left] | entity_relations[right]
                similarity = len(entity_relations[left] & entity_relations[right]) / len(union) if union else 1.0
                separated_pairs += similarity < 0.1

    def variants(mapping: Mapping[str, set[str]]) -> dict[str, int]:
        return {
            "unique_keys": len(mapping),
            "keys_with_multiple_values": sum(len(values) > 1 for values in mapping.values()),
            "max_values_per_key": max((len(values) for values in mapping.values()), default=0),
        }

    return {
        "schema_version": "seocho.gtsqa_identity_ambiguity.v1",
        "case_count": case_count,
        "graph_edge_count": graph_edges,
        "entity_surface_to_qid": variants(entity_label_ids),
        "qid_to_entity_surface": variants(entity_id_labels),
        "relation_surface_to_pid": variants(relation_label_ids),
        "pid_to_relation_surface": variants(relation_id_labels),
        "entity_surface_collision": {
            "surface_count": len(entity_collisions),
            "distinct_qid_count": len({qid for ids in entity_collisions.values() for qid in ids}),
            "pair_count": signature_pairs,
            "relation_signature_jaccard_lt_0_1_count": separated_pairs,
            "relation_signature_jaccard_lt_0_1_rate": round(separated_pairs / signature_pairs, 6) if signature_pairs else 0.0,
        },
        "relation_surface_collision": {
            "surface_count": len(relation_collisions),
            "distinct_pid_count": len({pid for ids in relation_collisions.values() for pid in ids}),
        },
        "limitations": [
            "QID/PID identity is authoritative; this does not infer a merge decision.",
            "Relation-neighborhood separation is a semantic-divergence proxy, not a Wikidata type assertion.",
        ],
    }


__all__ = ["analyze_gtsqa_identity"]
