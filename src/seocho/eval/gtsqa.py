"""GTSQA adapters for case-scoped graph retrieval evaluation.

The official ``gtsqa-with-graphs`` parquet contains a noisy retrieval graph per
question and an independently marked answer subgraph.  A GTSQA case is thus a
valid retrieval-evaluation workload, unlike an answer-only QA benchmark.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .gold_graph import GoldGraphScore, score_gold_graph


_WIKIDATA_ID = re.compile(r"\((Q\d+|P\d+)\)$")
_GTSQA_BASE = "urn:seocho:gtsqa:"


def canonical_identifier(value: str) -> str:
    """Return a stable Wikidata ID from ``label (QID/PID)`` or raw ID."""
    cleaned = " ".join(value.split())
    match = _WIKIDATA_ID.search(cleaned)
    return match.group(1) if match else cleaned


@dataclass(frozen=True)
class GTSQAEdge:
    source_id: str
    relation_id: str
    target_id: str
    source_label: str
    relation_label: str
    target_label: str

    @classmethod
    def from_row(cls, row: Sequence[str]) -> "GTSQAEdge":
        if len(row) != 3:
            raise ValueError("GTSQA triples must have exactly three values")
        source, relation, target = (str(value) for value in row)
        return cls(
            source_id=canonical_identifier(source),
            relation_id=canonical_identifier(relation),
            target_id=canonical_identifier(target),
            source_label=source,
            relation_label=relation,
            target_label=target,
        )

    def to_triple(self) -> dict[str, str]:
        return {
            "source": self.source_id,
            "relation": self.relation_id,
            "target": self.target_id,
        }


@dataclass(frozen=True)
class GTSQACase:
    case_id: int
    question: str
    sparql_query: str
    seed_entities: tuple[str, ...]
    answer_ids: tuple[str, ...]
    graph_edges: tuple[GTSQAEdge, ...]
    gold_edges: tuple[GTSQAEdge, ...]
    n_hops: int
    graph_isomorphism: str
    test_types: tuple[str, ...]

    @property
    def workspace_id(self) -> str:
        # Runtime policy accepts portable tenant identifiers rather than URI
        # syntax. RDF graph IRIs remain ``urn:seocho:gtsqa:case:<id>`` and are
        # intentionally a separate namespace from the runtime workspace.
        return f"gtsqa-case-{self.case_id}"

    def receipt(self) -> dict[str, Any]:
        """Return a content-free case receipt suitable for tool traces."""
        return {
            "benchmark": "GTSQA",
            "case_id": self.case_id,
            "workspace_id": self.workspace_id,
            "graph_edge_count": len(self.graph_edges),
            "gold_edge_count": len(self.gold_edges),
            "seed_entity_ids": [canonical_identifier(value) for value in self.seed_entities],
            "answer_ids": list(self.answer_ids),
            "n_hops": self.n_hops,
            "graph_isomorphism": self.graph_isomorphism,
            "test_types": list(self.test_types),
        }


def case_from_mapping(row: Mapping[str, Any]) -> GTSQACase:
    """Parse a row from the official graph-bearing GTSQA configuration."""
    graph = row.get("graph")
    if not isinstance(graph, list):
        raise ValueError("GTSQA graph-bearing parquet requires a graph field")
    gold = row.get("full_answer_subgraph_wikikg2")
    if not isinstance(gold, list):
        raise ValueError("GTSQA row requires full_answer_subgraph_wikikg2")
    return GTSQACase(
        case_id=int(row["id"]),
        question=str(row["question"]),
        sparql_query=str(row["sparql_query"]),
        seed_entities=tuple(str(value) for value in row.get("seed_entities", ())),
        answer_ids=tuple(str(value) for value in row.get("all_answers_wikikg2", ())),
        graph_edges=tuple(GTSQAEdge.from_row(edge) for edge in graph),
        gold_edges=tuple(GTSQAEdge.from_row(edge) for edge in gold),
        n_hops=int(row["n_hops"]),
        graph_isomorphism=str(row["graph_isomorphism"]),
        test_types=tuple(str(value) for value in row.get("test_type", ())),
    )


def load_gtsqa_cases(
    path: str | Path,
    *,
    limit: int | None = None,
    case_ids: Iterable[int] | None = None,
) -> list[GTSQACase]:
    """Stream cases from the official graph-bearing parquet.

    ``case_ids`` preserves caller order. This avoids materializing the ~800 MB
    graph-bearing table merely to run a small frozen cohort.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive when provided")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - optional benchmark dependency
        raise RuntimeError("GTSQA loading requires the 'pyarrow' development dependency") from exc
    wanted_order = tuple(int(value) for value in case_ids or ())
    wanted = set(wanted_order)
    found: dict[int, GTSQACase] = {}
    sequential: list[GTSQACase] = []
    parquet = pq.ParquetFile(Path(path))
    for batch in parquet.iter_batches(batch_size=32):
        for row in batch.to_pylist():
            row_id = int(row["id"])
            if wanted and row_id not in wanted:
                continue
            case = case_from_mapping(row)
            if wanted:
                found[row_id] = case
                if len(found) == len(wanted):
                    break
            else:
                sequential.append(case)
                if limit is not None and len(sequential) >= limit:
                    break
        if (wanted and len(found) == len(wanted)) or (
            not wanted and limit is not None and len(sequential) >= limit
        ):
            break
    if wanted:
        missing = [case_id for case_id in wanted_order if case_id not in found]
        if missing:
            raise KeyError(f"GTSQA case IDs not found: {missing}")
        ordered = [found[case_id] for case_id in wanted_order]
        return ordered[:limit] if limit is not None else ordered
    return sequential


def score_gtsqa_retrieval(
    retrieved_edges: Iterable[Mapping[str, Any]], case: GTSQACase
) -> GoldGraphScore:
    """Compare retrieved edges to GTSQA's official Wikidata answer subgraph."""
    return score_gold_graph(
        list(retrieved_edges),
        [edge.to_triple() for edge in case.gold_edges],
        required_slots=("seed_entities", "answer_entities"),
        observed_slots=("seed_entities", "answer_entities"),
        required_provenance=(f"gtsqa:{case.case_id}",),
        observed_provenance=(f"gtsqa:{case.case_id}",),
    )


def profile_gtsqa_gold_graphs(cases: Iterable[GTSQACase]) -> dict[str, Any]:
    """Summarize true graph and gold-subgraph distributions without text output."""
    rows = list(cases)
    graph_counts = [len(case.graph_edges) for case in rows]
    gold_counts = [len(case.gold_edges) for case in rows]
    hop_counts: Counter[str] = Counter()
    topology_counts: Counter[str] = Counter()
    gold_relations: Counter[str] = Counter()
    graph_relations: Counter[str] = Counter()
    fully_contained = 0
    for case in rows:
        hop_counts[str(case.n_hops)] += 1
        topology_counts[case.graph_isomorphism] += 1
        gold = {tuple(edge.to_triple().values()) for edge in case.gold_edges}
        graph = {tuple(edge.to_triple().values()) for edge in case.graph_edges}
        fully_contained += gold.issubset(graph)
        gold_relations.update(edge.relation_id for edge in case.gold_edges)
        graph_relations.update(edge.relation_id for edge in case.graph_edges)

    def distribution(values: list[int]) -> dict[str, float | int]:
        return {
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
            "mean": round(sum(values) / len(values), 6) if values else 0.0,
        }

    return {
        "schema_version": "seocho.gtsqa_gold_graph_profile.v1",
        "case_count": len(rows),
        "candidate_graph_edge_count": distribution(graph_counts),
        "gold_subgraph_edge_count": distribution(gold_counts),
        "candidate_to_gold_edge_ratio_mean": round(
            sum(graph / gold for graph, gold in zip(graph_counts, gold_counts) if gold)
            / sum(gold > 0 for gold in gold_counts),
            6,
        ) if any(gold_counts) else 0.0,
        "gold_subgraph_contained_in_candidate_graph_rate": round(
            fully_contained / len(rows), 6
        ) if rows else 0.0,
        "by_hops": dict(sorted(hop_counts.items())),
        "by_graph_isomorphism": dict(sorted(topology_counts.items())),
        "gold_relation_frequency": dict(sorted(gold_relations.items())),
        "candidate_relation_frequency": dict(sorted(graph_relations.items())),
    }


def render_gtsqa_nquads(cases: Iterable[GTSQACase]) -> tuple[str, str]:
    """Render case-scoped RDF data plus an RDFS relation-label profile."""
    def iri(kind: str, identifier: str) -> str:
        safe = "".join(char for char in identifier if char.isalnum() or char in "-._~")
        if not safe:
            raise ValueError(f"cannot make IRI from {identifier!r}")
        return f"<{_GTSQA_BASE}{kind}:{safe}>"

    def literal(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    data: list[str] = []
    ontology: dict[str, str] = {}
    for case in cases:
        graph = f"<{_GTSQA_BASE}case:{case.case_id}>"
        for edge in case.graph_edges:
            data.append(
                f"{iri('entity', edge.source_id)} {iri('predicate', edge.relation_id)} {iri('entity', edge.target_id)} {graph} ."
            )
            ontology.setdefault(
                edge.relation_id,
                f"{iri('predicate', edge.relation_id)} <http://www.w3.org/2000/01/rdf-schema#label> {literal(edge.relation_label)} .",
            )
    return "\n".join(data) + "\n", "\n".join(ontology[key] for key in sorted(ontology)) + "\n"


__all__ = [
    "GTSQACase",
    "GTSQAEdge",
    "canonical_identifier",
    "case_from_mapping",
    "load_gtsqa_cases",
    "profile_gtsqa_gold_graphs",
    "render_gtsqa_nquads",
    "score_gtsqa_retrieval",
]
