from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from seocho.eval.agentic_os_graph_system import (
    attach_graph_evidence,
    graph_system_sample,
    plan_runtime,
    summarize_graph_system,
)


def _stat(path: Path, start: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["0"] * 22
    fields[19] = str(start)
    path.write_text("42 (private name with spaces) " + " ".join(fields))


def test_numeric_snapshot_does_not_export_process_names_or_device_paths(tmp_path: Path) -> None:
    proc, cg = tmp_path / "proc", tmp_path / "cg"
    cg.mkdir()
    _stat(proc / "42/stat", 100)
    _stat(proc / "42/task/42/stat", 100)
    (proc / "42/task/42/schedstat").write_text("1000000000 500000000 4")
    (proc / "42/status").write_text("Name: secret\nCpus_allowed_list: 2-3\nMems_allowed_list: 0\n")
    (proc / "diskstats").write_text("259 0 private-device 1 2 3 4 5 6 7 8 9 10 11\n")
    (cg / "memory.numa_stat").write_text("anon N0=4096 N1=8192\nfile N0=12\nsecret N0=99")
    (cg / "cpu.stat").write_text("usage_usec 10\nthrottled_usec 0\nunknown 12")
    (cg / "cpu.pressure").write_text("some avg10=0 total=123\nfull avg10=0 total=0")
    sample = graph_system_sample({"db": 42}, proc=proc, cgroup=cg)
    assert sample["processes"]["db"]["run_s"] == 1
    assert sample["processes"]["db"]["wait_s"] == .5
    assert sample["processes"]["db"]["complete"]
    assert sample["numa_allocation_bytes"]["anon"] == {"N0": 4096, "N1": 8192}
    assert sample["devices"]["259:0"]["read_sectors"] == 3
    assert sample["pressure"]["cpu"]["full_total_us"] == 0
    assert "private" not in json.dumps(sample)
    assert "secret" not in json.dumps(sample)


def test_absent_linux_interfaces_are_unknown(tmp_path: Path) -> None:
    sample = graph_system_sample({"db": 99}, proc=tmp_path, cgroup=tmp_path)
    assert sample["processes"]["db"]["run_s"] is None
    assert sample["processes"]["db"]["threads_seen"] is None
    assert sample["devices"] is None
    assert sample["numa_allocation_bytes"] is None
    summary = summarize_graph_system([], 0, 10)
    assert not summary["full_window_coverage"]
    assert all(r["status"] == "not_collected" and r["value"] is None for r in summary["coverage"])


def _sample(stamp: float, run: float, *, tid: int = 42) -> dict:
    return {"timestamp_unix_s": stamp, "schedstats_enabled": True, "collection_ms": .5,
            "processes": {"db": {"pid": 42, "start_ticks": 100,
                "thread_identities": {str(tid): 100}, "complete": True, "threads_seen": 1,
                "run_s": run, "wait_s": run / 2, "allowed_masks": {"Cpus_allowed_list": "0-1"}}}}


def test_counter_deltas_reject_churn_resets_and_gaps() -> None:
    stable = summarize_graph_system([_sample(0, 1), _sample(5, 3)], 0, 5)
    assert stable["processes"]["db"]["run_s_delta"] == 2
    assert stable["processes"]["db"]["wait_s_delta"] == 1
    assert "thread_identities" not in json.dumps(stable)
    for samples in ([_sample(0, 1), _sample(5, 3, tid=43)],
                    [_sample(0, 3), _sample(5, 1)],
                    [_sample(0, 1), _sample(50, 3)]):
        summary = summarize_graph_system(samples, 0, samples[-1]["timestamp_unix_s"])
        assert summary["processes"]["db"]["run_s_delta"] is None
    samples = [_sample(0, 1), _sample(5, 3)]
    samples[-1]["schedstats_enabled"] = False
    assert summarize_graph_system(samples, 0, 5)["processes"]["db"]["wait_s_delta"] is None


def test_profile_rows_are_never_presented_as_visited_nodes() -> None:
    summary = attach_graph_evidence(summarize_graph_system([], 0, 1),
        tools=[{"rows": 100, "result_bytes": 200}], profiles=[{"plan": {"total_db_hits": 30}}],
        capabilities={"procedures": {"status": "unavailable"}}, agent_samples_present=True)
    status = {r["measurement"]: r["status"] for r in summary["coverage"]}
    assert status["profile_operators_db_hits"] == "available_separate_evidence"
    assert status["traversal_work"] == "not_collected"
    assert status["procedure_inventory"] == "not_collected"
    assert status["answer_equivalence"] == "not_collected"


def test_capability_probe_records_denial_without_leaking_exception() -> None:
    script = Path(__file__).parents[2] / "scripts/benchmarks/probe_agentic_os_graph_capabilities.py"
    spec = importlib.util.spec_from_file_location("graph_capabilities", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Driver:
        def session(self, **kwargs: object) -> None:
            assert kwargs["default_access_mode"] == "READ"
            raise RuntimeError("secret password and connection address")

    receipt = module.probe(Driver(), "neo4j")
    assert receipt["components"]["status"] == "unavailable"
    assert receipt["procedures"]["error_class"] == "RuntimeError"
    assert receipt["store_format"] is None
    assert "secret" not in json.dumps(receipt)


def test_runtime_is_observed_from_plan_not_requested_or_inferred() -> None:
    assert plan_runtime({"args": {"runtime": "slotted", "Details": "secret"}}) == "SLOTTED"
    assert plan_runtime({"args": {"requested_runtime": "parallel"}}) is None
    assert plan_runtime({"args": {"runtime": "private query"}}) is None
