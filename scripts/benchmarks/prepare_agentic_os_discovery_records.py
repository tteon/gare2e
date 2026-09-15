#!/usr/bin/env python3
"""Create reviewable per-QID neutral/semantic/ontology discovery records offline.

Arms per record (lab 007 / 015 / 016):

* ``b0_response`` — the graph's own schema answer (structure only).
* ``s1_response`` — B0 plus the controlled vocabulary (node types / relation
  names, or relation facets for RDF).
* ``o1_response`` — S1 plus relation *semantics*: direction, domain / range,
  meaning, property roles and, for RDF, the domain / range classes observed in
  the case graph itself. Nothing here is derived from the question or the
  answer; class labels equal to a forbidden term (answer, seed) are dropped.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from seocho.eval.agentic_graph_agents import _rdf_schema_slice
from seocho.eval.gtsqa import load_gtsqa_cases


_QID = re.compile(r"\((Q\d+)\)\s*$")


def _class_of(label: str) -> str | None:
    return label if _QID.search(label) else None


def _short(label: str) -> str:
    """Class label without the trailing (Qnnn) id — the agent needs the name, not the id."""
    return _QID.sub("", label).strip()


def _gtsqa_ontology(case, *, relation_cap: int = 48, class_cap: int = 2) -> dict:
    """S1 plus per-relation domain / range classes observed in the case graph.

    Classes come from the graph's own ``instance of (P31)`` triples, so the
    enrichment is data-derived and offline. Object kinds distinguish entity
    objects from literal / date values (an RDF-side "range" hint).
    """
    semantic = _semantic(case)
    forbidden = {str(x) for x in (*case.answer_ids, *case.seed_entities)}
    classes: dict[str, Counter] = defaultdict(Counter)
    for edge in case.graph_edges:
        if edge.relation_id == "P31" and edge.target_id not in forbidden:
            classes[edge.source_id][edge.target_label] += 1
    stats: dict[str, dict] = {}
    for edge in case.graph_edges:
        st = stats.setdefault(edge.relation_id, {"domain": Counter(), "range": Counter(), "object_kind": Counter(), "count": 0})
        st["count"] += 1
        for cls, n in classes.get(edge.source_id, {}).items():
            st["domain"][cls] += n
        if _class_of(edge.target_label):
            st["object_kind"]["entity"] += 1
            for cls, n in classes.get(edge.target_id, {}).items():
                st["range"][cls] += n
        else:
            st["object_kind"]["literal"] += 1
    relations = []
    for item in semantic["relations"][:relation_cap]:
        st = stats.get(item["id"])
        if st is None:
            relations.append(dict(item))
            continue
        compact = {k: v for k, v in item.items() if k != "preferred"}
        compact["domain"] = [_short(c) for c, _ in st["domain"].most_common(class_cap)]
        compact["range"] = [_short(c) for c, _ in st["range"].most_common(class_cap)]
        compact["object_kind"] = st["object_kind"].most_common(1)[0][0] if st["object_kind"] else "unknown"
        compact["edge_count"] = st["count"]
        relations.append(compact)
    return {
        **semantic,
        "context_kind": "ontology_enriched_relation_vocabulary",
        "semantics_source": "domain/range classes observed via instance-of (P31) triples in this case graph; counts are edge totals",
        "relations": relations,
    }


def _grbench_ontology(schema: dict, semantic: dict) -> dict:
    """S1 plus what each node type, relation and property *means* in the MAPLE projection."""
    return {
        **semantic,
        "context_kind": "ontology_enriched_lpg_projection",
        "node_types": [
            {"node_type": "paper", "meaning": "a physics paper", "name_property": "feature_title", "other_properties": ["feature_abstract", "feature_year", "feature_label"]},
            {"node_type": "author", "meaning": "a person who wrote papers", "name_property": "feature_name"},
            {"node_type": "venue", "meaning": "a journal or conference where papers appear", "name_property": "feature_name"},
        ],
        "relations": [
            {"relation_name": "author", "from": "paper", "to": "author", "meaning": "the paper was written by the author", "inverse": "paper"},
            {"relation_name": "paper", "from": "author", "to": "paper", "meaning": "the author wrote the paper", "inverse": "author"},
            {"relation_name": "venue", "from": "paper", "to": "venue", "meaning": "the paper appeared at the venue"},
            {"relation_name": "reference", "from": "paper", "to": "paper", "meaning": "the source paper cites the target paper (target is in the source's reference list)", "inverse": "cited_by"},
            {"relation_name": "cited_by", "from": "paper", "to": "paper", "meaning": "the target paper cites the source paper", "inverse": "reference"},
        ],
        "relation_encoding": "every relationship has type GRBENCH_REL; its relation_name property carries the name above and source_node_type the node_type of its start node; the two directions of a citation are stored as two relationships (reference from the citing paper, cited_by from the cited paper)",
        "property_semantics": {
            "feature_name": "display name of an author or venue (exact string, lower-case)",
            "feature_title": "paper title (exact string, lower-case)",
            "feature_year": "publication year of a paper, stored as a string",
            "feature_label": "JSON list of subject labels of a paper",
            "feature_abstract": "paper abstract text",
            "node_type": "one of paper, author, venue",
            "source_node_id": "the benchmark's original node id",
        },
        "query_idioms": [
            "anchor on feature_name (author, venue) or feature_title (paper) with = or CONTAINS; never scan a node_type alone",
            "count collaborators via paper -author-> author patterns; count citations via cited_by from the paper or reference into it",
            "all node and relationship patterns carry _workspace_id: $workspace_id",
        ],
    }


def _semantic(case) -> dict:
    neutral = _rdf_schema_slice(case)
    relations = []
    for item in neutral.get("relations", ()):
        label = str(item.get("label", ""))
        lower = label.casefold()
        facet = (
            "temporal" if any(word in lower for word in ("date", "year", "time"))
            else "location" if any(word in lower for word in ("place", "country", "located"))
            else "classification" if any(word in lower for word in ("type", "instance", "class"))
            else "relational"
        )
        relations.append({**item, "facet": facet, "preferred": label})
    return {
        **neutral,
        "context_kind": "controlled_relation_taxonomy",
        "facets": ["temporal", "location", "classification", "relational"],
        "relations": relations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--lane", choices=("gtsqa", "grbench"), default="gtsqa")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()
    rows = []
    if args.lane == "gtsqa":
        if args.parquet is None:
            parser.error("--parquet is required for --lane gtsqa")
        cases = load_gtsqa_cases(args.parquet, limit=args.limit)
        for case in cases:
            rows.append(
                {
                    "qid": str(case.case_id),
                    "b0_response": _rdf_schema_slice(case),
                    "s1_response": _semantic(case),
                    "o1_response": _gtsqa_ontology(case),
                    "forbidden_terms": list(case.answer_ids) + list(case.seed_entities),
                }
            )
    else:
        if args.questions is None:
            parser.error("--questions is required for --lane grbench")
        schema = {
            "nodes": [{"label": "GRBenchNode", "properties": ["id", "node_type", "source_node_id", "feature_*"]}],
            "relationships": [{"type": "GRBENCH_REL", "properties": ["relation_name", "source_node_type"]}],
            "query_scope": "Every node and relationship pattern must filter _workspace_id: $workspace_id.",
        }
        semantic = {
            **schema,
            "context_kind": "controlled_lpg_projection_taxonomy",
            "facets": ["node_type", "relation_name", "feature_property"],
            "taxonomy_hints": [
                {"concept": "author", "graph_node_type": "author"},
                {"concept": "paper", "graph_node_type": "paper"},
                {"concept": "venue", "graph_node_type": "venue"},
                {"concept": "author", "graph_relation_name": "author"},
                {"concept": "citation", "graph_relation_name": "cited_by"},
                {"concept": "reference", "graph_relation_name": "reference"},
                {"concept": "venue", "graph_relation_name": "venue"},
            ],
        }
        with args.questions.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                rows.append({"qid": str(item["qid"]), "b0_response": schema, "s1_response": semantic, "o1_response": _grbench_ontology(schema, semantic), "forbidden_terms": [str(item.get("answer", ""))]})
                if len(rows) >= args.limit:
                    break
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps({"records": len(rows), "out": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
