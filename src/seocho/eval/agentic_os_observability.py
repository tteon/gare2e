"""Neutral, content-free observability for AgenticOS graph experiments.

This module belongs to the *measurement harness*, not to a SEOCHO treatment.
It is safe to use for both direct-driver (SEOCHO-off) and SEOCHO-on arms.
In particular, vLLM's Prometheus endpoint is process-wide: a window delta is
only attributable to one request when the scheduler serialized that window.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.error import URLError
from urllib.request import urlopen


HOST_PROCESS_SCHEMA_VERSION = "agentic_os.host_process_sample.v2"
HOST_PROCESS_HEADERS = (
    "schema_version",
    "timestamp_unix_s",
    "timestamp_monotonic_s",
    "role",
    "pid",
    "scope",
    "member_count",
    "process_cpu_user_s",
    "process_cpu_system_s",
    "process_rss_bytes",
    "process_threads",
    "process_read_bytes",
    "process_write_bytes",
    "host_cpu_count",
    "host_load1",
    "host_mem_total_bytes",
    "host_mem_available_bytes",
)

ProcessScope = Literal["root_process", "process_tree"]
ROOT_PROCESS_SCOPE: ProcessScope = "root_process"
PROCESS_TREE_SCOPE: ProcessScope = "process_tree"
_PROCESS_SCOPES = frozenset({ROOT_PROCESS_SCOPE, PROCESS_TREE_SCOPE})
_PROCESS_METRIC_NAMES = (
    "process_cpu_user_s",
    "process_cpu_system_s",
    "process_rss_bytes",
    "process_threads",
    "process_read_bytes",
    "process_write_bytes",
)
_CUMULATIVE_PROCESS_METRICS = frozenset(
    {
        "process_cpu_user_s",
        "process_cpu_system_s",
        "process_read_bytes",
        "process_write_bytes",
    }
)

GPU_SAMPLE_SCHEMA_VERSION = "agentic_os.gpu_sample.v1"
GPU_SAMPLE_HEADERS = (
    "schema_version",
    "timestamp_unix_s",
    "timestamp_monotonic_s",
    "gpu_index",
    "gpu_uuid",
    "gpu_utilization_pct",
    "memory_used_bytes",
    "memory_total_bytes",
    "power_w",
    "temperature_c",
)

REQUIRED_VLLM_METRIC_NAMES = frozenset(
    {
        "vllm:e2e_request_latency_seconds_count",
        "vllm:generation_tokens",
        "vllm:inter_token_latency_seconds_count",
        "vllm:kv_block_idle_before_evict_seconds_count",
        "vllm:kv_block_lifetime_seconds_count",
        "vllm:kv_block_reuse_gap_seconds_count",
        "vllm:kv_cache_usage_perc",
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:prefix_cache_hits",
        "vllm:prefix_cache_queries",
        "vllm:prompt_tokens",
        "vllm:request_decode_time_seconds_count",
        "vllm:request_generation_tokens_count",
        "vllm:request_prefill_time_seconds_count",
        "vllm:request_prompt_tokens_count",
        "vllm:request_queue_time_seconds_count",
        "vllm:request_success",
        "vllm:time_to_first_token_seconds_count",
    }
)


def _read_meminfo() -> tuple[int | None, int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            fields = raw.split()
            if fields and fields[0].isdigit():
                values[key] = int(fields[0]) * 1024
    except (OSError, ValueError):
        return None, None
    return values.get("MemTotal"), values.get("MemAvailable")


@dataclass(frozen=True)
class _ProcessSnapshot:
    pid: int
    ppid: int
    start_ticks: int
    metrics: dict[str, int | float | None]


@dataclass(frozen=True)
class _ProcessScopeSnapshot:
    metrics: dict[str, int | float | None]
    member_count: int | None
    members: dict[int, int]
    valid: bool


def _unknown_process_metrics() -> dict[str, int | float | None]:
    return {name: None for name in _PROCESS_METRIC_NAMES}


def _read_process_snapshot(pid: int, *, include_io: bool = True) -> _ProcessSnapshot | None:
    """Read identity and counters without inspecting argv or environment."""
    try:
        # The final ')' makes names containing spaces harmless: remaining fields
        # begin at proc(5) field 3. PPID is offset 1 and starttime is offset 19.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat[stat.rfind(")") + 2 :].split()
        tick = float(os.sysconf("SC_CLK_TCK"))
        rss_pages = int(fields[21])
        metrics: dict[str, int | float | None] = {
            "process_cpu_user_s": round(int(fields[11]) / tick, 6),
            "process_cpu_system_s": round(int(fields[12]) / tick, 6),
            "process_rss_bytes": rss_pages * int(os.sysconf("SC_PAGE_SIZE")),
            "process_threads": int(fields[17]),
            "process_read_bytes": None,
            "process_write_bytes": None,
        }
        # Container processes may expose stat to an unprivileged harness while
        # denying /proc/<pid>/io. Preserve the available CPU,
        # RSS, and thread evidence instead of erasing the entire sample.
        io_lines: list[str] = []
        if include_io:
            try:
                io_lines = Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines()
            except OSError:
                pass
        for line in io_lines:
            key, raw = line.split(":", 1)
            if key == "read_bytes":
                metrics["process_read_bytes"] = int(raw.strip())
            elif key == "write_bytes":
                metrics["process_write_bytes"] = int(raw.strip())
        return _ProcessSnapshot(
            pid=pid,
            ppid=int(fields[1]),
            start_ticks=int(fields[19]),
            metrics=metrics,
        )
    except (IndexError, OSError, ValueError):
        return None


def _read_process(pid: int) -> dict[str, int | float | None]:
    """Read cumulative Linux process counters without command lines/content."""
    snapshot = _read_process_snapshot(pid)
    return dict(snapshot.metrics) if snapshot is not None else _unknown_process_metrics()


def _read_process_table() -> dict[int, _ProcessSnapshot]:
    """Build a privacy-safe process table from numeric /proc stat entries."""
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return {}
    table: dict[int, _ProcessSnapshot] = {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        # Descendant discovery needs only stat's PPID and identity fields. Do
        # not inspect unrelated processes' I/O counters while building it.
        snapshot = _read_process_snapshot(int(entry.name), include_io=False)
        if snapshot is not None:
            table[snapshot.pid] = snapshot
    return table


def _enrich_process_io(snapshot: _ProcessSnapshot) -> _ProcessSnapshot:
    current = _read_process_snapshot(snapshot.pid)
    if current is None or current.start_ticks != snapshot.start_ticks:
        return snapshot
    metrics = dict(snapshot.metrics)
    metrics["process_read_bytes"] = current.metrics.get("process_read_bytes")
    metrics["process_write_bytes"] = current.metrics.get("process_write_bytes")
    return _ProcessSnapshot(
        pid=snapshot.pid,
        ppid=snapshot.ppid,
        start_ticks=snapshot.start_ticks,
        metrics=metrics,
    )


def _aggregate_processes(snapshots: list[_ProcessSnapshot]) -> dict[str, int | float | None]:
    aggregated: dict[str, int | float | None] = {}
    for name in _PROCESS_METRIC_NAMES:
        values = [snapshot.metrics.get(name) for snapshot in snapshots]
        if not values or any(not isinstance(value, (int, float)) for value in values):
            aggregated[name] = None
            continue
        total = sum(values)
        aggregated[name] = round(float(total), 6) if any(isinstance(value, float) for value in values) else int(total)
    return aggregated


def _read_process_scope(
    root_pid: int,
    *,
    process_scope: ProcessScope,
    expected_root_start_ticks: int | None,
    process_table: Mapping[int, _ProcessSnapshot] | None = None,
) -> _ProcessScopeSnapshot:
    if process_scope == ROOT_PROCESS_SCOPE:
        root = _read_process_snapshot(root_pid)
        table = {root_pid: root} if root is not None else {}
    else:
        table = dict(process_table) if process_table is not None else _read_process_table()
        root = table.get(root_pid)
    if (
        root is None
        or expected_root_start_ticks is None
        or root.start_ticks != expected_root_start_ticks
    ):
        return _ProcessScopeSnapshot(
            metrics=_unknown_process_metrics(),
            member_count=None,
            members={},
            valid=False,
        )

    member_pids = {root_pid}
    if process_scope == PROCESS_TREE_SCOPE:
        children: dict[int, list[int]] = {}
        for snapshot in table.values():
            children.setdefault(snapshot.ppid, []).append(snapshot.pid)
        pending = [root_pid]
        while pending:
            parent_pid = pending.pop()
            for child_pid in children.get(parent_pid, ()):
                if child_pid not in member_pids:
                    member_pids.add(child_pid)
                    pending.append(child_pid)
    members = [_enrich_process_io(table[pid]) for pid in sorted(member_pids) if table.get(pid) is not None]
    return _ProcessScopeSnapshot(
        metrics=_aggregate_processes(members),
        member_count=len(members),
        members={snapshot.pid: snapshot.start_ticks for snapshot in members},
        valid=True,
    )


def _cumulative_members_comparable(
    before: _ProcessScopeSnapshot,
    after: _ProcessScopeSnapshot,
) -> bool:
    if not before.valid or not after.valid:
        return False
    # A newly born descendant contributes counters from zero and is comparable.
    # A missing or reused prior member loses cumulative work, so the delta is unknown.
    return all(after.members.get(pid) == start_ticks for pid, start_ticks in before.members.items())


class HostProcessSampler:
    """Append content-free cumulative host/process samples to a validated CSV.

    ``roles`` is an explicit public role -> PID map (for example ``agent`` or
    ``dozerdb``).  The sampler intentionally never reads argv, environment, or
    open files; those often contain prompts, credentials, or query contents.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        roles: Mapping[str, int],
        interval_s: float = 1.0,
        process_scope: ProcessScope = ROOT_PROCESS_SCOPE,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        if not roles or any(not role or pid <= 0 for role, pid in roles.items()):
            raise ValueError("roles must map non-empty names to positive PIDs")
        if process_scope not in _PROCESS_SCOPES:
            raise ValueError(f"unsupported process_scope: {process_scope!r}")
        self.path = Path(path)
        self.roles = dict(roles)
        self.interval_s = float(interval_s)
        self.process_scope = process_scope
        self._root_start_ticks = {
            role: snapshot.start_ticks
            if (snapshot := _read_process_snapshot(pid, include_io=False)) is not None
            else None
            for role, pid in self.roles.items()
        }
        self._previous_scopes: dict[str, _ProcessScopeSnapshot] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prepare_file()

    def _prepare_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle), [])
            if tuple(header) != HOST_PROCESS_HEADERS:
                raise ValueError(f"host sample CSV header mismatch: {self.path}")
            return
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(HOST_PROCESS_HEADERS)

    def sample_once(self) -> list[dict[str, Any]]:
        """Collect one timestamp-aligned row per configured role."""
        unix_s, monotonic_s = time.time(), time.monotonic()
        mem_total, mem_available = _read_meminfo()
        try:
            load1 = round(os.getloadavg()[0], 6)
        except OSError:
            load1 = None
        base: dict[str, Any] = {
            "schema_version": HOST_PROCESS_SCHEMA_VERSION,
            "timestamp_unix_s": round(unix_s, 6),
            "timestamp_monotonic_s": round(monotonic_s, 6),
            "host_cpu_count": os.cpu_count(),
            "host_load1": load1,
            "host_mem_total_bytes": mem_total,
            "host_mem_available_bytes": mem_available,
        }
        rows: list[dict[str, Any]] = []
        process_table = _read_process_table() if self.process_scope == PROCESS_TREE_SCOPE else None
        for role, pid in sorted(self.roles.items()):
            current = _read_process_scope(
                pid,
                process_scope=self.process_scope,
                expected_root_start_ticks=self._root_start_ticks[role],
                process_table=process_table,
            )
            metrics = dict(current.metrics)
            previous = self._previous_scopes.get(role)
            if previous is not None and not _cumulative_members_comparable(previous, current):
                for name in _CUMULATIVE_PROCESS_METRICS:
                    metrics[name] = None
            rows.append(
                {
                    **base,
                    "role": role,
                    "pid": pid,
                    "scope": self.process_scope,
                    "member_count": current.member_count,
                    **metrics,
                }
            )
            self._previous_scopes[role] = current
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HOST_PROCESS_HEADERS, extrasaction="raise")
            writer.writerows(rows)
        return rows

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("host sampler already running")
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                self.sample_once()
                self._stop.wait(self.interval_s)

        self._thread = threading.Thread(target=_loop, name="agentic-os-host-sampler", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(join_timeout_s)


@dataclass
class ProcessMetricsWindow:
    """Exact cumulative process-counter deltas around one serialized case.

    Continuous samples remain the source for peaks and scheduling shape. This
    window is the source for per-case CPU and I/O because Linux exposes those
    counters cumulatively, avoiding interpolation between one-second samples.
    """

    roles: Mapping[str, int]
    process_scope: ProcessScope = ROOT_PROCESS_SCOPE
    before: dict[str, _ProcessScopeSnapshot] | None = None
    after: dict[str, _ProcessScopeSnapshot] | None = None
    started_unix_s: float | None = None
    started_monotonic_s: float | None = None
    ended_unix_s: float | None = None
    ended_monotonic_s: float | None = None
    _root_start_ticks: dict[str, int | None] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.roles or any(not role or pid <= 0 for role, pid in self.roles.items()):
            raise ValueError("roles must map non-empty names to positive PIDs")
        if self.process_scope not in _PROCESS_SCOPES:
            raise ValueError(f"unsupported process_scope: {self.process_scope!r}")
        self.roles = dict(self.roles)
        self._root_start_ticks = {
            role: snapshot.start_ticks
            if (snapshot := _read_process_snapshot(pid, include_io=False)) is not None
            else None
            for role, pid in self.roles.items()
        }

    def _snapshot_roles(self) -> dict[str, _ProcessScopeSnapshot]:
        process_table = _read_process_table() if self.process_scope == PROCESS_TREE_SCOPE else None
        return {
            role: _read_process_scope(
                pid,
                process_scope=self.process_scope,
                expected_root_start_ticks=self._root_start_ticks[role],
                process_table=process_table,
            )
            for role, pid in self.roles.items()
        }

    def __enter__(self) -> "ProcessMetricsWindow":
        self.started_unix_s = time.time()
        self.started_monotonic_s = time.monotonic()
        self.before = self._snapshot_roles()
        return self

    def __exit__(self, *_: object) -> None:
        self.after = self._snapshot_roles()
        self.ended_monotonic_s = time.monotonic()
        self.ended_unix_s = time.time()

    @staticmethod
    def _delta(
        before: Mapping[str, int | float | None],
        after: Mapping[str, int | float | None],
        name: str,
    ) -> int | float | None:
        start, end = before.get(name), after.get(name)
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        delta = end - start
        if delta < 0:
            return None
        return round(delta, 6) if isinstance(start, float) or isinstance(end, float) else int(delta)

    def receipt(self) -> dict[str, Any]:
        if (
            self.before is None
            or self.after is None
            or self.started_unix_s is None
            or self.started_monotonic_s is None
            or self.ended_unix_s is None
            or self.ended_monotonic_s is None
        ):
            raise RuntimeError("process metrics window has not closed")
        processes: dict[str, dict[str, Any]] = {}
        for role, pid in sorted(self.roles.items()):
            before, after = self.before[role], self.after[role]
            counters_comparable = _cumulative_members_comparable(before, after)
            user_s = self._delta(before.metrics, after.metrics, "process_cpu_user_s") if counters_comparable else None
            system_s = (
                self._delta(before.metrics, after.metrics, "process_cpu_system_s") if counters_comparable else None
            )
            cpu_total_s = (
                round(float(user_s) + float(system_s), 6)
                if isinstance(user_s, (int, float)) and isinstance(system_s, (int, float))
                else None
            )
            processes[role] = {
                "pid": pid,
                "scope": self.process_scope,
                "member_count": {"start": before.member_count, "end": after.member_count},
                "cumulative_counters_comparable": counters_comparable,
                "cpu_user_s": user_s,
                "cpu_system_s": system_s,
                "cpu_total_s": cpu_total_s,
                "read_bytes": (
                    self._delta(before.metrics, after.metrics, "process_read_bytes") if counters_comparable else None
                ),
                "write_bytes": (
                    self._delta(before.metrics, after.metrics, "process_write_bytes") if counters_comparable else None
                ),
                "rss_start_bytes": before.metrics.get("process_rss_bytes"),
                "rss_end_bytes": after.metrics.get("process_rss_bytes"),
                "threads_start": before.metrics.get("process_threads"),
                "threads_end": after.metrics.get("process_threads"),
            }
        return {
            "schema_version": "agentic_os.process_metrics_window.v2",
            "scope": "serialized_case_window",
            "process_scope": self.process_scope,
            "clock_domain": "local_host_monotonic",
            "started_unix_s": round(self.started_unix_s, 6),
            "ended_unix_s": round(self.ended_unix_s, 6),
            "started_monotonic_s": round(self.started_monotonic_s, 6),
            "ended_monotonic_s": round(self.ended_monotonic_s, 6),
            "window_ms": round((self.ended_monotonic_s - self.started_monotonic_s) * 1000, 3),
            "processes": processes,
        }


def _optional_number(raw: str) -> float | None:
    value = raw.strip()
    if not value or value.lower() in {"n/a", "[not supported]", "not supported"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_nvidia_smi_rows(text: str, *, unix_s: float, monotonic_s: float) -> list[dict[str, Any]]:
    """Parse the content-free GPU fields emitted by the fixed nvidia-smi query."""
    rows: list[dict[str, Any]] = []
    for raw_row in csv.reader(text.splitlines()):
        if len(raw_row) != 7:
            continue
        index = _optional_number(raw_row[0])
        utilization = _optional_number(raw_row[2])
        memory_used_mib = _optional_number(raw_row[3])
        memory_total_mib = _optional_number(raw_row[4])
        power_w = _optional_number(raw_row[5])
        temperature_c = _optional_number(raw_row[6])
        rows.append(
            {
                "schema_version": GPU_SAMPLE_SCHEMA_VERSION,
                "timestamp_unix_s": round(unix_s, 6),
                "timestamp_monotonic_s": round(monotonic_s, 6),
                "gpu_index": int(index) if index is not None else None,
                "gpu_uuid": raw_row[1].strip(),
                "gpu_utilization_pct": utilization,
                "memory_used_bytes": int(memory_used_mib * 1024 * 1024) if memory_used_mib is not None else None,
                "memory_total_bytes": int(memory_total_mib * 1024 * 1024) if memory_total_mib is not None else None,
                "power_w": power_w,
                "temperature_c": temperature_c,
            }
        )
    return rows


class NvidiaSmiSampler:
    """Collect GPU utilization/capacity/power samples on a vLLM host."""

    _COMMAND = (
        "nvidia-smi",
        "--query-gpu=index,uuid,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
        "--format=csv,noheader,nounits",
    )

    def __init__(self, path: str | Path, *, interval_s: float = 1.0) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.path = Path(path)
        self.interval_s = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._prepare_file()

    def _prepare_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            with self.path.open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle), [])
            if tuple(header) != GPU_SAMPLE_HEADERS:
                raise ValueError(f"GPU sample CSV header mismatch: {self.path}")
            return
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(GPU_SAMPLE_HEADERS)

    def sample_once(self) -> list[dict[str, Any]]:
        unix_s, monotonic_s = time.time(), time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed operator command, no user-controlled argv
                self._COMMAND,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(2.0, self.interval_s),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"nvidia-smi sampling failed: {type(exc).__name__}") from exc
        rows = parse_nvidia_smi_rows(completed.stdout, unix_s=unix_s, monotonic_s=monotonic_s)
        if not rows:
            raise RuntimeError("nvidia-smi returned no parseable GPU rows")
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=GPU_SAMPLE_HEADERS, extrasaction="raise")
            writer.writerows(rows)
        return rows

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("GPU sampler already running")
        # Fail before a paid run proceeds without the requested telemetry.
        self.sample_once()
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(self.interval_s):
                try:
                    self.sample_once()
                except RuntimeError as exc:
                    self._error = str(exc)
                    self._stop.set()

        self._thread = threading.Thread(target=_loop, name="agentic-os-gpu-sampler", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(join_timeout_s)
        if self._error:
            raise RuntimeError(self._error)


def validate_gpu_samples(path: str | Path, *, max_gap_s: float) -> dict[str, Any]:
    """Validate GPU samples and integrate observed energy with trapezoids."""
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    target = Path(path)
    with target.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != GPU_SAMPLE_HEADERS:
            raise ValueError(f"GPU sample CSV header mismatch: {target}")
        by_gpu: dict[str, list[dict[str, float | None]]] = {}
        for row in reader:
            if row.get("schema_version") != GPU_SAMPLE_SCHEMA_VERSION:
                raise ValueError("GPU sample schema version mismatch")
            try:
                timestamp = float(row["timestamp_monotonic_s"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid monotonic timestamp in GPU samples") from exc
            uuid = str(row.get("gpu_uuid") or "")
            if not uuid:
                raise ValueError("GPU sample is missing uuid")
            by_gpu.setdefault(uuid, []).append(
                {
                    "timestamp": timestamp,
                    "utilization": _optional_number(str(row.get("gpu_utilization_pct") or "")),
                    "memory_used": _optional_number(str(row.get("memory_used_bytes") or "")),
                    "power": _optional_number(str(row.get("power_w") or "")),
                }
            )
    gaps: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for uuid, samples in sorted(by_gpu.items()):
        energy_j = 0.0
        for previous, current in zip(samples, samples[1:]):
            gap = float(current["timestamp"] or 0) - float(previous["timestamp"] or 0)
            if gap <= 0:
                raise ValueError(f"non-increasing monotonic timestamp for GPU {uuid!r}")
            if gap > max_gap_s:
                gaps.append({"gpu_uuid": uuid, "gap_s": round(gap, 6)})
                continue
            p0, p1 = previous["power"], current["power"]
            if isinstance(p0, float) and isinstance(p1, float):
                energy_j += (p0 + p1) * 0.5 * gap
        utilization = [float(item["utilization"]) for item in samples if item["utilization"] is not None]
        memory_used = [float(item["memory_used"]) for item in samples if item["memory_used"] is not None]
        power = [float(item["power"]) for item in samples if item["power"] is not None]
        summaries[uuid] = {
            "samples": len(samples),
            "mean_utilization_pct": round(sum(utilization) / len(utilization), 6) if utilization else None,
            "peak_utilization_pct": max(utilization) if utilization else None,
            "peak_memory_used_bytes": int(max(memory_used)) if memory_used else None,
            "mean_power_w": round(sum(power) / len(power), 6) if power else None,
            "observed_energy_j": round(energy_j, 6),
        }
    return {
        "schema_version": "agentic_os.gpu_sample_validation.v1",
        "scope": "sampler_host_window",
        "gpu_count": len(summaries),
        "row_count": sum(item["samples"] for item in summaries.values()),
        "max_gap_s": max_gap_s,
        "gap_count": len(gaps),
        "gaps": gaps,
        "gpus": summaries,
    }


def build_three_layer_attribution(
    *,
    wall_ms: float,
    llm_ms: float,
    tool_ms: float,
    process_window: Mapping[str, Any] | None,
    process_role_layers: Mapping[str, str],
    database_observation: Mapping[str, Any] | None = None,
    vllm_window_scope: str = "absent",
    observability_overhead_ms: float = 0.0,
) -> dict[str, Any]:
    """Build an honest LLM/database/middleware attribution receipt.

    The sequential critical path is exclusive at the agent boundary. Database
    server timers are auxiliary observations because they can overlap client
    wait/hydration; they are never subtracted a second time from wall time.
    """
    if min(wall_ms, llm_ms, tool_ms, observability_overhead_ms) < 0:
        raise ValueError("wall and component timings must be non-negative")
    workload_wall_ms = max(0.0, wall_ms - observability_overhead_ms)
    residual_ms = max(0.0, workload_wall_ms - llm_ms - tool_ms)
    known_roles = set()
    if isinstance(process_window, Mapping):
        processes = process_window.get("processes")
        if isinstance(processes, Mapping):
            known_roles = {str(role) for role in processes}
    unknown_roles = sorted(set(process_role_layers) - known_roles) if process_window else []
    return {
        "schema_version": "agentic_os.three_layer_attribution.v1",
        "critical_path_exclusive_ms": {
            "llm_http": round(llm_ms, 3),
            "graph_tool_callback": round(tool_ms, 3),
            "middleware_orchestration_residual": round(residual_ms, 3),
            "observability_overhead": round(observability_overhead_ms, 3),
            "workload_wall": round(workload_wall_ms, 3),
            "observed_wall": round(wall_ms, 3),
            "wall": round(workload_wall_ms, 3),
        },
        "layers": {
            "llm": {
                "critical_path_ms": round(llm_ms, 3),
                "vllm_metrics_scope": vllm_window_scope,
                "gpu_samples_scope": "same_host_sampler_window",
            },
            "database": {
                "exclusive_critical_path_ms": None,
                "observation": dict(database_observation) if database_observation else None,
            },
            "middleware": {
                "orchestration_residual_ms": round(residual_ms, 3),
                "graph_tool_callback_ms": round(tool_ms, 3),
            },
        },
        "process_window": dict(process_window) if process_window else None,
        "process_role_layers": dict(process_role_layers),
        "unknown_process_roles": unknown_roles,
        "limitations": [
            "graph_tool_callback includes database wait and middleware pre/post-processing",
            "database server observations may overlap client-side wall time",
            "GPU samples are attributable per case only for serialized same-clock windows",
            "synchronous metrics scrape time is reported as observer overhead and excluded from workload wall",
        ],
    }


def validate_host_process_samples(path: str | Path, *, max_gap_s: float) -> dict[str, Any]:
    """Validate header, timestamps and per-role sampling gaps.

    Returns a receipt rather than failing for a gap: gaps are evidence of
    sampler loss.  Structural corruption is a hard error because it makes the
    artifact uninterpretable.
    """
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    target = Path(path)
    with target.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != HOST_PROCESS_HEADERS:
            raise ValueError(f"host sample CSV header mismatch: {target}")
        by_role: dict[str, list[float]] = {}
        scopes_by_role: dict[str, set[str]] = {}
        member_counts_by_role: dict[str, list[int]] = {}
        unknown_member_counts: dict[str, int] = {}
        rows = 0
        for row in reader:
            rows += 1
            if row.get("schema_version") != HOST_PROCESS_SCHEMA_VERSION:
                raise ValueError("host sample schema version mismatch")
            try:
                timestamp = float(row["timestamp_monotonic_s"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid monotonic timestamp in host samples") from exc
            role = str(row.get("role", ""))
            scope = str(row.get("scope", ""))
            if scope not in _PROCESS_SCOPES:
                raise ValueError(f"invalid process scope for role {role!r}")
            raw_member_count = str(row.get("member_count", ""))
            if raw_member_count:
                try:
                    member_count = int(raw_member_count)
                except ValueError as exc:
                    raise ValueError(f"invalid member count for role {role!r}") from exc
                if member_count <= 0:
                    raise ValueError(f"invalid member count for role {role!r}")
                member_counts_by_role.setdefault(role, []).append(member_count)
            else:
                unknown_member_counts[role] = unknown_member_counts.get(role, 0) + 1
            by_role.setdefault(role, []).append(timestamp)
            scopes_by_role.setdefault(role, set()).add(scope)
    gaps: list[dict[str, Any]] = []
    for role, timestamps in by_role.items():
        for previous, current in zip(timestamps, timestamps[1:]):
            if current <= previous:
                raise ValueError(f"non-increasing monotonic timestamp for role {role!r}")
            gap = current - previous
            if gap > max_gap_s:
                gaps.append({"role": role, "gap_s": round(gap, 6)})
    return {
        "schema_version": "agentic_os.host_process_validation.v2",
        "rows": rows,
        "roles": sorted(by_role),
        "role_scopes": {role: sorted(scopes) for role, scopes in sorted(scopes_by_role.items())},
        "member_counts": {
            role: {
                "min": min(member_counts_by_role.get(role, [])) if member_counts_by_role.get(role) else None,
                "max": max(member_counts_by_role.get(role, [])) if member_counts_by_role.get(role) else None,
                "unknown_rows": unknown_member_counts.get(role, 0),
            }
            for role in sorted(by_role)
        },
        "max_gap_s": max_gap_s,
        "gap_count": len(gaps),
        "gaps": gaps,
    }


# Metric names are intentionally selected from vLLM's documented metric
# families.  The parser accepts both the current colon form (vllm:...) and
# Prometheus' conventional counter ``_total`` suffix.
_VLLM_GAUGES = frozenset(
    {
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:kv_cache_usage_perc",
        "vllm:kv_offload_cpu_cache_usage_perc",
    }
)
_VLLM_COUNTERS = frozenset(
    {
        "vllm:request_success",
        "vllm:prompt_tokens",
        "vllm:generation_tokens",
        "vllm:prefix_cache_hits",
        "vllm:prefix_cache_queries",
        "vllm:external_prefix_cache_hits",
        "vllm:external_prefix_cache_queries",
        "vllm:kv_offload_store_bytes",
        "vllm:kv_offload_load_bytes",
        "vllm:kv_offload_store_time",
        "vllm:kv_offload_load_time",
        "vllm:kv_offload_allocation_failure",
        "vllm:kv_offload_stores_skipped",
        # speculative decoding (lab 020 / 022): drafts, draft tokens, accepted tokens — acceptance rate per window
        "vllm:spec_decode_num_drafts",
        "vllm:spec_decode_num_draft_tokens",
        "vllm:spec_decode_num_accepted_tokens",
        "vllm:spec_decode_num_accepted_tokens_per_pos",
    }
)
_VLLM_HISTOGRAMS = frozenset(
    {
        "vllm:iteration_tokens_total",  # Histogram, not an output-token counter (v0.27.1).
        "vllm:e2e_request_latency_seconds",
        "vllm:time_to_first_token_seconds",
        "vllm:inter_token_latency_seconds",
        "vllm:request_queue_time_seconds",
        "vllm:request_prefill_time_seconds",
        "vllm:request_decode_time_seconds",
        "vllm:request_prompt_tokens",
        "vllm:request_generation_tokens",
        "vllm:request_max_num_generation_tokens",
        "vllm:kv_block_lifetime_seconds",
        "vllm:kv_block_idle_before_evict_seconds",
        "vllm:kv_block_reuse_gap_seconds",
        "vllm:kv_offload_lookup_sync_delay_seconds",
        "vllm:kv_offload_lookup_async_delay_seconds",
    }
)

_VLLM_CACHE_CONFIG_LABELS = frozenset(
    {
        "block_size",
        "cache_dtype",
        "cpu_offload_gb",
        "enable_prefix_caching",
        "gpu_memory_utilization",
        "kv_cache_memory_bytes",
        "num_gpu_blocks",
        "num_cpu_blocks",
    }
)


def _canonical_metric_name(name: str) -> str:
    if name.endswith("_total") and name[:-6] in _VLLM_COUNTERS:
        return name[:-6]
    return name


def parse_vllm_metrics(text: str) -> dict[str, float]:
    """Parse the selected vLLM Prometheus families, summing label series."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, raw_value = line.rpartition(" ")
        if not separator:
            continue
        name = head.split("{", 1)[0]
        canonical = _canonical_metric_name(name)
        wanted = canonical in _VLLM_GAUGES or canonical in _VLLM_COUNTERS
        if not wanted:
            for histogram in _VLLM_HISTOGRAMS:
                if canonical in {f"{histogram}_sum", f"{histogram}_count"}:
                    wanted = True
                    break
        if not wanted:
            continue
        try:
            values[canonical] = values.get(canonical, 0.0) + float(raw_value)
        except ValueError:
            continue
    return values


def parse_vllm_metric_metadata(text: str) -> dict[str, dict[str, str]]:
    """Extract a bounded, content-free subset of vLLM Info metric labels."""
    metadata: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head = line.rpartition(" ")[0]
        name, separator, labels = head.partition("{")
        if not separator or name not in {"vllm:cache_config_info", "vllm_cache_config_info"}:
            continue
        raw_labels = labels.rsplit("}", 1)[0]
        selected: dict[str, str] = {}
        # Cache-config label values are scalar and do not contain commas in the
        # documented vLLM metric. Ignore malformed/unknown labels rather than
        # broadening this content-free parser into a general Prometheus parser.
        for item in raw_labels.split(","):
            key, equals, value = item.partition("=")
            if equals and key in _VLLM_CACHE_CONFIG_LABELS:
                selected[key] = value.strip().strip('"')
        if selected:
            metadata["vllm:cache_config_info"] = selected
    return metadata


def parse_vllm_histogram_buckets(text: str) -> dict[str, dict[str, float]]:
    """Parse documented vLLM histogram buckets without retaining free labels."""
    buckets: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, raw_value = line.rpartition(" ")
        if not separator:
            continue
        name, label_separator, labels = head.partition("{")
        if not label_separator or not name.endswith("_bucket"):
            continue
        base = name.removesuffix("_bucket")
        if base not in _VLLM_HISTOGRAMS:
            continue
        le: str | None = None
        for item in labels.rsplit("}", 1)[0].split(","):
            key, equals, value = item.partition("=")
            if equals and key == "le":
                le = value.strip().strip('"')
                break
        if le is None:
            continue
        try:
            family = buckets.setdefault(base, {})
            family[le] = family.get(le, 0.0) + float(raw_value)
        except ValueError:
            continue
    return buckets


@dataclass(frozen=True)
class VllmMetricsSnapshot:
    """One process-wide metric endpoint snapshot; never request-level evidence."""

    timestamp_unix_s: float
    timestamp_monotonic_s: float
    metrics: dict[str, float]
    available: bool
    metadata: dict[str, dict[str, str]] = field(default_factory=dict)
    histogram_buckets: dict[str, dict[str, float]] = field(default_factory=dict)
    scrape_ms: float = 0.0


class VllmMetricsClient:
    """Small stdlib-only vLLM Prometheus scraper for controlled windows."""

    def __init__(self, url: str, *, timeout_s: float = 2.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.url = url
        self.timeout_s = timeout_s

    def snapshot(self) -> VllmMetricsSnapshot:
        timestamp_unix_s, timestamp_monotonic_s = time.time(), time.monotonic()
        scrape_started = time.perf_counter()
        try:
            with urlopen(self.url, timeout=self.timeout_s) as response:  # noqa: S310 - explicit operator endpoint
                payload = response.read().decode("utf-8", "replace")
                metrics = parse_vllm_metrics(payload)
                metadata = parse_vllm_metric_metadata(payload)
                histogram_buckets = parse_vllm_histogram_buckets(payload)
        except (URLError, OSError, ValueError):
            metrics = {}
            metadata = {}
            histogram_buckets = {}
        return VllmMetricsSnapshot(
            timestamp_unix_s=round(timestamp_unix_s, 6),
            timestamp_monotonic_s=round(timestamp_monotonic_s, 6),
            metrics=metrics,
            available=bool(metrics),
            metadata=metadata,
            histogram_buckets=histogram_buckets,
            scrape_ms=round((time.perf_counter() - scrape_started) * 1000, 3),
        )


class VllmMetricsSampler:
    """Continuously persist content-free vLLM metrics on the serving host.

    This is the preferred appliance-mode observer.  It runs independently of
    the request path, so agent cases only need timestamp/correlation markers;
    no SSH or synchronous ``/metrics`` request is added to case latency.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        client: VllmMetricsClient,
        interval_s: float = 1.0,
        require_available: bool = True,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.path = Path(path)
        self.client = client
        self.interval_s = float(interval_s)
        self.require_available = require_available
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def sample_once(self) -> VllmMetricsSnapshot:
        snapshot = self.client.snapshot()
        if self.require_available and not snapshot.available:
            raise RuntimeError("vLLM metrics endpoint returned no selected metrics")
        payload = {
            "schema_version": "agentic_os.vllm_metrics_sample.v1",
            "timestamp_unix_s": snapshot.timestamp_unix_s,
            "timestamp_monotonic_s": snapshot.timestamp_monotonic_s,
            "available": snapshot.available,
            "scrape_ms": snapshot.scrape_ms,
            "metrics": snapshot.metrics,
            "histogram_buckets": snapshot.histogram_buckets,
            "metadata": snapshot.metadata,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        return snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("vLLM metrics sampler already running")
        # Fail admission before a paid workload silently proceeds without the
        # serving evidence required by the paper protocol.
        self.sample_once()
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(self.interval_s):
                try:
                    self.sample_once()
                except RuntimeError as exc:
                    self._error = str(exc)
                    self._stop.set()

        self._thread = threading.Thread(target=_loop, name="agentic-os-vllm-sampler", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(join_timeout_s)
        if self._error:
            raise RuntimeError(self._error)


def validate_vllm_metrics_samples(path: str | Path, *, max_gap_s: float) -> dict[str, Any]:
    """Validate appliance-local vLLM samples and summarize observer cost."""
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    timestamps: list[float] = []
    scrape_ms: list[float] = []
    metric_names: set[str] = set()
    available = 0
    cache_config: dict[str, str] | None = None
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid vLLM sample JSON at line {line_number}") from exc
            if row.get("schema_version") != "agentic_os.vllm_metrics_sample.v1":
                raise ValueError("vLLM sample schema version mismatch")
            try:
                timestamps.append(float(row["timestamp_monotonic_s"]))
                scrape_ms.append(float(row["scrape_ms"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid timing field in vLLM samples") from exc
            if row.get("available") is True:
                available += 1
            metrics = row.get("metrics")
            if isinstance(metrics, Mapping):
                metric_names.update(str(name) for name in metrics)
            metadata = row.get("metadata")
            if isinstance(metadata, Mapping):
                candidate = metadata.get("vllm:cache_config_info")
                if isinstance(candidate, Mapping):
                    cache_config = {str(key): str(value) for key, value in candidate.items()}
    if not timestamps:
        raise ValueError("vLLM sample file is empty")
    gaps: list[float] = []
    for previous, current in zip(timestamps, timestamps[1:]):
        if current <= previous:
            raise ValueError("non-increasing monotonic timestamp in vLLM samples")
        if current - previous > max_gap_s:
            gaps.append(round(current - previous, 6))
    ordered_scrape = sorted(scrape_ms)
    p95_index = min(len(ordered_scrape) - 1, int(0.95 * len(ordered_scrape)))
    return {
        "schema_version": "agentic_os.vllm_metrics_sample_validation.v1",
        "scope": "same_host_background_sampler",
        "samples": len(timestamps),
        "available_samples": available,
        "availability_ratio": round(available / len(timestamps), 6),
        "gap_count": len(gaps),
        "gaps_s": gaps,
        "max_gap_s": max_gap_s,
        "mean_scrape_ms": round(sum(scrape_ms) / len(scrape_ms), 6),
        "p95_scrape_ms": round(ordered_scrape[p95_index], 6),
        "metric_names_present": sorted(metric_names),
        "cache_config_info": cache_config,
        "critical_path_scrapes": 0,
    }


def vllm_metrics_delta(before: VllmMetricsSnapshot, after: VllmMetricsSnapshot) -> dict[str, float] | None:
    """Return a process-wide controlled-window delta, or ``None`` if absent."""
    if not before.available or not after.available:
        return None
    out: dict[str, float] = {}
    for name, end in after.metrics.items():
        if name in _VLLM_GAUGES:
            out[name] = round(end, 6)
        else:
            change = end - before.metrics.get(name, 0.0)
            if change:
                out[name] = round(change, 6)
    return out


def vllm_histogram_bucket_delta(
    before: VllmMetricsSnapshot,
    after: VllmMetricsSnapshot,
) -> dict[str, dict[str, float]] | None:
    """Return process-window bucket deltas for documented vLLM histograms."""
    if not before.available or not after.available:
        return None
    out: dict[str, dict[str, float]] = {}
    for family, buckets in after.histogram_buckets.items():
        previous = before.histogram_buckets.get(family, {})
        changed = {
            boundary: round(value - previous.get(boundary, 0.0), 6)
            for boundary, value in buckets.items()
            if value - previous.get(boundary, 0.0)
        }
        if changed:
            out[family] = changed
    return out


@dataclass
class VllmMetricsWindow:
    """A metric window around one *serialized* model request."""

    client: VllmMetricsClient
    before: VllmMetricsSnapshot | None = None
    after: VllmMetricsSnapshot | None = None

    def __enter__(self) -> "VllmMetricsWindow":
        self.before = self.client.snapshot()
        return self

    def __exit__(self, *_: object) -> None:
        self.after = self.client.snapshot()

    def receipt(self) -> dict[str, Any]:
        if self.before is None or self.after is None:
            raise RuntimeError("vLLM metric window has not closed")
        return {
            "schema_version": "agentic_os.vllm_metrics_window.v1",
            "scope": "server_process_window",
            "before_available": self.before.available,
            "after_available": self.after.available,
            "window_ms": round((self.after.timestamp_monotonic_s - self.before.timestamp_monotonic_s) * 1000, 3),
            "before_scrape_ms": self.before.scrape_ms,
            "after_scrape_ms": self.after.scrape_ms,
            "observer_overhead_ms": round(self.before.scrape_ms + self.after.scrape_ms, 3),
            "metrics_delta": vllm_metrics_delta(self.before, self.after),
            "histogram_bucket_delta": vllm_histogram_bucket_delta(self.before, self.after),
            "metric_names_present": sorted(self.after.metrics),
            "cache_config_info": self.after.metadata.get("vllm:cache_config_info"),
        }


__all__ = [
    "GPU_SAMPLE_HEADERS",
    "GPU_SAMPLE_SCHEMA_VERSION",
    "HOST_PROCESS_HEADERS",
    "HOST_PROCESS_SCHEMA_VERSION",
    "HostProcessSampler",
    "NvidiaSmiSampler",
    "PROCESS_TREE_SCOPE",
    "ProcessScope",
    "ProcessMetricsWindow",
    "REQUIRED_VLLM_METRIC_NAMES",
    "ROOT_PROCESS_SCOPE",
    "VllmMetricsClient",
    "VllmMetricsSampler",
    "VllmMetricsSnapshot",
    "VllmMetricsWindow",
    "build_three_layer_attribution",
    "parse_nvidia_smi_rows",
    "parse_vllm_histogram_buckets",
    "parse_vllm_metric_metadata",
    "parse_vllm_metrics",
    "validate_gpu_samples",
    "validate_host_process_samples",
    "validate_vllm_metrics_samples",
    "vllm_histogram_bucket_delta",
    "vllm_metrics_delta",
]
