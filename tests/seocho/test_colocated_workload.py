from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

from seocho.eval.agentic_os_observability import VllmMetricsSnapshot


def _module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_agentic_os_colocated_workload.py"
    spec = importlib.util.spec_from_file_location("run_agentic_os_colocated_workload", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_role_pid_rejects_invalid_values() -> None:
    module = _module()
    assert module._parse_role_pid("vllm=12") == ("vllm", 12)
    with pytest.raises(Exception, match="ROLE=POSITIVE_PID"):
        module._parse_role_pid("vllm=0")


def test_colocated_wrapper_keeps_metrics_out_of_workload_path(tmp_path, monkeypatch) -> None:
    module = _module()

    class FakeMetricsClient:
        def __init__(self, *_: object, **__: object) -> None:
            self.calls = 0

        def snapshot(self) -> VllmMetricsSnapshot:
            self.calls += 1
            now = time.monotonic()
            metrics = {name: 1.0 for name in module.REQUIRED_VLLM_METRIC_NAMES}
            metrics["vllm:kv_cache_usage_perc"] = 0.5
            metrics["vllm:prefix_cache_queries"] = float(self.calls)
            return VllmMetricsSnapshot(
                time.time(),
                now,
                metrics,
                True,
                metadata={"vllm:cache_config_info": {"enable_prefix_caching": "True"}},
                scrape_ms=0.1,
            )

    monkeypatch.setattr(module, "VllmMetricsClient", FakeMetricsClient)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agentic_os_colocated_workload.py",
            "--artifacts",
            str(artifacts),
            "--role-pid",
            f"vllm={os.getpid()}",
            "--role-pid",
            f"dozerdb={os.getpid()}",
            "--interval-s",
            "0.01",
            "--max-gap-s",
            "1",
            "--no-gpu",
            "--workload",
            sys.executable,
            "-c",
            "import time; time.sleep(0.03)",
        ],
    )

    assert module.main() == 0
    receipt = json.loads((artifacts / "colocated-workload-receipt.json").read_text(encoding="utf-8"))
    assert receipt["topology"] == "single_host_colocated"
    assert receipt["critical_path_metrics_scrapes"] == 0
    assert receipt["role_process_scopes"] == {
        "agent": "process_tree",
        "dozerdb": "process_tree",
        "vllm": "process_tree",
    }
    assert receipt["validation"]["vllm"]["available_samples"] >= 1
    assert receipt["telemetry_acceptance"]["passed"] is True
    assert (artifacts / "host-process.csv").exists()
    assert (artifacts / "vllm-metrics.jsonl").exists()
    with (artifacts / "host-process.csv").open(newline="", encoding="utf-8") as handle:
        host_rows = list(csv.DictReader(handle))
    assert host_rows
    assert {row["scope"] for row in host_rows} == {"process_tree"}
    assert all(not row["member_count"] or int(row["member_count"]) >= 1 for row in host_rows)
    assert all(
        any(row["role"] == role and row["member_count"] for row in host_rows)
        for role in ("agent", "dozerdb", "vllm")
    )


def test_telemetry_acceptance_fails_closed_on_missing_metrics() -> None:
    module = _module()

    acceptance = module._telemetry_acceptance(
        {
            "host_process": {"rows": 3, "gap_count": 0, "roles": ["agent", "dozerdb", "vllm"]},
            "vllm": {
                "availability_ratio": 1.0,
                "gap_count": 0,
                "metric_names_present": ["vllm:num_requests_running"],
                "cache_config_info": {"enable_prefix_caching": "True"},
            },
            "gpu": {"gpu_count": 2, "row_count": 4, "gap_count": 0},
        }
    )

    assert acceptance["passed"] is False
    assert any(item.startswith("vllm_metric_families_missing:") for item in acceptance["blockers"])


def test_telemetry_acceptance_requires_tree_scope_and_known_members() -> None:
    module = _module()
    roles = ["agent", "dozerdb", "vllm"]
    host = {
        "rows": 3,
        "gap_count": 0,
        "roles": roles,
        "role_scopes": {role: ["root_process"] for role in roles},
        "member_counts": {role: {"min": None, "max": None, "unknown_rows": 1} for role in roles},
    }
    metrics = {name: 1.0 for name in module.REQUIRED_VLLM_METRIC_NAMES}

    acceptance = module._telemetry_acceptance(
        {
            "host_process": host,
            "vllm": {
                "availability_ratio": 1.0,
                "gap_count": 0,
                "metric_names_present": sorted(metrics),
                "cache_config_info": {"enable_prefix_caching": "True"},
            },
        }
    )

    assert acceptance["passed"] is False
    assert "host_process_tree_scope_missing:agent,dozerdb,vllm" in acceptance["blockers"]
    assert "host_process_members_missing:agent,dozerdb,vllm" in acceptance["blockers"]


def _run_wrapper(module, monkeypatch, tmp_path, *, workload_cmd: str, metrics_ok: bool, extra: list[str]) -> tuple[int, dict]:
    class MetricsClient:
        def __init__(self, *_: object, **__: object) -> None:
            self.calls = 0

        def snapshot(self) -> VllmMetricsSnapshot:
            self.calls += 1
            now = time.monotonic()
            if not metrics_ok:  # vLLM restarted mid-pass: endpoint answers but exposes none of the selected metrics
                return VllmMetricsSnapshot(time.time(), now, {}, False, metadata={}, scrape_ms=0.1)
            metrics = {name: 1.0 for name in module.REQUIRED_VLLM_METRIC_NAMES}
            metrics["vllm:kv_cache_usage_perc"] = 0.5
            metrics["vllm:prefix_cache_queries"] = float(self.calls)
            return VllmMetricsSnapshot(time.time(), now, metrics, True, metadata={"vllm:cache_config_info": {"enable_prefix_caching": "True"}}, scrape_ms=0.1)

    monkeypatch.setattr(module, "VllmMetricsClient", MetricsClient)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(sys, "argv", [
        "run_agentic_os_colocated_workload.py", "--artifacts", str(artifacts),
        "--role-pid", f"vllm={os.getpid()}", "--role-pid", f"dozerdb={os.getpid()}",
        "--interval-s", "0.01", "--max-gap-s", "1", "--no-gpu", *extra,
        "--workload", sys.executable, "-c", workload_cmd,
    ])
    code = module.main()
    return code, json.loads((artifacts / "colocated-workload-receipt.json").read_text(encoding="utf-8"))


def test_wrapper_exit_follows_workload_and_records_telemetry_degradation(tmp_path, monkeypatch) -> None:
    """lab-s3/s4/s6: every pass succeeded but the wrapper's exit 3 marked the session failed."""
    module = _module()
    code, receipt = _run_wrapper(module, monkeypatch, tmp_path / "a", workload_cmd="import time; time.sleep(0.03)", metrics_ok=False, extra=[])
    assert code == 0
    assert receipt["workload_exit_code"] == 0
    assert receipt["telemetry_acceptance"]["passed"] is False
    assert receipt["telemetry_degraded"] is True and receipt["telemetry_degradation"]
    assert receipt["exit_policy"] == "workload_exit_code" and receipt["exit_code"] == 0
    # a healthy pass is not flagged
    code, receipt = _run_wrapper(module, monkeypatch, tmp_path / "b", workload_cmd="import time; time.sleep(0.03)", metrics_ok=True, extra=[])
    assert code == 0 and receipt["telemetry_degraded"] is False


def test_wrapper_propagates_workload_failure_and_honours_strict_telemetry(tmp_path, monkeypatch) -> None:
    module = _module()
    code, receipt = _run_wrapper(module, monkeypatch, tmp_path / "a", workload_cmd="import sys; sys.exit(7)", metrics_ok=True, extra=[])
    assert code == 7 and receipt["exit_code"] == 7   # the workload's own status, not a flat 3
    code, receipt = _run_wrapper(module, monkeypatch, tmp_path / "b", workload_cmd="import time; time.sleep(0.03)", metrics_ok=False, extra=["--strict-telemetry"])
    assert code == 3 and receipt["exit_policy"] == "strict_telemetry"
