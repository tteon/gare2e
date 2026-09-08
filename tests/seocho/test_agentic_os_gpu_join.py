from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

from seocho.eval.agentic_os_observability import (
    GPU_SAMPLE_HEADERS,
    GPU_SAMPLE_SCHEMA_VERSION,
    REQUIRED_VLLM_METRIC_NAMES,
)


_SCRIPT = Path(__file__).parents[2] / "scripts" / "benchmarks" / "join_agentic_os_gpu_samples.py"
_SPEC = importlib.util.spec_from_file_location("join_agentic_os_gpu_samples", _SCRIPT)
assert _SPEC and _SPEC.loader
GPU_JOIN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(GPU_JOIN)


def test_summarize_window_interpolates_boundaries_and_energy() -> None:
    samples = [
        {"timestamp": 0.0, "utilization": 0.0, "memory_used": 10.0, "power": 100.0},
        {"timestamp": 1.0, "utilization": 100.0, "memory_used": 20.0, "power": 200.0},
        {"timestamp": 2.0, "utilization": 0.0, "memory_used": 30.0, "power": 100.0},
    ]

    result = GPU_JOIN.summarize_window(samples, start=0.5, end=1.5, max_gap_s=2.5)

    assert result["sample_count"] == 1
    assert result["mean_utilization_pct"] == 75.0
    assert result["mean_power_w"] == 175.0
    assert result["observed_energy_j"] == 175.0
    assert result["peak_memory_used_bytes"] == 20
    assert result["coverage_ratio"] == 1.0


def test_summarize_window_exposes_sampler_gap() -> None:
    samples = [
        {"timestamp": 0.0, "utilization": 10.0, "power": 100.0},
        {"timestamp": 5.0, "utilization": 20.0, "power": 110.0},
    ]

    result = GPU_JOIN.summarize_window(samples, start=1.0, end=4.0, max_gap_s=2.5)

    assert result["mean_utilization_pct"] is None
    assert result["observed_energy_j"] is None
    assert result["coverage_ratio"] == 0.0
    assert result["sample_gap_count"] == 1


def _write_case(root: Path, *, role: str | None = "single_agent") -> None:
    attempt = root / "lane" / "attempts" / "000000-case"
    attempt.mkdir(parents=True)
    receipt = {
        "run_id": "run-1",
        "case_id": "case-1",
        "arm": "B0",
        "seed": 0,
        "repetition": 0,
        "cache_phase": "warm",
        "role": role,
        # These fields prove the join does not copy workload content.
        "question": "PRIVATE QUESTION SENTINEL",
        "query": "PRIVATE QUERY SENTINEL",
        "resource_attribution": {
            "process_window": {
                "schema_version": "agentic_os.process_metrics_window.v1",
                "clock_domain": "local_host_monotonic",
                "started_monotonic_s": 10.0,
                "ended_monotonic_s": 20.0,
            }
        },
    }
    (attempt / "receipts.jsonl").write_text(
        json.dumps(receipt) + "\n", encoding="utf-8"
    )


def _write_vllm_samples(path: Path, *, omit_metric: str | None = None) -> None:
    rows = []
    for ordinal, timestamp in enumerate((9.0, 15.0, 21.0)):
        metrics = {
            name: (
                0.2 + ordinal * 0.1
                if name in GPU_JOIN.VLLM_GAUGES
                else float(ordinal)
            )
            for name in REQUIRED_VLLM_METRIC_NAMES
            if name != omit_metric
        }
        rows.append(
            {
                "schema_version": "agentic_os.vllm_metrics_sample.v1",
                "timestamp_unix_s": 1009.0 + timestamp,
                "timestamp_monotonic_s": timestamp,
                "available": True,
                "scrape_ms": 0.1,
                "metrics": metrics,
                "histogram_buckets": {
                    "vllm:e2e_request_latency_seconds": {"1.0": float(ordinal)}
                },
                "metadata": {
                    "vllm:cache_config_info": {"enable_prefix_caching": "True"}
                },
            }
        )
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _write_gpu_samples(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GPU_SAMPLE_HEADERS)
        writer.writeheader()
        for ordinal, timestamp in enumerate((9.0, 15.0, 21.0)):
            writer.writerow(
                {
                    "schema_version": GPU_SAMPLE_SCHEMA_VERSION,
                    "timestamp_unix_s": 1009.0 + timestamp,
                    "timestamp_monotonic_s": timestamp,
                    "gpu_index": 0,
                    "gpu_uuid": "GPU-content-free-id",
                    "gpu_utilization_pct": 10 + ordinal * 10,
                    "memory_used_bytes": 100 + ordinal * 10,
                    "memory_total_bytes": 1000,
                    "power_w": 200 + ordinal * 10,
                    "temperature_c": 50,
                }
            )


def test_join_receipt_requires_complete_same_host_case_coverage(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    _write_case(run_root)
    vllm = tmp_path / "vllm-metrics.jsonl"
    gpu = tmp_path / "gpu.csv"
    _write_vllm_samples(vllm)
    _write_gpu_samples(gpu)

    receipt = GPU_JOIN.build_join_receipt(
        [run_root],
        vllm_samples_path=vllm,
        gpu_samples_path=gpu,
        max_gap_s=6.5,
    )

    assert receipt["schema_version"] == "agentic_os.case_telemetry_join.v1"
    assert receipt["passed"] is True
    assert receipt["coverage_ratio"] == 1.0
    assert receipt["correlation_complete_count"] == 1
    case = receipt["cases"][0]
    assert case["vllm"]["isolated_case_window"] is True
    assert case["vllm"]["metrics_delta"]["vllm:e2e_request_latency_seconds_count"] == 2.0
    assert case["gpu"]["devices"][0]["coverage_ratio"] == 1.0
    public_text = json.dumps(receipt)
    assert "PRIVATE QUESTION SENTINEL" not in public_text
    assert "PRIVATE QUERY SENTINEL" not in public_text


def test_join_receipt_fails_closed_on_correlation_metric_and_gpu_gaps(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs"
    _write_case(run_root, role=None)
    vllm = tmp_path / "vllm-metrics.jsonl"
    gpu = tmp_path / "gpu.csv"
    omitted = "vllm:request_decode_time_seconds_count"
    _write_vllm_samples(vllm, omit_metric=omitted)
    _write_gpu_samples(gpu)

    receipt = GPU_JOIN.build_join_receipt(
        [run_root],
        vllm_samples_path=vllm,
        gpu_samples_path=gpu,
        max_gap_s=5.0,
    )

    assert receipt["passed"] is False
    assert receipt["coverage_ratio"] == 0.0
    assert "correlation_coverage_incomplete" in receipt["blockers"]
    case = receipt["cases"][0]
    assert "correlation_incomplete" in case["blockers"]
    assert "vllm_required_metrics_missing" in case["vllm"]["blockers"]
    assert case["vllm"]["missing_required_metrics"] == [omitted]
    assert "gpu_sample_gap" in case["gpu"]["blockers"]


def test_process_window_accepts_v1_and_v2_schemas() -> None:
    module = _module() if "_module" in globals() else None
    import importlib.util
    from pathlib import Path as _P
    if module is None:
        path = _P(__file__).parents[2] / "scripts" / "benchmarks" / "join_agentic_os_gpu_samples.py"
        spec = importlib.util.spec_from_file_location("join_agentic_os_gpu_samples", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    for version in ("v1", "v2"):
        receipt = {
            "resource_attribution": {
                "process_window": {
                    "schema_version": f"agentic_os.process_metrics_window.{version}",
                    "clock_domain": "local_host_monotonic",
                    "started_monotonic_s": 10.0,
                    "ended_monotonic_s": 12.5,
                }
            }
        }
        assert module._process_window(receipt) == (10.0, 12.5)
    receipt["resource_attribution"]["process_window"]["schema_version"] = "agentic_os.process_metrics_window.v9"
    assert module._process_window(receipt) is None


def test_valid_correlation_rejects_bool_and_requires_string_ids() -> None:
    import importlib.util
    from pathlib import Path as _P
    path = _P(__file__).parents[2] / "scripts" / "benchmarks" / "join_agentic_os_gpu_samples.py"
    spec = importlib.util.spec_from_file_location("join_agentic_os_gpu_samples", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    good = {"run_id": "r", "case_id": "102", "arm": "B0", "seed": 0, "repetition": 0, "cache_phase": "warm", "role": "single_agent"}
    assert module._valid_correlation(good)
    assert not module._valid_correlation({**good, "case_id": 102})
    assert not module._valid_correlation({**good, "seed": True})
