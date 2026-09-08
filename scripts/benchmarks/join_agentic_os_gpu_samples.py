#!/usr/bin/env python3
"""Join same-host vLLM/GPU samples to serialized AgenticOS case windows.

The output is intentionally content-free: it contains correlation identifiers,
timings, counters, gauges, hashes, and acceptance blockers, but never prompts,
queries, evidence, or model output. All clocks must be the host monotonic clock;
cross-host clock offsets are deliberately unsupported.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from seocho.eval.agentic_os_observability import (
    GPU_SAMPLE_HEADERS,
    GPU_SAMPLE_SCHEMA_VERSION,
    REQUIRED_VLLM_METRIC_NAMES,
)


JOIN_SCHEMA_VERSION = "agentic_os.case_telemetry_join.v1"
CASE_JOIN_SCHEMA_VERSION = "agentic_os.case_telemetry_window.v1"
VLLM_SAMPLE_SCHEMA_VERSION = "agentic_os.vllm_metrics_sample.v1"
CORRELATION_FIELDS = (
    "run_id",
    "case_id",
    "arm",
    "seed",
    "repetition",
    "cache_phase",
    "role",
)
VLLM_GAUGES = frozenset(
    {
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:kv_cache_usage_perc",
        "vllm:kv_offload_cpu_cache_usage_perc",
    }
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _valid_correlation(correlation: Mapping[str, Any]) -> bool:
    for field in CORRELATION_FIELDS:
        value = correlation.get(field)
        if value is None or isinstance(value, bool):
            return False
        if field in {"seed", "repetition"}:
            if not isinstance(value, int) or value < 0:
                return False
        elif not isinstance(value, str) or not value.strip():
            return False
    return True


def _require_increasing(samples: Sequence[Mapping[str, Any]], *, label: str) -> None:
    for previous, current in zip(samples, samples[1:]):
        if float(current["timestamp"]) <= float(previous["timestamp"]):
            raise ValueError(f"{label} sample monotonic timestamps are not strictly increasing")


def load_samples(path: Path) -> dict[str, list[dict[str, float]]]:
    """Load content-free GPU samples keyed by UUID using monotonic time."""
    by_gpu: dict[str, list[dict[str, float]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != GPU_SAMPLE_HEADERS:
            raise ValueError("GPU sample CSV header mismatch")
        for row in reader:
            if row.get("schema_version") != GPU_SAMPLE_SCHEMA_VERSION:
                raise ValueError("GPU sample schema mismatch")
            gpu_uuid = str(row.get("gpu_uuid") or "").strip()
            timestamp = _number(row.get("timestamp_monotonic_s"))
            if not gpu_uuid or timestamp is None:
                raise ValueError("GPU sample identity or monotonic timestamp is missing")
            values = {
                "timestamp": timestamp,
                "utilization": _number(row.get("gpu_utilization_pct")),
                "memory_used": _number(row.get("memory_used_bytes")),
                "memory_total": _number(row.get("memory_total_bytes")),
                "power": _number(row.get("power_w")),
            }
            by_gpu.setdefault(gpu_uuid, []).append(
                {key: float(value) for key, value in values.items() if value is not None}
            )
    for samples in by_gpu.values():
        samples.sort(key=lambda item: item["timestamp"])
        _require_increasing(samples, label="GPU")
    return by_gpu


def load_vllm_samples(path: Path) -> list[dict[str, Any]]:
    """Load the bounded metric families persisted by VllmMetricsSampler."""
    samples: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid vLLM sample JSON at line {line_number}") from exc
            if row.get("schema_version") != VLLM_SAMPLE_SCHEMA_VERSION:
                raise ValueError("vLLM sample schema mismatch")
            timestamp = _number(row.get("timestamp_monotonic_s"))
            if timestamp is None:
                raise ValueError("vLLM sample monotonic timestamp is missing")
            raw_metrics = row.get("metrics")
            metrics = {
                str(name): float(value)
                for name, raw in (raw_metrics.items() if isinstance(raw_metrics, Mapping) else ())
                if (value := _number(raw)) is not None
            }
            raw_buckets = row.get("histogram_buckets")
            buckets: dict[str, dict[str, float]] = {}
            if isinstance(raw_buckets, Mapping):
                for family, raw_values in raw_buckets.items():
                    if not isinstance(raw_values, Mapping):
                        continue
                    selected = {
                        str(boundary): float(value)
                        for boundary, raw in raw_values.items()
                        if (value := _number(raw)) is not None
                    }
                    if selected:
                        buckets[str(family)] = selected
            metadata = row.get("metadata")
            cache_config = None
            if isinstance(metadata, Mapping):
                raw_config = metadata.get("vllm:cache_config_info")
                if isinstance(raw_config, Mapping):
                    cache_config = {str(key): str(value) for key, value in raw_config.items()}
            samples.append(
                {
                    "timestamp": timestamp,
                    "available": row.get("available") is True,
                    "metrics": metrics,
                    "histogram_buckets": buckets,
                    "cache_config_info": cache_config,
                }
            )
    samples.sort(key=lambda item: item["timestamp"])
    _require_increasing(samples, label="vLLM")
    return samples


def _integrate(
    samples: list[dict[str, float]],
    field: str,
    *,
    start: float,
    end: float,
    max_gap_s: float,
) -> tuple[float, float]:
    integral = 0.0
    covered = 0.0
    for left, right in zip(samples, samples[1:]):
        t0, t1 = left["timestamp"], right["timestamp"]
        if t1 <= start or t0 >= end or t1 - t0 <= 0 or t1 - t0 > max_gap_s:
            continue
        if field not in left or field not in right:
            continue
        low, high = max(start, t0), min(end, t1)
        if high <= low:
            continue
        slope = (right[field] - left[field]) / (t1 - t0)
        v0 = left[field] + slope * (low - t0)
        v1 = left[field] + slope * (high - t0)
        integral += (v0 + v1) * 0.5 * (high - low)
        covered += high - low
    return integral, covered


def _bracket_window(
    samples: Sequence[Mapping[str, Any]],
    *,
    start: float,
    end: float,
) -> tuple[int, int, list[Mapping[str, Any]]] | None:
    if end <= start or not samples:
        return None
    timestamps = [float(row["timestamp"]) for row in samples]
    left = bisect.bisect_right(timestamps, start) - 1
    right = bisect.bisect_left(timestamps, end)
    if left < 0 or right >= len(samples) or left >= right:
        return None
    return left, right, list(samples[left : right + 1])


def _window_gaps(
    samples: Sequence[Mapping[str, Any]],
    *,
    start: float,
    end: float,
    max_gap_s: float,
) -> list[float]:
    gaps: list[float] = []
    for left, right in zip(samples, samples[1:]):
        t0, t1 = float(left["timestamp"]), float(right["timestamp"])
        if t1 <= start or t0 >= end:
            continue
        if t1 - t0 > max_gap_s:
            gaps.append(round(t1 - t0, 6))
    return gaps


def summarize_window(
    samples: list[dict[str, float]],
    *,
    start: float,
    end: float,
    max_gap_s: float,
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("GPU window end must be after start")
    bracket = _bracket_window(samples, start=start, end=end)
    duration = end - start
    if bracket is None:
        return {
            "sample_count": 0,
            "mean_utilization_pct": None,
            "peak_memory_used_bytes": None,
            "mean_power_w": None,
            "observed_energy_j": None,
            "coverage_ratio": 0.0,
            "power_coverage_ratio": 0.0,
            "sample_gap_count": 0,
            "sample_gaps_s": [],
            "window_s": round(duration, 6),
            "boundary_covered": False,
        }
    _left, _right, selected = bracket
    window = [dict(item) for item in selected]
    util_integral, util_covered = _integrate(
        window, "utilization", start=start, end=end, max_gap_s=max_gap_s
    )
    power_integral, power_covered = _integrate(
        window, "power", start=start, end=end, max_gap_s=max_gap_s
    )
    memory = [
        row["memory_used"]
        for row in window
        if start <= row["timestamp"] <= end and "memory_used" in row
    ]
    gaps = _window_gaps(window, start=start, end=end, max_gap_s=max_gap_s)
    return {
        "sample_count": sum(start <= row["timestamp"] <= end for row in window),
        "mean_utilization_pct": round(util_integral / util_covered, 6) if util_covered else None,
        "peak_memory_used_bytes": int(max(memory)) if memory else None,
        "mean_power_w": round(power_integral / power_covered, 6) if power_covered else None,
        "observed_energy_j": round(power_integral, 6) if power_covered else None,
        "coverage_ratio": round(util_covered / duration, 6),
        "power_coverage_ratio": round(power_covered / duration, 6),
        "sample_gap_count": len(gaps),
        "sample_gaps_s": gaps,
        "window_s": round(duration, 6),
        "boundary_covered": True,
    }


def _iter_receipts(roots: Iterable[Path]) -> Iterable[dict[str, Any]]:
    seen_paths: set[Path] = set()
    for root in roots:
        paths = sorted((*root.rglob("run-receipt.json"), *root.rglob("receipts.jsonl")))
        for path in paths:
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            if path.name == "run-receipt.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                cases = payload.get("cases")
                if not isinstance(cases, list):
                    raise ValueError("direct runner receipt has no cases list")
                for ordinal, case in enumerate(cases):
                    if not isinstance(case, Mapping):
                        raise ValueError("direct runner case is not an object")
                    yield {
                        "receipt": dict(case),
                        "source_record_sha256": _canonical_digest(dict(case)),
                        "source_record_ordinal": ordinal,
                    }
            else:
                for ordinal, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                    if not line.strip():
                        continue
                    case = json.loads(line)
                    if not isinstance(case, Mapping):
                        raise ValueError("runner JSONL case is not an object")
                    yield {
                        "receipt": dict(case),
                        "source_record_sha256": hashlib.sha256(line.encode()).hexdigest(),
                        "source_record_ordinal": ordinal,
                    }


_PROCESS_WINDOW_SCHEMAS = frozenset(
    {"agentic_os.process_metrics_window.v1", "agentic_os.process_metrics_window.v2"}
)


def _process_window(receipt: Mapping[str, Any]) -> tuple[float, float] | None:
    attribution = receipt.get("resource_attribution")
    if not isinstance(attribution, Mapping):
        return None
    window = attribution.get("process_window")
    if not isinstance(window, Mapping):
        return None
    # v2 (agentic_os_observability.ProcessMetricsWindow) keeps the same
    # monotonic boundary keys and adds per-role counters; the co-located
    # runners have emitted v2 since 2026-08-31 while this join still demanded
    # v1, so every V17 case was reported as "window missing".
    if (
        window.get("schema_version") not in _PROCESS_WINDOW_SCHEMAS
        or window.get("clock_domain") != "local_host_monotonic"
    ):
        return None
    start = _number(window.get("started_monotonic_s"))
    end = _number(window.get("ended_monotonic_s"))
    return (start, end) if start is not None and end is not None and end > start else None


def _vllm_window(
    samples: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    max_gap_s: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    bracket = _bracket_window(samples, start=start, end=end)
    if bracket is None:
        return {
            "passed": False,
            "blockers": ["vllm_boundary_samples_missing"],
            "clock_domain": "same_host_monotonic",
        }
    left, right, selected_raw = bracket
    selected = [dict(item) for item in selected_raw]
    gaps = _window_gaps(selected, start=start, end=end, max_gap_s=max_gap_s)
    if gaps:
        blockers.append("vllm_sample_gap")
    if not all(bool(sample.get("available")) for sample in selected):
        blockers.append("vllm_sample_unavailable")
    before, after = selected[0], selected[-1]
    before_metrics = before["metrics"]
    after_metrics = after["metrics"]
    missing = sorted(
        REQUIRED_VLLM_METRIC_NAMES - (set(before_metrics) & set(after_metrics))
    )
    if missing:
        blockers.append("vllm_required_metrics_missing")

    metric_names = sorted(set(before_metrics) & set(after_metrics))
    deltas: dict[str, float] = {}
    resets: list[str] = []
    for name in metric_names:
        if name in VLLM_GAUGES:
            continue
        delta = float(after_metrics[name]) - float(before_metrics[name])
        if delta < 0:
            resets.append(name)
        else:
            deltas[name] = round(delta, 6)
    if resets:
        blockers.append("vllm_counter_reset")

    gauges: dict[str, dict[str, float]] = {}
    for name in sorted(VLLM_GAUGES):
        values = [
            float(sample["metrics"][name])
            for sample in selected
            if name in sample["metrics"]
        ]
        if values:
            gauges[name] = {
                "start": round(values[0], 6),
                "end": round(values[-1], 6),
                "mean": round(sum(values) / len(values), 6),
                "max": round(max(values), 6),
            }

    bucket_deltas: dict[str, dict[str, float]] = {}
    before_buckets = before.get("histogram_buckets") or {}
    after_buckets = after.get("histogram_buckets") or {}
    for family in sorted(set(before_buckets) & set(after_buckets)):
        changed: dict[str, float] = {}
        for boundary in sorted(set(before_buckets[family]) & set(after_buckets[family])):
            delta = float(after_buckets[family][boundary]) - float(before_buckets[family][boundary])
            if delta < 0:
                blockers.append("vllm_histogram_reset")
            elif delta:
                changed[boundary] = round(delta, 6)
        if changed:
            bucket_deltas[family] = changed

    request_count = deltas.get("vllm:e2e_request_latency_seconds_count")
    if request_count is None or request_count <= 0:
        blockers.append("vllm_request_delta_not_positive")
    cache_config = after.get("cache_config_info") or before.get("cache_config_info")
    if not isinstance(cache_config, Mapping):
        blockers.append("vllm_cache_config_missing")
        cache_config = None
    elif str(cache_config.get("enable_prefix_caching", "")).lower() != "true":
        blockers.append("vllm_prefix_caching_not_enabled")

    return {
        "passed": not blockers,
        "blockers": sorted(set(blockers)),
        "clock_domain": "same_host_monotonic",
        "boundary_sample_indices": [left, right],
        "boundary_monotonic_s": [before["timestamp"], after["timestamp"]],
        "sample_count": len(selected),
        "sample_gap_count": len(gaps),
        "sample_gaps_s": gaps,
        "required_metrics": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "missing_required_metrics": missing,
        "counter_resets": sorted(resets),
        "metrics_delta": deltas,
        "gauge_summary": gauges,
        "histogram_bucket_delta": bucket_deltas,
        "cache_config_info": dict(cache_config) if cache_config else None,
    }


def _gpu_window(
    samples_by_gpu: Mapping[str, list[dict[str, float]]],
    *,
    start: float,
    end: float,
    max_gap_s: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    devices: list[dict[str, Any]] = []
    if not samples_by_gpu:
        blockers.append("gpu_devices_missing")
    for gpu_uuid, samples in sorted(samples_by_gpu.items()):
        summary = summarize_window(samples, start=start, end=end, max_gap_s=max_gap_s)
        device_blockers: list[str] = []
        if not summary["boundary_covered"]:
            device_blockers.append("gpu_boundary_samples_missing")
        if summary["sample_gap_count"]:
            device_blockers.append("gpu_sample_gap")
        if float(summary["coverage_ratio"]) < 1.0:
            device_blockers.append("gpu_utilization_coverage_incomplete")
        devices.append(
            {
                "gpu_uuid": gpu_uuid,
                **summary,
                "passed": not device_blockers,
                "blockers": device_blockers,
            }
        )
        blockers.extend(device_blockers)
    return {
        "passed": not blockers,
        "blockers": sorted(set(blockers)),
        "clock_domain": "same_host_monotonic",
        "device_count": len(devices),
        "devices": devices,
    }


def build_join_receipt(
    run_roots: Sequence[Path],
    *,
    vllm_samples_path: Path,
    gpu_samples_path: Path,
    max_gap_s: float,
) -> dict[str, Any]:
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    source_records = list(_iter_receipts(run_roots))
    vllm_samples = load_vllm_samples(vllm_samples_path)
    gpu_samples = load_samples(gpu_samples_path)
    blockers: list[str] = []
    cases: list[dict[str, Any]] = []
    valid_windows: dict[str, tuple[float, float]] = {}
    seen_keys: dict[str, int] = {}

    if not source_records:
        blockers.append("case_receipts_missing")
    if not vllm_samples:
        blockers.append("vllm_samples_missing")
    if not gpu_samples:
        blockers.append("gpu_samples_missing")

    for source in source_records:
        receipt = source["receipt"]
        correlation = {field: receipt.get(field) for field in CORRELATION_FIELDS}
        # GTSQA single-agent receipts carry the parquet integer qid as case_id;
        # every other producer writes a string.  Normalise here so the join does
        # not drop a case over a type difference in an otherwise complete id.
        if isinstance(correlation.get("case_id"), int) and not isinstance(correlation.get("case_id"), bool):
            correlation["case_id"] = str(correlation["case_id"])
        case_blockers: list[str] = []
        if not _valid_correlation(correlation):
            case_blockers.append("correlation_incomplete")
            case_key = source["source_record_sha256"]
        else:
            case_key = _canonical_digest(correlation)
            seen_keys[case_key] = seen_keys.get(case_key, 0) + 1
        process_window = _process_window(receipt)
        if process_window is None:
            case_blockers.append("case_monotonic_window_missing")
            vllm = {
                "passed": False,
                "blockers": ["case_monotonic_window_missing"],
                "clock_domain": "same_host_monotonic",
            }
            gpu = {
                "passed": False,
                "blockers": ["case_monotonic_window_missing"],
                "clock_domain": "same_host_monotonic",
                "device_count": 0,
                "devices": [],
            }
            window_receipt = None
        else:
            start, end = process_window
            valid_windows[case_key] = process_window
            window_receipt = {
                "started_monotonic_s": round(start, 6),
                "ended_monotonic_s": round(end, 6),
                "window_ms": round((end - start) * 1000, 3),
            }
            vllm = _vllm_window(
                vllm_samples, start=start, end=end, max_gap_s=max_gap_s
            )
            gpu = _gpu_window(
                gpu_samples, start=start, end=end, max_gap_s=max_gap_s
            )
            case_blockers.extend(vllm["blockers"])
            case_blockers.extend(gpu["blockers"])
        cases.append(
            {
                "schema_version": CASE_JOIN_SCHEMA_VERSION,
                "case_key": case_key,
                "correlation": correlation,
                "source_record_sha256": source["source_record_sha256"],
                "source_record_ordinal": source["source_record_ordinal"],
                "clock_domain": "same_host_monotonic",
                "window": window_receipt,
                "vllm": vllm,
                "gpu": gpu,
                "passed": False,
                "blockers": sorted(set(case_blockers)),
            }
        )

    duplicate_keys = {key for key, count in seen_keys.items() if count > 1}
    if duplicate_keys:
        blockers.append("duplicate_case_correlation")
    for case in cases:
        if case["case_key"] in duplicate_keys:
            case["blockers"] = sorted(
                set((*case["blockers"], "duplicate_case_correlation"))
            )

    # Process-wide counters are attributable only when the selected sample
    # bracket contains no other serialized case window.
    for case in cases:
        boundary = case["vllm"].get("boundary_monotonic_s")
        if not isinstance(boundary, list) or len(boundary) != 2:
            continue
        foreign = sorted(
            key
            for key, (start, end) in valid_windows.items()
            if key != case["case_key"]
            and start < float(boundary[1])
            and end > float(boundary[0])
        )
        case["vllm"]["foreign_case_keys"] = foreign
        case["vllm"]["isolated_case_window"] = not foreign
        if foreign:
            case["vllm"]["passed"] = False
            case["vllm"]["blockers"] = sorted(
                set((*case["vllm"]["blockers"], "vllm_window_contains_foreign_case"))
            )
            case["blockers"] = sorted(
                set((*case["blockers"], "vllm_window_contains_foreign_case"))
            )

    for case in cases:
        case["passed"] = (
            not case["blockers"] and case["vllm"]["passed"] and case["gpu"]["passed"]
        )
        if not case["passed"]:
            blockers.append(f"case_join_failed:{case['case_key']}")

    discovered = len(cases)
    joined = sum(bool(case["passed"]) for case in cases)
    coverage = joined / discovered if discovered else 0.0
    if coverage != 1.0:
        blockers.append("case_telemetry_coverage_incomplete")
    if any(not _valid_correlation(case["correlation"]) for case in cases):
        blockers.append("correlation_coverage_incomplete")

    cases.sort(key=lambda item: item["case_key"])
    return {
        "schema_version": JOIN_SCHEMA_VERSION,
        "passed": not blockers,
        "clock_domain": "same_host_monotonic",
        "content_policy": "correlation_and_numeric_telemetry_only",
        "inputs": {
            "run_root_count": len(run_roots),
            "vllm_samples": {
                "sha256": _sha256(vllm_samples_path),
                "bytes": vllm_samples_path.stat().st_size,
            },
            "gpu_samples": {
                "sha256": _sha256(gpu_samples_path),
                "bytes": gpu_samples_path.stat().st_size,
            },
        },
        "required_vllm_metrics": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "cases_discovered": discovered,
        "cases_joined": joined,
        "coverage_ratio": round(coverage, 6),
        "correlation_complete_count": sum(
            _valid_correlation(case["correlation"]) for case in cases
        ),
        "blockers": sorted(set(blockers)),
        "cases": cases,
    }


def _failure_receipt(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": JOIN_SCHEMA_VERSION,
        "passed": False,
        "clock_domain": "same_host_monotonic",
        "content_policy": "correlation_and_numeric_telemetry_only",
        "cases_discovered": 0,
        "cases_joined": 0,
        "coverage_ratio": 0.0,
        "correlation_complete_count": 0,
        "required_vllm_metrics": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "blockers": ["input_validation_failed"],
        "failure": {"error_class": type(exc).__name__, "error_message": str(exc)},
        "cases": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--vllm-samples", required=True, type=Path)
    parser.add_argument("--gpu-samples", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-gap-s", type=float, default=2.5)
    args = parser.parse_args()
    try:
        receipt = build_join_receipt(
            args.run_roots,
            vllm_samples_path=args.vllm_samples,
            gpu_samples_path=args.gpu_samples,
            max_gap_s=args.max_gap_s,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        receipt = _failure_receipt(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": receipt["passed"],
                "cases_discovered": receipt["cases_discovered"],
                "cases_joined": receipt["cases_joined"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
