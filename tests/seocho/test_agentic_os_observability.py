from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import time

import pytest

import seocho.eval.agentic_os_observability as observability
from seocho.eval.agentic_os_observability import (
    GPU_SAMPLE_HEADERS,
    HOST_PROCESS_HEADERS,
    HostProcessSampler,
    PROCESS_TREE_SCOPE,
    ProcessMetricsWindow,
    VllmMetricsClient,
    VllmMetricsSampler,
    VllmMetricsSnapshot,
    VllmMetricsWindow,
    build_three_layer_attribution,
    parse_nvidia_smi_rows,
    parse_vllm_histogram_buckets,
    parse_vllm_metric_metadata,
    parse_vllm_metrics,
    validate_gpu_samples,
    validate_host_process_samples,
    validate_vllm_metrics_samples,
    vllm_metrics_delta,
)


def test_host_process_sampler_writes_content_free_samples_and_detects_gap(tmp_path) -> None:
    path = tmp_path / "host.csv"
    sampler = HostProcessSampler(path, roles={"test": os.getpid()}, interval_s=0.01)
    rows = sampler.sample_once()
    assert rows[0]["role"] == "test"
    assert rows[0]["scope"] == "root_process"
    assert rows[0]["member_count"] == 1
    assert rows[0]["process_rss_bytes"] is not None
    time.sleep(0.012)
    sampler.sample_once()

    report = validate_host_process_samples(path, max_gap_s=1.0)
    assert report["rows"] == 2
    assert report["gap_count"] == 0
    assert report["role_scopes"] == {"test": ["root_process"]}
    assert report["member_counts"]["test"] == {"min": 1, "max": 1, "unknown_rows": 0}
    assert "prompt" not in path.read_text(encoding="utf-8").lower()

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[1]["timestamp_monotonic_s"] = str(float(rows[0]["timestamp_monotonic_s"]) + 3.0)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HOST_PROCESS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    assert validate_host_process_samples(path, max_gap_s=1.0)["gap_count"] == 1


def test_host_process_sampler_refuses_header_mismatch(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("wrong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header mismatch"):
        HostProcessSampler(path, roles={"test": os.getpid()})


def test_process_metrics_window_emits_per_case_cpu_and_io_deltas() -> None:
    with ProcessMetricsWindow({"middleware": os.getpid()}) as window:
        sum(index * index for index in range(10_000))
    receipt = window.receipt()
    process = receipt["processes"]["middleware"]
    assert receipt["scope"] == "serialized_case_window"
    assert receipt["window_ms"] >= 0
    assert process["cpu_total_s"] is not None
    assert process["cpu_total_s"] >= 0
    assert process["rss_start_bytes"] > 0
    assert process["scope"] == "root_process"
    assert process["member_count"] == {"start": 1, "end": 1}


def _process_snapshot(
    pid: int,
    ppid: int,
    start_ticks: int,
    *,
    cpu_user_s: float,
    rss_bytes: int,
) -> observability._ProcessSnapshot:
    return observability._ProcessSnapshot(
        pid=pid,
        ppid=ppid,
        start_ticks=start_ticks,
        metrics={
            "process_cpu_user_s": cpu_user_s,
            "process_cpu_system_s": 0.5,
            "process_rss_bytes": rss_bytes,
            "process_threads": 1,
            "process_read_bytes": 10,
            "process_write_bytes": 20,
        },
    )


def test_process_tree_scope_aggregates_recursive_descendants_by_ppid(monkeypatch) -> None:
    table = {
        10: _process_snapshot(10, 1, 100, cpu_user_s=1.0, rss_bytes=100),
        11: _process_snapshot(11, 10, 110, cpu_user_s=2.0, rss_bytes=200),
        12: _process_snapshot(12, 11, 120, cpu_user_s=3.0, rss_bytes=300),
        20: _process_snapshot(20, 1, 200, cpu_user_s=50.0, rss_bytes=5_000),
    }
    monkeypatch.setattr(observability, "_read_process_table", lambda: table)

    snapshot = observability._read_process_scope(
        10,
        process_scope=PROCESS_TREE_SCOPE,
        expected_root_start_ticks=100,
    )

    assert snapshot.valid is True
    assert snapshot.member_count == 3
    assert snapshot.members == {10: 100, 11: 110, 12: 120}
    assert snapshot.metrics["process_cpu_user_s"] == 6.0
    assert snapshot.metrics["process_rss_bytes"] == 600


def test_process_table_discovers_members_without_reading_io(monkeypatch) -> None:
    calls: list[tuple[int, bool]] = []

    def fake_snapshot(pid: int, *, include_io: bool = True) -> observability._ProcessSnapshot:
        calls.append((pid, include_io))
        return _process_snapshot(pid, 1, pid * 10, cpu_user_s=1.0, rss_bytes=100)

    monkeypatch.setattr(Path, "iterdir", lambda self: iter((Path("/proc/10"), Path("/proc/11"))))
    monkeypatch.setattr(observability, "_read_process_snapshot", fake_snapshot)

    assert sorted(observability._read_process_table()) == [10, 11]
    assert calls == [(10, False), (11, False)]


def test_process_scope_marks_reused_root_pid_unknown(monkeypatch) -> None:
    reused_root = _process_snapshot(10, 1, 101, cpu_user_s=9.0, rss_bytes=900)
    monkeypatch.setattr(observability, "_read_process_table", lambda: {10: reused_root})

    snapshot = observability._read_process_scope(
        10,
        process_scope=PROCESS_TREE_SCOPE,
        expected_root_start_ticks=100,
    )

    assert snapshot.valid is False
    assert snapshot.member_count is None
    assert all(value is None for value in snapshot.metrics.values())


def test_host_sampler_marks_exited_tree_member_counters_unknown(tmp_path, monkeypatch) -> None:
    root = _process_snapshot(10, 1, 100, cpu_user_s=1.0, rss_bytes=100)
    before = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 5.0},
        member_count=2,
        members={10: 100, 11: 110},
        valid=True,
    )
    after = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 6.0},
        member_count=1,
        members={10: 100},
        valid=True,
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(observability, "_read_process_snapshot", lambda pid, **kwargs: root)
    monkeypatch.setattr(observability, "_read_process_table", lambda: {})
    monkeypatch.setattr(observability, "_read_process_scope", lambda *args, **kwargs: next(snapshots))
    sampler = HostProcessSampler(
        tmp_path / "tree.csv",
        roles={"service": 10},
        process_scope=PROCESS_TREE_SCOPE,
    )

    assert sampler.sample_once()[0]["process_cpu_user_s"] == 5.0
    second = sampler.sample_once()[0]

    assert second["scope"] == PROCESS_TREE_SCOPE
    assert second["member_count"] == 1
    assert second["process_cpu_user_s"] is None
    assert second["process_read_bytes"] is None
    assert second["process_rss_bytes"] == 100


def test_process_window_marks_exited_member_and_negative_counters_unknown(monkeypatch) -> None:
    root = _process_snapshot(10, 1, 100, cpu_user_s=1.0, rss_bytes=100)
    before = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 5.0, "process_read_bytes": 100},
        member_count=2,
        members={10: 100, 11: 110},
        valid=True,
    )
    after = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 4.0, "process_read_bytes": 90},
        member_count=1,
        members={10: 100},
        valid=True,
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(observability, "_read_process_snapshot", lambda pid, **kwargs: root)
    monkeypatch.setattr(observability, "_read_process_scope", lambda *args, **kwargs: next(snapshots))

    with ProcessMetricsWindow({"service": 10}, process_scope=PROCESS_TREE_SCOPE) as window:
        pass

    process = window.receipt()["processes"]["service"]
    assert process["scope"] == PROCESS_TREE_SCOPE
    assert process["member_count"] == {"start": 2, "end": 1}
    assert process["cumulative_counters_comparable"] is False
    assert process["cpu_user_s"] is None
    assert process["read_bytes"] is None
    assert process["rss_start_bytes"] == 100
    assert process["rss_end_bytes"] == 100


def test_process_window_marks_negative_delta_unknown_with_stable_members(monkeypatch) -> None:
    root = _process_snapshot(10, 1, 100, cpu_user_s=1.0, rss_bytes=100)
    before = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 5.0},
        member_count=1,
        members={10: 100},
        valid=True,
    )
    after = observability._ProcessScopeSnapshot(
        metrics={**root.metrics, "process_cpu_user_s": 4.0},
        member_count=1,
        members={10: 100},
        valid=True,
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(observability, "_read_process_snapshot", lambda pid, **kwargs: root)
    monkeypatch.setattr(observability, "_read_process_scope", lambda *args, **kwargs: next(snapshots))

    with ProcessMetricsWindow({"service": 10}, process_scope=PROCESS_TREE_SCOPE) as window:
        pass

    process = window.receipt()["processes"]["service"]
    assert process["cumulative_counters_comparable"] is True
    assert process["cpu_user_s"] is None
    assert process["cpu_total_s"] is None


def test_gpu_sample_parser_and_energy_validation(tmp_path) -> None:
    rows = parse_nvidia_smi_rows(
        "0, GPU-abc, 75, 1024, 81920, 200.5, 42\n",
        unix_s=10.0,
        monotonic_s=20.0,
    )
    assert rows[0]["gpu_utilization_pct"] == 75.0
    assert rows[0]["memory_used_bytes"] == 1024 * 1024 * 1024

    path = tmp_path / "gpu.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GPU_SAMPLE_HEADERS)
        writer.writeheader()
        writer.writerow(rows[0])
        writer.writerow(
            {
                **rows[0],
                "timestamp_unix_s": 11.0,
                "timestamp_monotonic_s": 21.0,
                "gpu_utilization_pct": 25.0,
                "power_w": 100.5,
            }
        )
    report = validate_gpu_samples(path, max_gap_s=1.5)
    gpu = report["gpus"]["GPU-abc"]
    assert report["gap_count"] == 0
    assert gpu["mean_utilization_pct"] == 50.0
    assert gpu["observed_energy_j"] == 150.5


def test_three_layer_attribution_keeps_tool_and_database_observation_non_additive() -> None:
    attribution = build_three_layer_attribution(
        wall_ms=100.0,
        llm_ms=60.0,
        tool_ms=30.0,
        process_window={"processes": {"agent": {}, "dozerdb": {}}},
        process_role_layers={"agent": "middleware", "dozerdb": "database"},
        database_observation={"source": "bolt_server_summary", "server_reported_ms": 20.0},
        vllm_window_scope="serialized_request",
        observability_overhead_ms=5.0,
    )
    critical = attribution["critical_path_exclusive_ms"]
    assert critical["middleware_orchestration_residual"] == 5.0
    assert critical["observed_wall"] == 100.0
    assert critical["workload_wall"] == 95.0
    assert critical["observability_overhead"] == 5.0
    assert sum(critical[name] for name in ("llm_http", "graph_tool_callback", "middleware_orchestration_residual")) == 95.0
    assert attribution["layers"]["database"]["exclusive_critical_path_ms"] is None


_METRICS = """
# HELP vllm:prefix_cache_queries_total queries
vllm:prefix_cache_queries_total{model_name="x"} 3
vllm:prefix_cache_queries_total{model_name="y"} 4
vllm:prefix_cache_hits_total 5
vllm:kv_cache_usage_perc 0.75
vllm:e2e_request_latency_seconds_sum 1.25
vllm:e2e_request_latency_seconds_count 2
vllm:e2e_request_latency_seconds_bucket{le="0.1",model_name="x"} 1
vllm:e2e_request_latency_seconds_bucket{le="0.1",model_name="y"} 2
vllm:prompt_tokens_total{model_name="x"} 120
vllm:request_prefill_time_seconds_sum 0.25
vllm:request_prefill_time_seconds_count 2
vllm:kv_block_reuse_gap_seconds_sum 0.5
vllm:kv_block_reuse_gap_seconds_count 4
vllm:cache_config_info{block_size="16",cache_dtype="auto",enable_prefix_caching="True",gpu_memory_utilization="0.9",model_name="x"} 1
unrelated_metric 10
"""


def test_vllm_metrics_parse_and_window_delta_without_network(monkeypatch) -> None:
    parsed = parse_vllm_metrics(_METRICS)
    assert parsed["vllm:prefix_cache_queries"] == 7.0
    assert parsed["vllm:prefix_cache_hits"] == 5.0
    assert parsed["vllm:e2e_request_latency_seconds_count"] == 2.0
    assert parsed["vllm:prompt_tokens"] == 120.0
    assert parsed["vllm:request_prefill_time_seconds_count"] == 2.0
    assert parsed["vllm:kv_block_reuse_gap_seconds_count"] == 4.0
    assert parse_vllm_histogram_buckets(_METRICS) == {
        "vllm:e2e_request_latency_seconds": {"0.1": 3.0}
    }
    assert parse_vllm_metric_metadata(_METRICS) == {
        "vllm:cache_config_info": {
            "block_size": "16",
            "cache_dtype": "auto",
            "enable_prefix_caching": "True",
            "gpu_memory_utilization": "0.9",
        }
    }

    before = VllmMetricsSnapshot(1.0, 2.0, parsed, True, scrape_ms=1.25)
    after = VllmMetricsSnapshot(
        2.0,
        3.0,
        {**parsed, "vllm:prefix_cache_queries": 9.0, "vllm:kv_cache_usage_perc": 0.9},
        True,
        scrape_ms=2.75,
    )
    assert vllm_metrics_delta(before, after) == {
        "vllm:prefix_cache_queries": 2.0,
        "vllm:kv_cache_usage_perc": 0.9,
    }

    snapshots = iter([before, after])
    client = VllmMetricsClient("http://unused")
    monkeypatch.setattr(client, "snapshot", lambda: next(snapshots))
    with VllmMetricsWindow(client) as window:
        pass
    receipt = window.receipt()
    assert receipt["scope"] == "server_process_window"
    assert receipt["metrics_delta"]["vllm:prefix_cache_queries"] == 2.0
    assert receipt["observer_overhead_ms"] == 4.0
    assert "vllm:prompt_tokens" in receipt["metric_names_present"]


def test_vllm_window_marks_unavailable_metrics_as_absent() -> None:
    snapshot = VllmMetricsSnapshot(1.0, 1.0, {}, False)
    assert vllm_metrics_delta(snapshot, snapshot) is None


def test_same_host_vllm_sampler_persists_content_free_series(tmp_path, monkeypatch) -> None:
    snapshots = iter(
        [
            VllmMetricsSnapshot(
                10.0,
                20.0,
                {"vllm:kv_cache_usage_perc": 0.25, "vllm:prefix_cache_queries": 4.0},
                True,
                metadata={"vllm:cache_config_info": {"enable_prefix_caching": "True"}},
                scrape_ms=0.4,
            ),
            VllmMetricsSnapshot(
                11.0,
                21.0,
                {"vllm:kv_cache_usage_perc": 0.5, "vllm:prefix_cache_queries": 8.0},
                True,
                metadata={"vllm:cache_config_info": {"enable_prefix_caching": "True"}},
                scrape_ms=0.6,
            ),
        ]
    )
    client = VllmMetricsClient("http://unused")
    monkeypatch.setattr(client, "snapshot", lambda: next(snapshots))
    path = tmp_path / "vllm.jsonl"
    sampler = VllmMetricsSampler(path, client=client)
    sampler.sample_once()
    sampler.sample_once()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["metrics"]["vllm:kv_cache_usage_perc"] == 0.25
    assert "prompt" not in path.read_text(encoding="utf-8").lower()
    report = validate_vllm_metrics_samples(path, max_gap_s=1.5)
    assert report["samples"] == 2
    assert report["availability_ratio"] == 1.0
    assert report["critical_path_scrapes"] == 0
    assert report["cache_config_info"]["enable_prefix_caching"] == "True"


def test_same_host_vllm_sampler_rejects_missing_metrics(tmp_path, monkeypatch) -> None:
    client = VllmMetricsClient("http://unused")
    monkeypatch.setattr(
        client,
        "snapshot",
        lambda: VllmMetricsSnapshot(10.0, 20.0, {}, False, scrape_ms=0.1),
    )
    sampler = VllmMetricsSampler(tmp_path / "vllm.jsonl", client=client)
    with pytest.raises(RuntimeError, match="no selected metrics"):
        sampler.sample_once()
