#!/usr/bin/env python3
"""Build deterministic CSV/JSON/Markdown catalogs from AgenticOS run roots."""

from __future__ import annotations

import argparse
from pathlib import Path

from seocho.eval.agentic_os_catalog import build_catalog, write_catalog_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--baseline-arm", default="B0")
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--bootstrap-seed", type=int, default=0)
    parser.add_argument("--p99-min-n", type=int, default=100)
    parser.add_argument("--judge-scores", type=Path, help="Precomputed blinded score JSONL; no provider is called")
    parser.add_argument("--privacy-sentinels", type=Path, help="One injected telemetry canary per line")
    args = parser.parse_args()
    sentinels = []
    if args.privacy_sentinels:
        sentinels = [line.strip() for line in args.privacy_sentinels.read_text(encoding="utf-8").splitlines() if line.strip()]
    catalog, attempts, failures = build_catalog(
        args.run_roots,
        baseline_arm=args.baseline_arm,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
        p99_min_n=args.p99_min_n,
        judge_scores_path=args.judge_scores,
        privacy_sentinels=sentinels,
    )
    if not attempts:
        raise RuntimeError("no correlated attempts discovered; refusing an empty catalog")
    write_catalog_outputs(args.output_dir, catalog, attempts, failures)
    print(f"cataloged {len(attempts)} attempts into {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
