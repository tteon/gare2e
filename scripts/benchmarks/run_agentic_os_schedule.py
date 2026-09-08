#!/usr/bin/env python3
"""Execute a frozen paired schedule without silently reordering attempts.

The coordinator launches one fresh runner process per scheduled attempt. This
keeps every attempt independently recoverable and gives cold-cache control an
explicit hook. It records only correlation IDs, durations, exit classes and
digests; model output remains in each runner's private artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from seocho.eval.agentic_os_artifacts import (
    Correlation,
    append_jsonl,
    canonical_digest,
    require_fresh_outputs,
    write_manifest,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_LANE_ARMS = {
    "grbench-single": {"B0", "C-", "S1"},
    "grbench-multi": {"B0", "C-", "S1"},
    "gtsqa-single": {"B0", "C-", "S1", "S2a", "S2b", "S3", "S4"},
    "gtsqa-multi": {"B0", "S4"},
}
# The GRBench LPG projection and every Text2Cypher runner must agree on one
# runtime-valid workspace id.  raw16k-v6 queried ``grbench:maple:physics``
# while the database held ``grbench-maple-physics`` and returned zero rows for
# all 40 GRBench attempts, so the coordinator now passes it explicitly.
GRBENCH_WORKSPACE_ID = "grbench-maple-physics"

_LANE_RUNNERS = {
    "gtsqa-single": _REPO_ROOT / "scripts/benchmarks/run_gtsqa_text2sparql_react.py",
    "grbench-single": _REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py",
    "gtsqa-multi": _REPO_ROOT / "scripts/benchmarks/run_gtsqa_agents_sdk_handoff.py",
    "grbench-multi": _REPO_ROOT / "scripts/benchmarks/run_grbench_agents_sdk_handoff.py",
}


def _load_schedule(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "seocho.agentic_os.schedule.v1":
        raise ValueError("unsupported schedule schema")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("schedule has no entries")
    seen: set[tuple[str, str, int, str, int]] = set()
    normalized: list[dict[str, Any]] = []
    for raw in entries:
        entry = dict(raw)
        key = (
            str(entry["qid"]),
            str(entry["arm"]),
            int(entry["seed"]),
            str(entry["cache_phase"]),
            int(entry["repetition"]),
        )
        if key in seen:
            raise ValueError(f"duplicate scheduled attempt: {key}")
        seen.add(key)
        normalized.append(entry)
    return payload, normalized


def _attempt_id(lane: str, entry: dict[str, Any], *, execution_digest: str) -> str:
    identity = {
        "lane": lane,
        "qid": str(entry["qid"]),
        "arm": str(entry["arm"]),
        "seed": int(entry["seed"]),
        "cache_phase": str(entry["cache_phase"]),
        "repetition": int(entry["repetition"]),
        "paired_unit": str(entry["paired_unit"]),
        "execution_digest": execution_digest,
    }
    return canonical_digest(identity)[:20]


def _persist_runner_streams(attempt_root: Path, stdout: bytes, stderr: bytes) -> None:
    """Keep the runner's raw streams as private attempt artifacts.

    Only digests used to be recorded; V16 lost every GTSQA gate cell to a
    ``ModuleNotFoundError`` that no collected artifact could show.  The files
    are mode 0600 because runner stderr may echo model output.
    """
    attempt_root.mkdir(parents=True, exist_ok=True)
    for name, payload in (("runner.stdout.private.log", stdout), ("runner.stderr.private.log", stderr)):
        target = attempt_root / name
        target.write_bytes(payload)
        target.chmod(0o600)


def _runner_command(args: argparse.Namespace, entry: dict[str, Any], root: Path, run_id: str) -> list[str]:
    common = [
        "--base-url", args.base_url,
        "--model", args.model,
        "--run-id", run_id,
        "--arm", str(entry["arm"]),
        "--seed", str(entry["seed"]),
        "--repetition", str(entry["repetition"]),
        "--cache-phase", str(entry["cache_phase"]),
        "--enable-thinking", args.enable_thinking,
        "--max-output-tokens", str(args.max_output_tokens),
        "--stage", "main",
        "--limit", "1",
        "--prompt-profile", str(getattr(args, "prompt_profile", "baseline")),
    ]
    if args.gpu_sampling == "co-located":
        common.extend(("--gpu-samples-out", str(root / "metrics" / "gpu.csv")))
    if args.metrics_url:
        common.extend(("--metrics-url", args.metrics_url))
    if args.lane == "gtsqa-single":
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_gtsqa_text2sparql_react.py"),
            "--parquet", str(args.dataset),
            "--discovery-cache", str(args.discovery_cache),
            "--run-root", str(root),
            "--case-id", str(entry["qid"]),
            "--max-turns", str(args.max_turns),
        "--token-budget", str(args.token_budget),
            "--condition", args.condition,
            *common,
        ]
    elif args.lane == "grbench-single":
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py"),
            "--questions", str(args.dataset),
            "--discovery-cache", str(args.discovery_cache),
            "--receipt-out", str(root / "run-receipt.json"),
            "--predictions-out", str(root / "predictions.private.json"),
            "--manifest-out", str(root / "manifest.json"),
            "--host-samples-out", str(root / "metrics/agent-dozerdb-process.csv"),
            "--qid", str(entry["qid"]),
            "--max-turns", str(args.max_turns),
            "--token-budget", str(args.token_budget),
            "--condition", args.condition,
            "--workspace-id", str(getattr(args, "grbench_workspace_id", GRBENCH_WORKSPACE_ID)),
            *common,
        ]
        if args.db_pid is not None:
            command.extend(("--db-pid", str(args.db_pid)))
        if args.profile_query_plans:
            command.append("--profile-query-plans")
    elif args.lane == "gtsqa-multi":
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_gtsqa_agents_sdk_handoff.py"),
            "--parquet", str(args.dataset),
            "--discovery-cache", str(args.discovery_cache),
            "--run-root", str(root),
            "--case-id", str(entry["qid"]),
            "--max-turns", str(args.max_turns),
            "--token-budget", str(args.token_budget),
            *common,
        ]
    else:
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_grbench_agents_sdk_handoff.py"),
            "--questions", str(args.dataset),
            "--discovery-cache", str(args.discovery_cache),
            "--run-root", str(root),
            "--qid", str(entry["qid"]),
            "--max-turns", str(args.max_turns),
            "--token-budget", str(args.token_budget),
            "--workspace-id", str(getattr(args, "grbench_workspace_id", GRBENCH_WORKSPACE_ID)),
            *common,
        ]
        if args.db_pid is not None:
            command.extend(("--db-pid", str(args.db_pid)))
        if args.profile_query_plans:
            command.append("--profile-query-plans")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=tuple(_LANE_ARMS))
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--discovery-cache", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--metrics-url")
    parser.add_argument("--model", required=True)
    parser.add_argument("--enable-thinking", choices=("true", "false"), required=True)
    parser.add_argument(
        "--condition",
        choices=("closed_book", "tool_auto"),
        default="tool_auto",
        help="Single-agent evidence condition; multi-agent lanes require tool_auto.",
    )
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=512,
        help="Per-vLLM-request generation ceiling; distinct from the case token budget.",
    )
    parser.add_argument("--token-budget", type=int, default=8_192)
    parser.add_argument(
        "--prompt-profile",
        choices=("baseline", "strengthened"),
        default="baseline",
        help="Prompt-engineering arm forwarded to every lane runner (PE-vs-CE study).",
    )
    parser.add_argument(
        "--grbench-workspace-id",
        default=GRBENCH_WORKSPACE_ID,
        help="Workspace id the GRBench LPG projection was loaded under; passed to Text2Cypher runners.",
    )
    parser.add_argument(
        "--db-pid",
        type=int,
        help="Host PID of the co-located DozerDB process for GRBench process attribution.",
    )
    parser.add_argument(
        "--gpu-sampling",
        choices=("external", "co-located", "appliance"),
        default="external",
        help=(
            "Use co-located only when the runner owns the same-host GPU sampler. "
            "Use appliance when an enclosing co-located workload owns the shared "
            "GPU and vLLM-metrics samplers. Hybrid SSH-tunnel runs must use external."
        ),
    )
    parser.add_argument(
        "--profile-query-plans",
        action="store_true",
        help="Replay executable read-only GRBench queries under PROFILE outside E2E timing.",
    )
    parser.add_argument(
        "--cache-control-script",
        type=Path,
        help="Executable invoked as SCRIPT PHASE PAIRED_UNIT before every attempt",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--inter-attempt-gap-s",
        type=float,
        default=0.0,
        help=(
            "Idle gap after each serialized attempt so background metric samples "
            "can close a case bracket before the next case starts."
        ),
    )
    parser.add_argument(
        "--only-arm",
        help="Gate-only filter applied before execution; the frozen source schedule remains digest-bound.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        help="Gate-only positive limit after filtering; omit for the full frozen schedule.",
    )
    args = parser.parse_args()

    schedule, entries = _load_schedule(args.schedule)
    if args.only_arm is not None:
        entries = [entry for entry in entries if str(entry["arm"]) == args.only_arm]
    if args.max_attempts is not None:
        if args.max_attempts < 1:
            parser.error("--max-attempts must be positive")
        entries = entries[: args.max_attempts]
    if not entries:
        parser.error("schedule filters selected no attempts")
    unsupported = sorted({str(entry["arm"]) for entry in entries} - _LANE_ARMS[args.lane])
    if unsupported:
        raise ValueError(f"schedule contains unsupported {args.lane} arms: {unsupported}")
    if args.lane.endswith("-multi") and args.condition != "tool_auto":
        raise ValueError("multi-agent lanes require tool_auto")
    if args.profile_query_plans and args.lane.startswith("gtsqa"):
        raise ValueError("--profile-query-plans is available only for GRBench lanes")
    if args.gpu_sampling == "appliance" and args.metrics_url:
        raise ValueError("appliance telemetry owns vLLM sampling; omit --metrics-url")
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be positive")
    if args.inter_attempt_gap_s < 0:
        raise ValueError("--inter-attempt-gap-s must be non-negative")
    if args.condition == "closed_book" and any(str(entry["arm"]) != "B0" for entry in entries):
        raise ValueError("closed_book schedules must contain only the SEOCHO-free B0 arm")
    if any(str(entry["cache_phase"]) == "cold" for entry in entries):
        if args.cache_control_script is None or not args.cache_control_script.is_file():
            raise ValueError("cold schedule requires an explicit --cache-control-script")

    schedule_sha256 = hashlib.sha256(args.schedule.read_bytes()).hexdigest()
    schedule_digest = canonical_digest(schedule)
    dataset_sha256 = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    discovery_cache_sha256 = hashlib.sha256(args.discovery_cache.read_bytes()).hexdigest()
    harness_files = (
        Path(__file__).resolve(),
        _LANE_RUNNERS[args.lane],
        _REPO_ROOT / "src/seocho/eval/agentic_graph_agents.py",
        _REPO_ROOT / "src/seocho/eval/agentic_os_observability.py",
    )
    harness_sha256 = canonical_digest(
        {
            str(path.relative_to(_REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in harness_files
        }
    )
    execution_identity = {
        "lane": args.lane,
        "model": args.model,
        "reasoning_mode": "reasoning" if args.enable_thinking == "true" else "non_reasoning",
        "condition": args.condition,
        "max_turns": args.max_turns,
        "max_output_tokens": args.max_output_tokens,
        "token_budget": args.token_budget,
        "schedule_digest": schedule_digest,
        "dataset_sha256": dataset_sha256,
        "discovery_cache_sha256": discovery_cache_sha256,
        "profile_query_plans": args.profile_query_plans,
        "only_arm": args.only_arm,
        "max_attempts": args.max_attempts,
        "inter_attempt_gap_s": args.inter_attempt_gap_s,
        "harness_sha256": harness_sha256,
    }
    execution_digest = canonical_digest(execution_identity)
    manifest_path = args.run_root / "coordinator-manifest.json"
    lifecycle_path = args.run_root / "coordinator-receipts.jsonl"
    require_fresh_outputs((manifest_path, lifecycle_path))
    write_manifest(
        manifest_path,
        {
            "schema_version": "agentic_os.schedule_execution_manifest.v1",
            "lane": args.lane,
            "model": args.model,
            "reasoning_mode": "reasoning" if args.enable_thinking == "true" else "non_reasoning",
            "condition": args.condition,
            "execution_digest": execution_digest,
            "execution_identity": execution_identity,
            "schedule_sha256": schedule_sha256,
            "schedule_digest": schedule_digest,
            "dataset_sha256": dataset_sha256,
            "discovery_cache_sha256": discovery_cache_sha256,
            "attempt_count": len(entries),
            "gpu_sampling": args.gpu_sampling,
            "telemetry_scope": (
                "enclosing_same_host_background_sampler"
                if args.gpu_sampling == "appliance"
                else "runner_owned"
            ),
            "cache_control_script_sha256": (
                hashlib.sha256(args.cache_control_script.read_bytes()).hexdigest()
                if args.cache_control_script
                else None
            ),
        },
    )

    failures = 0
    for ordinal, entry in enumerate(entries):
        attempt_id = _attempt_id(args.lane, entry, execution_digest=execution_digest)
        attempt_root = args.run_root / "attempts" / f"{ordinal:06d}-{attempt_id}"
        run_id = f"{args.lane}-{attempt_id}"
        if args.cache_control_script is not None:
            control = subprocess.run(
                [str(args.cache_control_script), str(entry["cache_phase"]), str(entry["paired_unit"])],
                cwd=_REPO_ROOT,
                check=False,
                capture_output=True,
            )
            if control.returncode != 0:
                raise RuntimeError("cache-control script failed; refusing mislabeled cache phase")
        command = _runner_command(args, entry, attempt_root, run_id)
        started_unix_s = time.time()
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=_REPO_ROOT, check=False, capture_output=True)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        finished_unix_s = time.time()
        _persist_runner_streams(attempt_root, completed.stdout, completed.stderr)
        correlation = Correlation(
            run_id=run_id,
            case_id=str(entry["qid"]),
            arm=str(entry["arm"]),
            seed=int(entry["seed"]),
            repetition=int(entry["repetition"]),
            cache_phase=str(entry["cache_phase"]),
            role="schedule_coordinator",
        ).receipt_fields()
        append_jsonl(
            lifecycle_path,
            {
                **correlation,
                "schema_version": "agentic_os.schedule_attempt_lifecycle.v1",
                "paired_unit": str(entry["paired_unit"]),
                "schedule_ordinal": ordinal,
                "runner_command_digest": canonical_digest(command),
                "runner_exit_code": completed.returncode,
                "runner_wall_ms": elapsed_ms,
                "runner_started_unix_s": started_unix_s,
                "runner_finished_unix_s": finished_unix_s,
                "stdout_digest": hashlib.sha256(completed.stdout).hexdigest(),
                "stderr_digest": hashlib.sha256(completed.stderr).hexdigest(),
                "terminal_status": "ok" if completed.returncode == 0 else "runner_process_error",
            },
        )
        if completed.returncode != 0:
            failures += 1
            if not args.continue_on_error:
                break
        if args.inter_attempt_gap_s:
            time.sleep(args.inter_attempt_gap_s)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
