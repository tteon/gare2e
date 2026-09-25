#!/usr/bin/env python3
"""Content-free diagnostic summary; never promotes a pilot to a throughput result."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any


def summarize(root: Path) -> dict[str, Any]:
    cells = []
    fingerprints: dict[str, dict[str, str]] = {}
    for path in sorted(root.glob("run-*/run-receipt.json")):
        receipt = json.loads(path.read_text())
        cases = receipt["cases"]
        queries = [
            json.loads(line)
            for file in path.parent.rglob("*.queries.jsonl")
            for line in file.read_text().splitlines()
            if line.strip()
        ]
        timed = [q for q in queries if q["scope"] == "timed_execution"]
        times = [c.get("time", {}).get("wall_ms", c.get("wall_ms")) for c in cases]
        tools = [t for c in cases for t in c.get("tools", [])]
        predictions = path.parent / "predictions.private.json"
        if predictions.exists():
            fingerprints[path.parent.name] = {
                str(qid): hashlib.sha256(str(item["prediction"]).encode()).hexdigest()
                for qid, item in json.loads(predictions.read_text()).items()
            }
        cells.append(
            {
                "condition": path.parent.name,
                "cases": len(cases),
                "case_ids": [str(c.get("case_id") or c.get("qid")) for c in cases],
                "wall_ms": times,
                "mean_wall_ms": statistics.mean(times)
                if times and all(t is not None for t in times)
                else None,
                "tool_calls": len(tools) if tools else None,
                "query_attempts": len(timed) if queries else None,
                "db_submissions": sum(q["db_queries_executed"] for q in timed)
                if queries
                else None,
                "reuses": sum(q["outcome"] == "reused" for q in timed)
                if queries
                else None,
                "failed_attempts": sum(q["outcome"] == "error" for q in timed)
                if queries
                else None,
                "failure_classes": sorted(
                    {q["error_class"] for q in timed if q.get("error_class")}
                ),
                "profile_replays": sum(
                    q["scope"] == "diagnostic_replay" for q in queries
                ),
                "all_final": all(c.get("terminal") == "final" for c in cases),
                "answer_quality": "unscored",
            }
        )
    parity = []
    for lane in ("graphcot", "text2cypher"):
        baseline = fingerprints.get(f"run-{lane}-0-raw", {})
        for index, mode in (
            (1, "exact_result_reuse"),
            (2, "exact_result_reuse"),
            (3, "raw"),
        ):
            condition = f"run-{lane}-{index}-{mode}"
            candidate = fingerprints.get(condition, {})
            common = baseline.keys() & candidate.keys()
            parity.append(
                {
                    "condition": condition,
                    "paired_cases": len(common),
                    "identical_answer_text": sum(
                        baseline[q] == candidate[q] for q in common
                    ),
                    "quality_claim": False,
                }
            )
    fixed = json.loads((root / "fixed-gate.json").read_text())
    fixed_summary = [
        {
            "condition": cell["condition"],
            "mode": cell["mode"],
            "median_lookup_ms": statistics.median(cell["latency_ms"]),
            "total_lookup_ms": sum(cell["latency_ms"]),
        }
        for cell in fixed["conditions"]
    ]
    return {
        "schema_version": "gare2e.graph_evidence_pilot_summary.v1",
        "cells": cells,
        "fixed_lookup": fixed_summary,
        "fixed_result_parity": fixed["passed"],
        "answer_text_parity": parity,
        "limitations": [
            "K=1 diagnostic; no throughput or p99 claim.",
            "Uncontrolled warm caches and fixed cell order.",
            "Identical output text is not answer correctness.",
            "No cache hit means timing changes cannot be attributed to result reuse.",
            "Unknown observations remain null; PROFILE replays are separate.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a fresh summary path")
    result = summarize(args.pilot_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "cells": len(result["cells"]),
                "fixed_result_parity": result["fixed_result_parity"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
