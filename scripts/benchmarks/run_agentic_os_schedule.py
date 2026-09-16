#!/usr/bin/env python3
"""Execute a frozen paired schedule without silently reordering attempts.

By default the coordinator launches one fresh process per attempt. Explicit
--workers / --runner-mode persistent options use a dynamic FIFO for warm
GRBench single-agent schedules; cold-cache control stays serial. It records only correlation IDs, durations, exit classes and
digests; model output remains in each runner's private artifacts.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading
import hashlib
import json
import math
import resource
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic_os_schedule_worker import PersistentRunner  # noqa: E402
_LANE_ARMS = {
    "grbench-replay": {"S1"},
    "grbench-single": {"B0", "C-", "S1", "O1"},
    "grbench-graphcot": {"B0", "O1"},  # Graph-CoT hop actions (lab 017); B0 = tool signatures + vocabulary, O1 = + relation semantics
    "grbench-router": {"V0", "V0A"},  # lab 019: arm = router policy; the router picks the context arm per question
    "grbench-grammar": {"B0", "S1", "O1"},  # lab 020: query-first plain-response Text2Cypher, optionally grammar-constrained (xgrammar)
    "grbench-multi": {"B0", "C-", "S1"},
    "gtsqa-single": {"B0", "C-", "S1", "S2a", "S2b", "S3", "S4", "O1"},
    "gtsqa-multi": {"B0", "S4"},
}
# The GRBench LPG projection and every Text2Cypher runner must agree on one
# runtime-valid workspace id.  raw16k-v6 queried ``grbench:maple:physics``
# while the database held ``grbench-maple-physics`` and returned zero rows for
# all 40 GRBench attempts, so the coordinator now passes it explicitly.
GRBENCH_WORKSPACE_ID = "grbench-maple-physics"

_LANE_RUNNERS = {
    "grbench-replay": _REPO_ROOT / "scripts/benchmarks/run_agentic_os_fixed_replay.py",
    "gtsqa-single": _REPO_ROOT / "scripts/benchmarks/run_gtsqa_text2sparql_react.py",
    "grbench-single": _REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py",
    "grbench-graphcot": _REPO_ROOT / "scripts/benchmarks/run_grbench_graphcot_react.py",
    "grbench-router": _REPO_ROOT / "scripts/benchmarks/run_grbench_router.py",
    "grbench-grammar": _REPO_ROOT / "scripts/benchmarks/run_grbench_grammar_cypher.py",
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
    elif args.lane in ("grbench-single", "grbench-router", "grbench-grammar", "grbench-replay"):
        command = [
            sys.executable,
            str(_LANE_RUNNERS[args.lane]),
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
        if args.profile_query_plans and args.lane == "grbench-single":
            command.append("--profile-query-plans")
        if getattr(args, "zero_row_hint", False) and args.lane in ("grbench-single", "grbench-grammar"):
            command.append("--zero-row-hint")   # the router decides the hint per plan
        if args.lane == "grbench-grammar":
            command.extend(("--constrained", str(getattr(args, "constrained", None) or "xgrammar")))
        # Result-size axis (lab 014): the executor's LIMIT / byte caps, forwarded
        # only when set so frozen defaults (20 rows / 12 000 bytes) stay in force.
        if getattr(args, "row_cap", None) is not None:
            command.extend(("--row-cap", str(args.row_cap)))
        if getattr(args, "byte_cap", None) is not None:
            command.extend(("--byte-cap", str(args.byte_cap)))
    elif args.lane == "grbench-graphcot":
        command = [
            sys.executable,
            str(_REPO_ROOT / "scripts/benchmarks/run_grbench_graphcot_react.py"),
            "--questions", str(args.dataset),
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
        if getattr(args, "row_cap", None) is not None:
            command.extend(("--row-cap", str(args.row_cap)))
        if getattr(args, "byte_cap", None) is not None:
            command.extend(("--byte-cap", str(args.byte_cap)))
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
    if args.lane == "grbench-replay":
        command.extend(("--replay-mode", getattr(args, "replay_mode", "tool-stub")))
    if args.lane == "grbench-grammar":
        command.extend(("--constrained-ratio", str(getattr(args, "constrained_ratio", 1.0))))
    if getattr(args, "record_replay", False):
        command.extend(("--replay-out", str(root / "replay.private.jsonl")))
    return command



def _run_dynamic(
    args: argparse.Namespace, entries: list[dict[str, Any]],
    execution_digest: str, lifecycle_path: Path,
) -> int:
    """Refill the first free worker from the frozen FIFO, preserving ordinals."""
    local = threading.local()
    runners: list[PersistentRunner] = []
    runner_lock = threading.Lock()
    enqueued = time.time()
    dispatches: list[float] = []
    rows: list[dict[str, Any]] = []

    def execute(ordinal: int, dispatched: float, pending: int) -> dict[str, Any]:
        entry = entries[ordinal]
        attempt_id = _attempt_id(args.lane, entry, execution_digest=execution_digest)
        root = args.run_root / "attempts" / f"{ordinal:06d}-{attempt_id}"
        run_id = f"{args.lane}-{attempt_id}"
        command = _runner_command(args, entry, root, run_id)
        start = time.time()
        clock = time.perf_counter()
        try:
            if args.runner_mode == "persistent":
                if not hasattr(local, "runner"):
                    # Popen temporarily owns more descriptors than its two IPC
                    # pipes. Serialize startup, not task execution, so high K
                    # does not multiply those transient descriptors.
                    with runner_lock:
                        local.runner = PersistentRunner(_REPO_ROOT, native_threads=getattr(args, 'worker_native_threads', None))
                        runners.append(local.runner)
                result = local.runner.run(command, root)
            else:
                completed = subprocess.run(command, cwd=_REPO_ROOT, check=False, capture_output=True)
                _persist_runner_streams(root, completed.stdout, completed.stderr)
                result = {
                    "runner_exit_code": completed.returncode,
                    "runner_started_unix_s": start, "runner_finished_unix_s": time.time(),
                    "runner_wall_ms": (time.perf_counter() - clock) * 1000,
                    "runner_setup_ms": None,
                }
        except Exception as exc:
            # Never put the exception body (possibly a provider response) in receipts.
            result = {
                "runner_exit_code": 1, "runner_error_class": type(exc).__name__,
                "runner_error_errno": getattr(exc, 'errno', None) if isinstance(getattr(exc, 'errno', None), int) else None,
                "coordinator_nofile_limits": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
                "runner_started_unix_s": start, "runner_finished_unix_s": time.time(),
                "runner_wall_ms": (time.perf_counter() - clock) * 1000,
                "runner_setup_ms": None,
            }
        streams = {}
        for stream in ("stdout", "stderr"):
            path = root / f"runner.{stream}.private.log"
            streams[f"{stream}_digest"] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        return {
            **Correlation(run_id=run_id, case_id=str(entry["qid"]), arm=str(entry["arm"]),
                          seed=int(entry["seed"]), repetition=int(entry["repetition"]),
                          cache_phase=str(entry["cache_phase"]), role="schedule_coordinator").receipt_fields(),
            "schema_version": "agentic_os.schedule_attempt_lifecycle.v1",
            "paired_unit": str(entry["paired_unit"]), "schedule_ordinal": ordinal,
            "runner_command_digest": canonical_digest(command),
            "runner_mode": args.runner_mode, "worker_slot": threading.get_ident(),
            "host_queue_wait_ms": (result["runner_started_unix_s"] - enqueued) * 1000,
            "dispatch_to_runner_ms": (result["runner_started_unix_s"] - dispatched) * 1000,
            "enqueued_unix_s": enqueued, "dispatched_unix_s": dispatched,
            "pending_after_dispatch": pending,
            **result, **streams,
            "terminal_status": "ok" if result["runner_exit_code"] == 0 else "runner_process_error",
        }

    first_finishes: dict[int, float] = {}
    duration = getattr(args, "measurement_duration_s", None)
    deadline: float | None = None
    duration_reached = False
    failures = 0
    next_ordinal = 0
    stopped = False
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            active = set()
            while active or (next_ordinal < len(entries) and not stopped):
                if deadline is not None and time.time() >= deadline:
                    duration_reached = True
                    stopped = True
                while len(active) < args.workers and next_ordinal < len(entries) and not stopped:
                    dispatched = time.time()
                    dispatches.append(dispatched)
                    active.add(pool.submit(execute, next_ordinal, dispatched, len(entries) - next_ordinal - 1))
                    next_ordinal += 1
                if not active:
                    break
                wait_s = max(0.001, deadline - time.time()) if deadline is not None and not stopped else None
                done, active = wait(active, timeout=wait_s, return_when=FIRST_COMPLETED)
                for future in done:
                    row = future.result()
                    rows.append(row)
                    append_jsonl(lifecycle_path, row)
                    slot = row["worker_slot"]
                    first_finishes.setdefault(slot, row["runner_finished_unix_s"])
                    if duration is not None and len(first_finishes) == args.workers and deadline is None:
                        deadline = max(first_finishes.values()) + args.steady_state_warmup_s + duration
                    if row["runner_exit_code"] != 0:
                        failures += 1
                        stopped = stopped or not args.continue_on_error
    finally:
        for runner in runners:
            runner.close()
    first_finishes: dict[int, float] = {}
    for row in rows:
        slot = row["worker_slot"]
        first_finishes[slot] = min(first_finishes.get(slot, float("inf")), row["runner_finished_unix_s"])
    # Each slot must complete a warmup case (including its first module import).
    start = max(first_finishes.values()) + args.steady_state_warmup_s if len(first_finishes) == args.workers else None
    # End when the last queued case is dispatched, excluding the drain tail.
    end = deadline if duration_reached and next_ordinal < len(entries) else (dispatches[-1] if next_ordinal == len(entries) else None)
    if duration is not None and (not duration_reached or next_ordinal == len(entries)):
        end = None  # Backlog drained before the registered measurement duration.
    valid = start is not None and end is not None and end > start and failures == 0
    write_manifest(args.run_root / "coordinator-window.json", {
        "schema_version": "agentic_os.backlog_window.v1",
        "execution_digest": execution_digest, "workers": args.workers,
        "runner_mode": args.runner_mode, "warmup_s": args.steady_state_warmup_s,
        "window_status": "available" if valid else "insufficient_backlog_or_failed_run",
        "started_unix_s": start if valid else None, "finished_unix_s": end if valid else None,
        "duration_s": end - start if valid else None,
        "completed_tasks": sum(start <= row["runner_finished_unix_s"] < end for row in rows) if valid else None,
        "task_completions_per_s": sum(start <= row["runner_finished_unix_s"] < end for row in rows) / (end - start) if valid else None,
        "scheduled_attempts": len(entries), "dispatched_attempts": next_ordinal,
        "completed_attempts": len(rows), "skipped_attempts": len(entries) - next_ordinal,
        "failed_attempts": failures,
        "requested_duration_s": duration, "duration_reached": duration_reached,
        "interpretation": "host FIFO backlog window; server saturation requires vLLM telemetry",
    })
    return 2 if failures else 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=tuple(_LANE_ARMS))
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--discovery-cache", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-root-suffix", default=None, help="Optional subdirectory appended to --run-root (concurrent shards).")
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
        choices=("baseline", "strengthened", "shared_prefix"),
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
        "--zero-row-hint",
        action="store_true",
        help="lab 021: grbench-single executor appends a content-free anchor/degree diagnosis to empty results.",
    )
    parser.add_argument(
        "--constrained",
        choices=("xgrammar", "xgrammar-ontology", "none"),
        default=None,
        help="lab 020 grbench-grammar lane: decode query turns under the read-only Cypher grammar (xgrammar) or run the same protocol unconstrained (none).",
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
        "--shard",
        default=None,
        help="Run only entries with ordinal %% K == I, written I/K (0-based); used to run K coordinators concurrently over disjoint shards.",
    )
    parser.add_argument("--row-cap", type=int, default=None, help="GRBench executor row cap (LIMIT); default = runner default 20")
    parser.add_argument("--byte-cap", type=int, default=None, help="GRBench executor result byte cap; default = runner default 12000")
    parser.add_argument(
        "--only-stratum",
        action="append",
        default=[],
        help="Gate-only filter on the frozen schedule's stratum field (repeatable); the source schedule stays digest-bound.",
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
    parser.add_argument("--dynamic-queue", action="store_true", help="Use the FIFO even with one fresh worker.")
    parser.add_argument("--workers", type=int, default=1,
                        help="Dynamic FIFO concurrency in one coordinator; do not combine with --shard.")
    parser.add_argument("--runner-mode", choices=("fresh", "persistent"), default="fresh")
    parser.add_argument('--worker-native-threads', type=int, help='Bound native libraries inside persistent worker processes only')
    parser.add_argument("--steady-state-warmup-s", type=float, default=0.0,
                        help="Exclude this many seconds after every worker completes its first case.")
    parser.add_argument("--measurement-duration-s", type=float,
                        help="Stop FIFO dispatch after this measured duration, then drain in-flight cases.")
    parser.add_argument("--constrained-ratio", type=float, default=1.0)
    parser.add_argument("--record-replay", action="store_true")
    parser.add_argument("--replay-mode", choices=("serving-only", "tool-stub", "real-db"), default="tool-stub")
    args = parser.parse_args()
    if args.worker_native_threads is not None and (args.worker_native_threads < 1 or args.runner_mode != 'persistent'):
        parser.error('worker-native-threads requires persistent mode and a positive count')
    if args.workers < 1 or not math.isfinite(args.steady_state_warmup_s) or args.steady_state_warmup_s < 0:
        parser.error("workers must be positive and warmup non-negative")
    dynamic = args.dynamic_queue or args.workers > 1 or args.runner_mode == "persistent"
    if args.measurement_duration_s is not None and (
        not dynamic or not math.isfinite(args.measurement_duration_s) or args.measurement_duration_s <= 0
    ):
        parser.error("--measurement-duration-s requires dynamic execution and a finite positive value")
    if not 0 <= args.constrained_ratio <= 1:
        parser.error("constrained ratio must be between zero and one")
    if args.record_replay and args.lane != "grbench-single":
        parser.error("replay capture supports grbench-single only")
    if dynamic and (args.shard or args.cache_control_script or args.inter_attempt_gap_s):
        parser.error("dynamic execution forbids static shards, shared cache control and idle gaps")
    if dynamic and (args.lane not in ("grbench-single", "grbench-graphcot", "grbench-router", "grbench-grammar", "grbench-replay")):
        parser.error("dynamic execution supports only GRBench single-agent lanes")
    if args.runner_mode == "persistent" and args.lane == "grbench-router":
        parser.error("router launches a child per case; use fresh mode until its dispatch is persistent")
    if dynamic and (args.gpu_sampling != "appliance" or args.metrics_url):
        parser.error("dynamic execution requires --gpu-sampling appliance and external pass telemetry")
    if args.run_root_suffix:
        args.run_root = args.run_root / str(args.run_root_suffix)

    schedule, entries = _load_schedule(args.schedule)
    if args.only_arm is not None:
        entries = [entry for entry in entries if str(entry["arm"]) == args.only_arm]
    if args.only_stratum:
        entries = [entry for entry in entries if str(entry.get("stratum")) in set(args.only_stratum)]
    if args.shard:
        index_text, _, count_text = str(args.shard).partition("/")
        shard_index, shard_count = int(index_text), int(count_text)
        if shard_count < 1 or not 0 <= shard_index < shard_count:
            parser.error("--shard must be I/K with 0 <= I < K")
        entries = [entry for ordinal, entry in enumerate(entries) if ordinal % shard_count == shard_index]
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
        Path(__file__).with_name("agentic_os_schedule_worker.py"),
        _REPO_ROOT / "src/seocho/eval/agentic_graph_agents.py",
        _REPO_ROOT / "src/seocho/eval/agentic_os_observability.py",
        _REPO_ROOT / "src/seocho/eval/agentic_os_replay.py",
        _REPO_ROOT / "src/seocho/eval/agentic_os_characterization.py",
        _REPO_ROOT / "src/seocho/eval/agentic_os_otel.py",
        _REPO_ROOT / "src/seocho/eval/grbench_runtime.py",
        _REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py",
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
        "zero_row_hint": bool(getattr(args, "zero_row_hint", False)),
        "constrained": getattr(args, "constrained", None),
        "only_arm": args.only_arm,
        "only_stratum": sorted(args.only_stratum),
        "shard": args.shard,
        "max_attempts": args.max_attempts,
        "inter_attempt_gap_s": args.inter_attempt_gap_s,
        "harness_sha256": harness_sha256,
        "workers": args.workers,
        "measurement_duration_s": args.measurement_duration_s,
        "constrained_ratio": args.constrained_ratio,
        "record_replay": args.record_replay,
        "replay_mode": args.replay_mode if args.lane == "grbench-replay" else None,
        "dispatch_mode": "dynamic_fifo" if dynamic else "serial",
        "runner_mode": args.runner_mode,
        "worker_native_threads": args.worker_native_threads,
        "steady_state_warmup_s": args.steady_state_warmup_s,
        "prompt_profile": args.prompt_profile,
        "grbench_workspace_id": args.grbench_workspace_id,
        "row_cap": args.row_cap,
        "byte_cap": args.byte_cap,
    }
    execution_digest = canonical_digest(execution_identity)
    manifest_path = args.run_root / "coordinator-manifest.json"
    lifecycle_path = args.run_root / "coordinator-receipts.jsonl"
    require_fresh_outputs((manifest_path, lifecycle_path, args.run_root / "coordinator-window.json"))
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

    if dynamic:
        return _run_dynamic(args, entries, execution_digest, lifecycle_path)

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
