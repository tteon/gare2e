#!/usr/bin/env python3
"""Create reviewable per-QID neutral/semantic discovery records offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.agentic_graph_agents import _rdf_schema_slice
from seocho.eval.gtsqa import load_gtsqa_cases


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
                rows.append({"qid": str(item["qid"]), "b0_response": schema, "s1_response": semantic, "forbidden_terms": [str(item.get("answer", ""))]})
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
