"""Content-free graph-system observations; missing counters are never zero.

Linux observations describe explicitly supplied root PIDs and the sampler-visible
cgroup/host. They do not attribute shared hardware counters to individual queries.
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Mapping


# view, measurement, unit, required source. This is a coverage contract, not a
# claim that every installation exports every counter.
GRAPH_OBSERVATIONS = (
    ("Runtime", "db_version_edition", "identity", "read-only capability probe"),
    ("Runtime", "store_format", "identity", "verified database store metadata; never infer from product name"),
    ("Runtime", "actual_query_runtime", "category", "execution-plan metadata; requested runtime is separate"),
    ("Runtime", "process_cpu_rss_threads_io", "s / bytes / threads", "existing host-process.csv"),
    ("Runtime", "thread_scheduler", "run_s / wait_s", "graph-system.jsonl; live root-process thread identities"),
    ("Runtime", "cpu_numa_affinity", "CPU / node IDs", "graph-system.jsonl; root thread allowed masks"),
    ("Runtime", "cpu_quota_throttling", "usec / count", "graph-system.jsonl; sampler-visible cgroup cpu.stat"),
    ("Runtime", "numa_allocation", "bytes per node", "graph-system.jsonl; cgroup memory.numa_stat; not remote accesses"),
    ("Runtime", "io_pressure_devices", "usec / sectors / ms", "graph-system.jsonl; PSI and per-device diskstats"),
    ("Runtime", "db_page_cache", "hits / faults / bytes", "version-verified DB metrics exporter or JMX"),
    ("Runtime", "jvm_heap_gc", "bytes / seconds", "version-verified JVM exporter or JFR"),
    ("Runtime", "bolt_workers_transactions", "threads / transactions", "DB worker and transaction metrics; not client connection count"),
    ("Runtime", "ipc_llc_tlb", "ratio / misses", "separate perf diagnostic with enabled/running time and event definitions"),
    ("Runtime", "memory_bandwidth_remote_access", "bytes/s / events", "supported PMU/uncore diagnostic; scope and multiplexing recorded"),
    ("Runtime", "kv_offload_transfers", "bytes / seconds", "actual offload backend counters; not process IO"),
    ("Tool", "query_latency_rows_bytes", "ms / rows / bytes", "existing tool receipts; bytes are not tokens"),
    ("Tool", "procedure_inventory", "availability", "read-only SHOW PROCEDURES; availability is not invocation"),
    ("Tool", "procedure_execution", "calls / ms / bytes", "exact tool/DB spans and bounded procedure labels"),
    ("Tool", "profile_operators_db_hits", "rows / logical hits", "existing out-of-band PROFILE replay; not timed execution"),
    ("Tool", "traversal_work", "visited nodes / edges / frontier", "instrumented procedure/cursor; result rows are not visited nodes"),
    ("Tool", "structure_locality", "degree / type / direction / page reuse", "dataset strata plus cursor/page diagnostic; degree alone is insufficient"),
    ("Tool", "projection_cost", "build_s / bytes / refresh_s", "GDS/OpenGDS projection receipts separate from algorithm time"),
    ("Tool", "pool_server_receive_serialize", "ms", "driver/server spans; session creation is not pool wait"),
    ("Agent", "calls_context_repairs", "calls / tokens", "existing task and turn receipts"),
    ("Agent", "tool_to_llm_gap", "ms", "exact tool end and next request submission in the same task"),
    ("Agent", "answer_equivalence", "score / verdict", "separate evaluator; completion is not accuracy"),
)


def _text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def _pairs(path: Path, allowed: set[str]) -> dict[str, int]:
    result = {}
    for line in (_text(path) or "").splitlines():
        fields = line.replace(":", "").split()
        if len(fields) == 2 and fields[0] in allowed and fields[1].isdigit():
            result[fields[0]] = int(fields[1])
    return result


def _mask(raw: str) -> str | None:
    return raw if re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", raw) else None


def _start_ticks(path: Path) -> int | None:
    raw = _text(path)
    try:
        return int(raw[raw.rfind(")") + 2:].split()[19]) if raw else None
    except (ValueError, IndexError):
        return None


def process_threads(pid: int, *, proc: Path = Path("/proc"), limit: int = 4096) -> dict[str, Any]:
    """Bounded numeric thread scan. Churn invalidates subsequent counter deltas."""
    root = proc / str(pid)
    identity = _start_ticks(root / "stat")
    masks: dict[str, str | None] = {}
    for line in (_text(root / "status") or "").splitlines():
        key, _, value = line.partition(":")
        if key in {"Cpus_allowed_list", "Mems_allowed_list"}:
            masks[key] = _mask(value.strip())
    try:
        tids = sorted(int(p.name) for p in (root / "task").iterdir() if p.name.isdigit())
    except OSError:
        tids = []
    members, run_ns, wait_ns, slices = {}, 0, 0, 0
    for tid in tids[:limit]:
        thread = root / "task" / str(tid)
        begun = _start_ticks(thread / "stat")
        fields = (_text(thread / "schedstat") or "").split()
        if begun is None or len(fields) != 3 or not all(f.isdigit() for f in fields):
            continue
        if _start_ticks(thread / "stat") != begun:
            continue
        members[str(tid)] = begun
        run_ns += int(fields[0])
        wait_ns += int(fields[1])
        slices += int(fields[2])
    complete = bool(tids) and len(members) == len(tids) and identity is not None
    complete = complete and _start_ticks(root / "stat") == identity
    return {"pid": pid, "start_ticks": identity, "scope": "root_process_live_threads_only",
            "affinity_scope": "root_thread_allowed_masks_not_all_threads_or_actual_placement",
            "allowed_masks": masks, "threads_seen": len(tids) if identity is not None else None,
            "threads_sampled": len(members), "complete": complete, "thread_identities": members,
            "run_s": run_ns / 1e9 if complete else None,
            "wait_s": wait_ns / 1e9 if complete else None,
            "timeslices": slices if complete else None}


def graph_system_sample(roles: Mapping[str, int], *, proc: Path = Path("/proc"),
                        cgroup: Path = Path("/sys/fs/cgroup")) -> dict[str, Any]:
    started = time.monotonic()
    stamp = time.time()
    numa = {}
    for line in (_text(cgroup / "memory.numa_stat") or "").splitlines():
        fields = line.split()
        if fields and fields[0] in {"anon", "file", "shmem"}:
            numa[fields[0]] = {k: int(v) for f in fields[1:] for k, sep, v in [f.partition("=")]
                               if sep and re.fullmatch(r"N\d+", k) and v.isdigit()}
    devices = {}
    for line in (_text(proc / "diskstats") or "").splitlines():
        fields = line.split()
        if len(fields) >= 14 and all(f.isdigit() for f in fields[:2] + fields[3:14]):
            # Major:minor, no device paths/names. Do not sum stacked devices.
            devices[f"{fields[0]}:{fields[1]}"] = dict(zip(
                ("reads", "read_merges", "read_sectors", "read_ms", "writes", "write_merges",
                 "write_sectors", "write_ms", "inflight", "io_ms", "weighted_io_ms"),
                map(int, fields[3:14]), strict=True))
    pressure = {}
    for resource in ("cpu", "memory", "io"):
        values = {}
        for line in (_text(cgroup / f"{resource}.pressure") or "").splitlines():
            fields = line.split()
            if fields and fields[0] in {"some", "full"}:
                for field in fields[1:]:
                    key, _, value = field.partition("=")
                    if key == "total" and value.isdigit():
                        values[fields[0] + "_total_us"] = int(value)
        pressure[resource] = values or None
    cpu_max = (_text(cgroup / "cpu.max") or "").split()
    return {"schema_version": "agentic_os.graph_system_sample.v1", "timestamp_unix_s": stamp,
            "timestamp_monotonic_s": started, "scope": "explicit_root_PIDs_and_sampler_visible_host_cgroup",
            "processes": {role: process_threads(pid, proc=proc) for role, pid in sorted(roles.items())},
            "cpu_max": cpu_max if len(cpu_max) == 2 and (cpu_max[0] == "max" or cpu_max[0].isdigit()) and cpu_max[1].isdigit() else None,
            "cpu_stat": _pairs(cgroup / "cpu.stat", {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}) or None,
            "numa_allocation_bytes": numa or None, "pressure": pressure,
            "devices": devices or None,
            "schedstats_enabled": _text(proc / "sys/kernel/sched_schedstats") == "1\n",
            "collection_ms": (time.monotonic() - started) * 1000}


def coverage() -> list[dict[str, Any]]:
    return [{"view": view, "measurement": name, "unit": unit, "source": source,
             "status": "not_collected", "value": None} for view, name, unit, source in GRAPH_OBSERVATIONS]


def plan_runtime(plan: Any) -> str | None:
    """Export only a bounded engine runtime label, never arbitrary plan arguments."""
    args = (plan.get("args", plan.get("arguments", {})) if isinstance(plan, Mapping)
            else getattr(plan, "arguments", {})) or {}
    runtime = str(args.get("runtime", "")).upper()
    return runtime if runtime in {"SLOTTED", "PIPELINED", "PARALLEL", "INTERPRETED"} else None


def summarize_graph_system(samples: list[dict[str, Any]], start: float, end: float,
                           *, max_gap_s: float = 12.5) -> dict[str, Any]:
    """Summarize interval evidence without presenting partial counters as full rates."""
    selected = [s for s in samples if start <= s.get("timestamp_unix_s", -1) <= end]
    stamps = [s["timestamp_unix_s"] for s in selected]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    full = bool(len(stamps) >= 2 and start <= end and stamps[0] - start <= max_gap_s
                and end - stamps[-1] <= max_gap_s and all(0 < g <= max_gap_s for g in gaps))
    result: dict[str, Any] = {"schema_version": "agentic_os.graph_system_summary.v1",
        "samples": len(selected), "full_window_coverage": full,
        "sample_window_s": [stamps[0], stamps[-1]] if stamps else None,
        "processes": {}, "coverage": coverage(),
        "limitations": ["Root PID thread counters exclude child processes; consult existing process-tree sampler.",
                        "Scheduler wait is runnable CPU wait, not IO wait, Bolt pool wait or query time.",
                        "NUMA allocation is not remote memory traffic; DB page faults are not CPU page faults.",
                        "Diskstats are per device, host-wide and may include stacked devices; never sum blindly.",
                        "Shared hardware samples correlate with a query timeline but do not attribute its cost."]}
    if not selected:
        return result
    durations = [s.get("collection_ms") for s in selected]
    result["collection_ms_max"] = max((v for v in durations if isinstance(v, (int, float)) and math.isfinite(v)), default=None)
    roles = sorted({role for s in selected for role in s.get("processes", {})})
    for role in roles:
        observations = [s.get("processes", {}).get(role, {}) for s in selected]
        identity = observations[0].get("thread_identities")
        comparable = full and bool(identity) and all(
            p.get("complete") and p.get("pid") == observations[0].get("pid")
            and p.get("start_ticks") == observations[0].get("start_ticks")
            and p.get("thread_identities") == identity for p in observations)
        values: dict[str, Any] = {"stable_thread_set": comparable,
            "threads_max": max((p["threads_seen"] for p in observations if isinstance(p.get("threads_seen"), int)), default=None),
            "allowed_masks": observations[-1].get("allowed_masks"), "run_s_delta": None, "wait_s_delta": None}
        for metric in ("run_s", "wait_s"):
            counters = [p.get(metric) for p in observations]
            known = comparable and all(isinstance(v, (int, float)) and math.isfinite(v) for v in counters)
            if metric == "wait_s":
                known = known and all(s.get("schedstats_enabled") is True for s in selected)
            if known and all(b >= a for a, b in zip(counters, counters[1:])):
                values[metric + "_delta"] = counters[-1] - counters[0]
        result["processes"][role] = values
    # Keep native units and scopes. These raw numeric series feed the linked UI;
    # counter deltas/IOPS need a separate validated device/cgroup identity contract.
    result["timeline"] = [{**s, "processes": {role: {k: v for k, v in p.items() if k != "thread_identities"}
                           for role, p in s.get("processes", {}).items()}} for s in selected]
    present = {
        "thread_scheduler": any(p["run_s_delta"] is not None for p in result["processes"].values()),
        "cpu_numa_affinity": any(p["allowed_masks"] for p in result["processes"].values()),
        "cpu_quota_throttling": any(s.get("cpu_stat") for s in selected),
        "numa_allocation": any(s.get("numa_allocation_bytes") for s in selected),
        "io_pressure_devices": any(s.get("devices") or any(s.get("pressure", {}).values()) for s in selected),
    }
    for row in result["coverage"]:
        if present.get(row["measurement"]):
            row["status"] = "sampled" if full else "partial"
            row["value"] = "See process summary and timestamped raw series; individual subfields may be unknown"
    return result


def attach_graph_evidence(summary: dict[str, Any], *, capabilities: dict[str, Any] | None = None,
                          tools: list[dict[str, Any]] | None = None,
                          profiles: list[dict[str, Any]] | None = None,
                          process_samples_present: bool = False,
                          agent_samples_present: bool = False) -> dict[str, Any]:
    """Attach existing evidence without manufacturing internal traversal counts."""
    summary["capabilities"] = capabilities
    tools, profiles = tools or [], profiles or []
    successful_profiles = [p for p in profiles if not p.get("error_class") and p.get("plan")]
    summary["profile_replays"] = {"count": len(profiles), "successful": len(successful_profiles),
                                  "scope": "separate_diagnostic_not_throughput_window"}
    summary["tool_observations"] = {"calls": len(tools),
        "pool_wait_measured_calls": sum(t.get("connection_pool_wait_ms") is not None for t in tools),
        "result_tokens_measured_calls": sum(t.get("result_tokens") is not None for t in tools)}
    present = {"process_cpu_rss_threads_io": process_samples_present,
               "calls_context_repairs": agent_samples_present,
               "query_latency_rows_bytes": bool(tools),
               "profile_operators_db_hits": bool(successful_profiles)}
    summary["profile_replays"]["observed_runtimes"] = sorted({p["actual_runtime"] for p in successful_profiles
        if p.get("actual_runtime") in {"SLOTTED", "PIPELINED", "PARALLEL", "INTERPRETED"}})
    present["actual_query_runtime"] = bool(summary["profile_replays"]["observed_runtimes"])
    if capabilities:
        present["db_version_edition"] = bool((capabilities.get("components") or {}).get("rows"))
        present["procedure_inventory"] = (capabilities.get("procedures") or {}).get("status") == "observed"
    for row in summary["coverage"]:
        if present.get(row["measurement"]):
            row["status"] = "available_separate_evidence"
            row["value"] = "See source receipt; availability does not imply full window or all subfields"
    return summary
