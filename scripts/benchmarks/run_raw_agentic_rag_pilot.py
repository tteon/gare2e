#!/usr/bin/env python3
"""Run the SEOCHO-free closed-book/tool-auto/plumbing pilot matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from seocho.eval.agentic_os_artifacts import append_jsonl, canonical_digest, require_fresh_outputs, write_manifest
from seocho.eval.raw_agentic_baseline import RAW_CONDITIONS, raw_condition_spec


def _attempt_root(root: Path, *, lane: str, condition: str, reasoning: str, repetition: int) -> Path:
    return root / lane / condition / reasoning / f"repetition-{repetition:02d}"


def _runner_command(
    args: argparse.Namespace,
    *,
    condition: str,
    reasoning: str,
    repetition: int,
    root: Path,
    run_id: str,
) -> list[str]:
    common = [
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--run-id",
        run_id,
        "--condition",
        condition,
        "--stage",
        "smoke",
        "--enable-thinking",
        "true" if reasoning == "reasoning" else "false",
        "--repetition",
        str(repetition),
        "--cache-phase",
        "none",
        "--max-turns",
        str(args.max_turns),
        "--limit",
        str(args.limit),
    ]
    if args.metrics_url:
        common.extend(("--metrics-url", args.metrics_url))
    if args.lane == "grbench":
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py"),
            "--questions",
            str(args.dataset),
            "--receipt-out",
            str(root / "run-receipt.json"),
            "--predictions-out",
            str(root / "predictions.private.json"),
            "--manifest-out",
            str(root / "manifest.json"),
            "--host-samples-out",
            str(root / "metrics" / "agent-dozerdb-process.csv"),
            *common,
        ]
        if args.db_pid:
            command.extend(("--db-pid", str(args.db_pid)))
        for case_id in args.case_id:
            command.extend(("--qid", str(case_id)))
        return command
    command = [
        sys.executable,
        str(_REPO_ROOT / "scripts/benchmarks/run_gtsqa_text2sparql_react.py"),
        "--parquet",
        str(args.dataset),
        "--run-root",
        str(root),
        *common,
    ]
    for case_id in args.case_id:
        command.extend(("--case-id", str(case_id)))
    return command


def _matrix(args: argparse.Namespace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for repetition in range(args.repetitions):
        for reasoning in args.reasoning:
            for condition in args.condition:
                spec = raw_condition_spec(condition, stage="smoke")
                identity = {
                    "lane": args.lane,
                    "condition": condition,
                    "reasoning": reasoning,
                    "repetition": repetition,
                    "model": args.model,
                    "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                    "case_ids": [str(value) for value in args.case_id],
                    "limit": args.limit,
                }
                entries.append(
                    {
                        **identity,
                        "run_id": f"raw-{args.lane}-{canonical_digest(identity)[:16]}",
                        "evidence_class": spec.evidence_class,
                    }
                )
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("grbench", "gtsqa"), required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--metrics-url")
    parser.add_argument("--model", required=True)
    parser.add_argument("--condition", action="append", choices=RAW_CONDITIONS, default=[])
    parser.add_argument(
        "--reasoning",
        action="append",
        choices=("reasoning", "non_reasoning"),
        default=[],
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--db-pid", type=int)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args(argv)
    if not args.dataset.is_file():
        raise FileNotFoundError(args.dataset)
    if args.limit < 1 or args.repetitions < 1 or args.max_turns < 1:
        raise ValueError("limit, repetitions and max-turns must be positive")
    args.condition = args.condition or list(RAW_CONDITIONS)
    args.reasoning = args.reasoning or ["reasoning", "non_reasoning"]
    entries = _matrix(args)
    manifest_path = args.run_root / "pilot-manifest.json"
    lifecycle_path = args.run_root / "pilot-lifecycle.jsonl"
    require_fresh_outputs((manifest_path, lifecycle_path))
    write_manifest(
        manifest_path,
        {
            "schema_version": "agentic_os.raw_pilot_manifest.v1",
            "lane": args.lane,
            "model": args.model,
            "endpoint_digest": hashlib.sha256(args.base_url.encode()).hexdigest(),
            "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "conditions": list(args.condition),
            "reasoning_modes": list(args.reasoning),
            "case_ids": [str(value) for value in args.case_id],
            "limit": args.limit,
            "repetitions": args.repetitions,
            "attempt_count": len(entries),
            "forced_plumbing_excluded_from_quality": True,
        },
    )
    failures = 0
    for entry in entries:
        root = _attempt_root(
            args.run_root,
            lane=args.lane,
            condition=str(entry["condition"]),
            reasoning=str(entry["reasoning"]),
            repetition=int(entry["repetition"]),
        )
        command = _runner_command(
            args,
            condition=str(entry["condition"]),
            reasoning=str(entry["reasoning"]),
            repetition=int(entry["repetition"]),
            root=root,
            run_id=str(entry["run_id"]),
        )
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=_REPO_ROOT, check=False, capture_output=True)
        append_jsonl(
            lifecycle_path,
            {
                "schema_version": "agentic_os.raw_pilot_lifecycle.v1",
                **entry,
                "runner_command_digest": canonical_digest(command),
                "runner_exit_code": completed.returncode,
                "runner_wall_ms": round((time.perf_counter() - started) * 1000, 3),
                "stdout_digest": hashlib.sha256(completed.stdout).hexdigest(),
                "stderr_digest": hashlib.sha256(completed.stderr).hexdigest(),
                "terminal_status": "ok" if completed.returncode == 0 else "runner_process_error",
            },
        )
        if completed.returncode != 0:
            failures += 1
            if not args.continue_on_error:
                break
    print(json.dumps({"attempts": len(entries), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
