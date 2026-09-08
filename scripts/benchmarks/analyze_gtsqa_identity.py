#!/usr/bin/env python3
"""Write a content-free GTSQA entity/relation ambiguity report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from seocho.eval.gtsqa_identity import analyze_gtsqa_identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = analyze_gtsqa_identity(args.parquet, batch_size=args.batch_size)
    report["source"] = {"parquet_name": args.parquet.name, "sha256": hashlib.sha256(args.parquet.read_bytes()).hexdigest()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "graph_edge_count", "entity_surface_collision", "relation_surface_collision")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
