#!/usr/bin/env python3
"""Run one co-located graph-agent workload with out-of-band local telemetry.

The process is intended to run *inside* the Vast appliance.  vLLM, DozerDB,
Oxigraph/agent code and samplers share the same host; sampler requests never
occur on an agent's request critical path.  This wrapper does not start graph
or model services: the appliance entrypoint owns their readiness and passes
their PIDs here for attribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any

from seocho.eval.agentic_os_observability import (
    HostProcessSampler,
    NvidiaSmiSampler,
    PROCESS_TREE_SCOPE,
    REQUIRED_VLLM_METRIC_NAMES,
    VllmMetricsClient,
    VllmMetricsSampler,
    validate_gpu_samples,
    validate_host_process_samples,
    validate_vllm_metrics_samples,
    memory_pressure_sample,
)


def _parse_role_pid(raw: str) -> tuple[str, int]:
    role, separator, pid_raw = raw.partition("=")
    if not separator or not role or not pid_raw.isdigit() or int(pid_raw) <= 0:
        raise argparse.ArgumentTypeError("--role-pid must be ROLE=POSITIVE_PID")
    return role, int(pid_raw)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _command_version(*command: str) -> str | None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else None


def _hardware_receipt() -> dict[str, Any]:
    mem_total = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    cgroup_cpu_max = None
    try:
        cgroup_cpu_max = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {
        "schema_version": "agentic_os.colocated_hardware.v2",
        "timestamp_unix_s": round(time.time(), 6),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": mem_total,
        "cgroup_cpu_max": cgroup_cpu_max,
        "cpu_affinity_count": len(os.sched_getaffinity(0)),
        "memory_observation": memory_pressure_sample(),
        "configured_budgets": {key: os.environ.get(key) for key in (
            "DOZERDB_HEAP_INITIAL_SIZE", "DOZERDB_HEAP_MAX_SIZE", "DOZERDB_PAGECACHE_SIZE",
            "VLLM_MAX_NUM_SEQS", "VLLM_TENSOR_PARALLEL_SIZE", "VLLM_REPLICAS",
            "SEOCHO_VLLM_CPU_COUNT", "SEOCHO_VLLM_CPU_LIST",
        )},
        "vllm_version": _command_version("vllm", "--version"),
        "java_version": _command_version("java", "-version"),
        "nvidia_smi_version": _command_version("nvidia-smi", "--version"),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _telemetry_acceptance(validation: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    host = validation.get("host_process") or {}
    if int(host.get("rows", 0)) < 1:
        blockers.append("host_process_samples_missing")
    if int(host.get("gap_count", 0)) != 0:
        blockers.append("host_process_sampling_gaps")
    required_roles = {"agent", "dozerdb", "vllm"}
    missing_roles = sorted(required_roles - set(host.get("roles") or []))
    if missing_roles:
        blockers.append(f"host_process_roles_missing:{','.join(missing_roles)}")
    role_scopes = host.get("role_scopes") or {}
    non_tree_roles = sorted(
        role for role in required_roles if role_scopes.get(role) != [PROCESS_TREE_SCOPE]
    )
    if non_tree_roles:
        blockers.append(f"host_process_tree_scope_missing:{','.join(non_tree_roles)}")
    member_counts = host.get("member_counts") or {}
    roles_without_members = sorted(
        role for role in required_roles if (member_counts.get(role) or {}).get("min") is None
    )
    if roles_without_members:
        blockers.append(f"host_process_members_missing:{','.join(roles_without_members)}")

    vllm = validation.get("vllm") or {}
    if float(vllm.get("availability_ratio", 0.0)) != 1.0:
        blockers.append("vllm_metrics_not_fully_available")
    if int(vllm.get("gap_count", 0)) != 0:
        blockers.append("vllm_metrics_sampling_gaps")
    names = set(vllm.get("metric_names_present") or [])
    missing_metrics = sorted(REQUIRED_VLLM_METRIC_NAMES - names)
    if missing_metrics:
        blockers.append(f"vllm_metric_families_missing:{','.join(missing_metrics)}")
    cache_config = vllm.get("cache_config_info") or {}
    if str(cache_config.get("enable_prefix_caching", "")).lower() != "true":
        blockers.append("vllm_prefix_caching_not_enabled")

    gpu = validation.get("gpu")
    if gpu is not None:
        if int(gpu.get("gpu_count", 0)) < 1 or int(gpu.get("row_count", 0)) < 1:
            blockers.append("gpu_samples_missing")
        if int(gpu.get("gap_count", 0)) != 0:
            blockers.append("gpu_sampling_gaps")
    return {
        "schema_version": "agentic_os.telemetry_acceptance.v1",
        "passed": not blockers,
        "required_vllm_metrics": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--vllm-metrics-url", default="http://127.0.0.1:8000/metrics")
    parser.add_argument("--role-pid", action="append", type=_parse_role_pid, default=[])
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--graph-system-observer", action="store_true",
                        default=os.environ.get("SEOCHO_GRAPH_SYSTEM_OBSERVER") == "1",
                        help="Opt-in 5-second root-thread/cgroup/NUMA/device observer; records its cost")
    parser.add_argument("--max-gap-s", type=float, default=2.5)
    parser.add_argument("--no-gpu", action="store_true", help="Testing-only: do not invoke nvidia-smi")
    parser.add_argument(
        "--strict-telemetry",
        action="store_true",
        help=(
            "Exit 3 when a sampler failed or telemetry acceptance did not pass, even if the "
            "workload succeeded. Default: the workload's own exit code decides; telemetry "
            "problems are recorded in the receipt (telemetry_degraded) for the acceptance "
            "validators to judge."
        ),
    )
    parser.add_argument("--workload", nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    if args.interval_s <= 0 or args.max_gap_s <= 0:
        parser.error("--interval-s and --max-gap-s must be positive")
    workload = list(args.workload)
    if workload[:1] == ["--"]:
        workload = workload[1:]
    if not workload:
        parser.error("--workload requires an executable and arguments")

    roles = dict(args.role_pid)
    if len(roles) != len(args.role_pid):
        parser.error("--role-pid roles must be unique")
    args.artifacts.mkdir(parents=True, exist_ok=True)
    _write_json(args.artifacts / "hardware.json", _hardware_receipt())
    if args.graph_system_observer:
        from probe_agentic_os_graph_capabilities import probe_environment

        # Pre-workload only. No SHOW/PROFILE query is injected into timed calls.
        try:
            capabilities = probe_environment()
        except Exception as exc:
            capabilities = {"status": "unavailable", "error_class": type(exc).__name__}
        capabilities["run_id"] = os.environ.get("SEOCHO_EXPERIMENT_RUN_ID")
        capabilities["workspace_id"] = os.environ.get("SEOCHO_WORKSPACE_ID")
        _write_json(args.artifacts / "graph-capabilities.json", capabilities)
    started_unix_s = time.time()
    started_monotonic_s = time.monotonic()
    stdout_path = args.artifacts / "workload.stdout.private.log"
    stderr_path = args.artifacts / "workload.stderr.private.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        child = subprocess.Popen(workload, stdout=stdout, stderr=stderr, start_new_session=True)
        if "agent" in roles and roles["agent"] != child.pid:
            child.terminate()
            parser.error("--role-pid agent must not be supplied; wrapper owns the agent PID")
        roles["agent"] = child.pid
        host_sampler = HostProcessSampler(
            args.artifacts / "host-process.csv",
            roles=roles,
            interval_s=args.interval_s,
            process_scope=PROCESS_TREE_SCOPE,
            graph_system_interval_s=5.0 if args.graph_system_observer else None,
        )
        vllm_sampler = VllmMetricsSampler(
            args.artifacts / "vllm-metrics.jsonl",
            client=VllmMetricsClient(args.vllm_metrics_url),
            interval_s=args.interval_s,
        )
        gpu_sampler = None if args.no_gpu else NvidiaSmiSampler(args.artifacts / "gpu.csv", interval_s=args.interval_s)
        sampler_errors: list[str] = []
        # Samplers are observers.  A sampler that cannot take its first sample
        # (vLLM metrics window empty right after an in-pass restart, nvidia-smi
        # unavailable) must not abort the wrapper and orphan the running
        # workload; it is recorded and the receipt shows the degradation.
        running: list[Any] = []
        for name, sampler in (("host", host_sampler), ("vllm", vllm_sampler), ("gpu", gpu_sampler)):
            if sampler is None:
                continue
            try:
                sampler.start()
                running.append(sampler)
            except RuntimeError as exc:
                sampler_errors.append(f"{name} sampler failed to start: {exc}")
        try:
            workload_exit_code = child.wait()
        finally:
            for sampler in reversed(running):
                try:
                    sampler.stop()
                except RuntimeError as exc:
                    sampler_errors.append(str(exc))
    ended_monotonic_s = time.monotonic()
    ended_unix_s = time.time()
    def _validate(name: str, path: Path, fn: Any) -> dict[str, Any]:
        # A sampler that never started leaves no file; _telemetry_acceptance
        # fails closed on the empty result instead of the wrapper crashing here.
        if not path.exists():
            return {"error": f"{name} samples missing: {path.name}"}
        return fn(path, max_gap_s=args.max_gap_s)

    validation: dict[str, Any] = {
        "host_process": _validate("host_process", args.artifacts / "host-process.csv", validate_host_process_samples),
        "vllm": _validate("vllm", args.artifacts / "vllm-metrics.jsonl", validate_vllm_metrics_samples),
    }
    if not args.no_gpu:
        validation["gpu"] = _validate("gpu", args.artifacts / "gpu.csv", validate_gpu_samples)
    telemetry_acceptance = _telemetry_acceptance(validation)
    files = sorted(path for path in args.artifacts.iterdir() if path.is_file())
    receipt = {
        "schema_version": "agentic_os.colocated_workload.v2",
        "topology": "single_host_colocated",
        "observer_mode": "same_host_background_sampler",
        "critical_path_metrics_scrapes": 0,
        "started_unix_s": round(started_unix_s, 6),
        "ended_unix_s": round(ended_unix_s, 6),
        "wall_s": round(ended_monotonic_s - started_monotonic_s, 6),
        "workload_exit_code": workload_exit_code,
        "role_pids": roles,
        "role_process_scopes": {role: PROCESS_TREE_SCOPE for role in sorted(roles)},
        "workload_argv_sha256": hashlib.sha256("\0".join(workload).encode()).hexdigest(),
        "sampler_errors": sampler_errors,
        "validation": validation,
        "telemetry_acceptance": telemetry_acceptance,
        "artifacts": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
    }
    # Exit policy (bead seocho-twoh): lab-s3/s4/s6 finished every pass with
    # workload_exit_code 0, yet the wrapper returned 3 because an in-pass vLLM
    # restart left the metrics window empty or nvidia-smi sampling gapped, and
    # the lifecycle receipt then read "failed" for a successful session.  The
    # workload result is authoritative; telemetry quality is a receipt fact that
    # validate_agentic_os_e2e_acceptance.py (which already checks
    # telemetry_acceptance.passed) and --strict-telemetry can still fail on.
    degradation: list[str] = list(sampler_errors)
    if not telemetry_acceptance["passed"]:
        degradation.extend(f"telemetry_acceptance:{b}" for b in telemetry_acceptance.get("blockers", []) or ["not_passed"])
    receipt["telemetry_degraded"] = bool(degradation)
    receipt["telemetry_degradation"] = degradation
    receipt["exit_policy"] = "strict_telemetry" if args.strict_telemetry else "workload_exit_code"
    if workload_exit_code != 0:
        receipt["exit_code"] = workload_exit_code
    elif args.strict_telemetry and degradation:
        receipt["exit_code"] = 3
    else:
        receipt["exit_code"] = 0
    _write_json(args.artifacts / "colocated-workload-receipt.json", receipt)
    return int(receipt["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
