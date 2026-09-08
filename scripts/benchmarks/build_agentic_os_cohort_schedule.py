#!/usr/bin/env python3
"""Freeze a cohort and deterministic paired arm schedule for AgenticOS runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.discovery_cache import CohortCase, build_cohort_manifest, build_paired_schedule, write_frozen_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--cases", required=True, type=Path, help="JSONL: qid, stratum")
    parser.add_argument("--cohort-out", required=True, type=Path)
    parser.add_argument("--schedule-out", required=True, type=Path)
    parser.add_argument("--arms", nargs="+", required=True)
    parser.add_argument("--seeds", nargs="+", required=True, type=int)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--cache-phases",
        nargs="+",
        choices=("cold", "warm", "none"),
        default=("cold", "warm"),
        help="Cache phases to freeze into the schedule (default: cold warm).",
    )
    args = parser.parse_args()
    cases = [
        CohortCase(qid=str(item["qid"]), stratum=str(item.get("stratum", "unstratified")))
        for item in (json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip())
    ]
    cohort = build_cohort_manifest(dataset=args.dataset, cases=cases)
    schedule = build_paired_schedule(
        cohort=cohort,
        arms=args.arms,
        seeds=args.seeds,
        repetitions=args.repetitions,
        cache_phases=args.cache_phases,
    )
    write_frozen_json(args.cohort_out, cohort)
    write_frozen_json(args.schedule_out, {"schema_version": "seocho.agentic_os.schedule.v1", "cohort_digest": cohort["cohort_digest"], "entries": schedule})
    print(json.dumps({"cohort_digest": cohort["cohort_digest"], "entries": len(schedule)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
