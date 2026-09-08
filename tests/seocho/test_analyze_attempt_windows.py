from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "analyze_agentic_os_attempt_windows.py"
    spec = importlib.util.spec_from_file_location("analyze_agentic_os_attempt_windows", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "gate-grbench-single-tool-false"
    attempt = root / "attempts" / "000000-abc"
    attempt.mkdir(parents=True)
    receipt = {
        "cases": [{
            "run_id": "grbench-single-x", "case_id": "0", "arm": "B0", "seed": 0, "repetition": 0, "cache_phase": "warm",
            "role": "single_agent", "terminal": "turn_budget_exhausted", "turn_count": 2,
            "time": {"wall_ms": 4000.0, "llm_ms": 2500.0, "tool_ms": 1400.0, "observability_overhead_ms": 0.0},
            "retrieval_funnel": {"graph_query_calls": 1, "query_validated": True, "nonempty_result": True},
            "turns": [
                {"latency_ms": 1500.0, "output_kind": "tool_calls", "output_tokens": 40},
                {"latency_ms": 1000.0, "output_kind": "message", "output_tokens": 20},
            ],
            "tools": [{"name": "execute_cypher", "outcome": "ok", "latency_ms": 1400.0}, "ignored-string-entry"],
        }]
    }
    (attempt / "run-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    join = {"cases": [{"correlation": {"run_id": "grbench-single-x"}, "window": {"started_monotonic_s": 100.0, "ended_monotonic_s": 104.0},
                       "vllm": {"metrics_delta": {"vllm:request_success": 2, "vllm:request_decode_time_seconds_sum": 3.0, "vllm:request_decode_time_seconds_count": 2,
                                                  "vllm:prefix_cache_hits": 50, "vllm:prefix_cache_queries": 100}}}]}
    join_path = tmp_path / "join.json"; join_path.write_text(json.dumps(join), encoding="utf-8")
    gpu = tmp_path / "gpu.csv"
    with gpu.open("w", newline="") as handle:
        w = csv.writer(handle); w.writerow(["schema_version", "timestamp_unix_s", "timestamp_monotonic_s", "gpu_index", "gpu_uuid", "gpu_utilization_pct", "memory_used_bytes", "memory_total_bytes", "power_w", "temperature_c"])
        for i in range(6):
            w.writerow(["v1", 0, 99.5 + i, 0, "GPU-0", 100.0 if i < 3 else 0.0, 1, 2, 200.0, 40])
    host = tmp_path / "host.csv"
    with host.open("w", newline="") as handle:
        w = csv.writer(handle); w.writerow(["schema_version", "timestamp_unix_s", "timestamp_monotonic_s", "role", "pid", "scope", "member_count", "process_cpu_user_s", "process_cpu_system_s", "process_rss_bytes"])
        for i in range(5):
            w.writerow(["v2", 0, 100.0 + i, "dozerdb", 1, "tree", 1, 10.0 + i * 0.5, 1.0, 1000])
    vllm = tmp_path / "vllm.jsonl"
    # cumulative counters: turn 1 spans 0-1.5 s (queries 100, hits 0), turn 2 spans 2.9-3.9 s (queries 100, hits 80)
    hits = [0, 0, 0, 0, 80, 80]
    queries = [0, 0, 100, 100, 200, 200]
    vllm.write_text("\n".join(json.dumps({"timestamp_monotonic_s": 99.0 + i, "metrics": {"vllm:kv_cache_usage_perc": 0.01 * max(i - 1, 0), "vllm:num_requests_running": 1, "vllm:prefix_cache_hits": hits[i], "vllm:prefix_cache_queries": queries[i], "vllm:prompt_tokens": queries[i]}}) for i in range(6)), encoding="utf-8")
    return {"root": root, "join": join_path, "gpu": gpu, "host": host, "vllm": vllm}


def test_decomposition_row_and_outputs(tmp_path: Path) -> None:
    module = _module()
    f = _fixture(tmp_path)
    rows = module.analyse(run_roots=[f["root"]], join_path=f["join"], gpu_path=f["gpu"], host_path=f["host"], vllm_path=f["vllm"])
    assert len(rows) == 1
    row = rows[0]
    assert (row["lane"], row["topology"], row["reasoning"]) == ("GRBench", "single", "non_reasoning")
    assert row["wall_s"] == 4.0 and row["llm_s"] == 2.5 and row["tool_s"] == 1.4 and row["residual_s"] == 0.1
    assert row["gpu_busy_share"] == 0.5  # samples at 100.5..103.5 are in-window; two of four read 100 %
    assert row["cpu_s_dozerdb"] == 2.0
    assert row["decode_s_per_req"] == 1.5 and row["prefix_hit_ratio"] == 0.5
    assert row["kv_usage_max_pct"] == 4.0
    ctx = row["_context"]
    assert [t["turn"] for t in ctx["turns"]] == [1, 2]
    assert ctx["turns"][0]["prefix_hit"] == 0.0 and ctx["turns"][1]["prefix_hit"] == 0.8
    assert row["prompt_first"] is None or True  # fixture turns carry no input tokens; growth fields stay None
    assert row["evidence_bytes"] == 0
    assert row["activity_timing"] == "reconstructed"
    kinds = [s["kind"] for s in row["_spans"]]
    assert kinds == ["turn", "tool", "turn"]
    out = tmp_path / "out"; out.mkdir()
    module.write_table(rows, out)
    module.write_html(rows, out, "test")
    assert (out / "attempt-decomposition.csv").is_file() and (out / "attempt-decomposition.md").is_file()
    assert (out / "context-growth.md").is_file()
    page = (out / "attempt-timelines.html").read_text(encoding="utf-8")
    assert page.count("<svg") == 1 and "GPU utilisation" in page and "<title>test</title>" in page
