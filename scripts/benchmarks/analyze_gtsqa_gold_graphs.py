#!/usr/bin/env python3
"""Write a content-free true triple/topology profile of official GTSQA graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from seocho.eval.gtsqa import load_gtsqa_cases, profile_gtsqa_gold_graphs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = profile_gtsqa_gold_graphs(load_gtsqa_cases(args.parquet, limit=args.limit))
    report["source"] = {
        "parquet_name": args.parquet.name,
        "sha256": hashlib.sha256(args.parquet.read_bytes()).hexdigest(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "candidate_graph_edge_count", "gold_subgraph_edge_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
