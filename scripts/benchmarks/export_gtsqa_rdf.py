#!/usr/bin/env python3
"""Export official GTSQA retrieval graphs as case-scoped RDF N-Quads.

The resulting graph is local benchmark state.  It contains no generated facts:
every data triple comes directly from the official GTSQA candidate graph.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from seocho.eval.gtsqa import load_gtsqa_cases, render_gtsqa_nquads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--nquads-out", type=Path, required=True)
    parser.add_argument("--ontology-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args()
    cases = load_gtsqa_cases(args.parquet, limit=args.limit)
    nquads, ontology = render_gtsqa_nquads(cases)
    for path, content in ((args.nquads_out, nquads), (args.ontology_out, ontology)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    receipt = {
        "schema_version": "seocho.gtsqa_rdf_export.v1",
        "case_count": len(cases),
        "graph_edge_count": sum(len(case.graph_edges) for case in cases),
        "nquads_sha256": hashlib.sha256(nquads.encode()).hexdigest(),
        "ontology_sha256": hashlib.sha256(ontology.encode()).hexdigest(),
        "cases": [case.receipt() for case in cases],
    }
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in ("case_count", "graph_edge_count", "nquads_sha256", "ontology_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
