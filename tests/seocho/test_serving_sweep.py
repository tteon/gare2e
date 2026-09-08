from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _module():
    path = ROOT / "scripts" / "benchmarks" / "sweep_agentic_os_serving.py"
    spec = importlib.util.spec_from_file_location("sweep_agentic_os_serving", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_example_spec_loads_and_plans_remote_commands_without_shell_injection() -> None:
    module = _module()
    spec = module.load_spec(ROOT / "examples/agentic-os/serving-sweep.yaml")
    ids = [v["id"] for v in spec["variants"]]
    assert ids[0] == "baseline" and "cudagraph-full" in ids
    full = next(v for v in spec["variants"] if v["id"] == "cudagraph-full")
    env = module.pass_env(spec, full)
    assert env["PASS_ID"] == "vllm-serving-v1-cudagraph-full"
    assert env["SEOCHO_GATE_B0_CRITERION"] == "attempted" and env["SEOCHO_PROMPT_PROFILE"] == "baseline"
    cmd = module.remote_command(env)
    assert cmd.endswith("bash /workspace/seocho/docker/agentic-os-appliance/run_lab_pass.sh")
    assert "VLLM_EXTRA_ARGS_OVERRIDE='--compilation-config '\"'\"'{\"cudagraph_mode\":\"FULL\"}'\"'\"''" in cmd
    baseline_env = module.pass_env(spec, spec["variants"][0])
    assert baseline_env["VLLM_EXTRA_ARGS_OVERRIDE"]  # baseline still restarts for a cold, identical server state


def test_spec_validation_rejects_missing_baseline_and_protected_env(tmp_path: Path) -> None:
    module = _module()
    bad = {"schema_version": module.SCHEMA, "name": "x", "model": "m", "model_revision": "r", "variants": [{"id": "a", "vllm_args": []}]}
    p = tmp_path / "s.yaml"; p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_spec(p)
    bad["variants"] = [{"id": "baseline", "vllm_args": []}, {"id": "b", "env": {"SEOCHO_E2E_PHASE": "main"}}]
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_spec(p)


def test_summary_pairs_cells_and_guards_on_funnel(tmp_path: Path) -> None:
    module = _module()
    spec = {"schema_version": module.SCHEMA, "name": "sw", "model": "m", "model_revision": "r",
            "metrics_of_record": ["wall_s", "decode_s_per_req"], "funnel_guard": ["graph_queries", "query_validated"],
            "variants": [{"id": "baseline"}, {"id": "eager"}]}
    def write(variant, rows):
        d = tmp_path / f"sw-{variant}" / "analysis"; d.mkdir(parents=True)
        with (d / "attempt-decomposition.csv").open("w", newline="") as h:
            w = csv.DictWriter(h, fieldnames=["cell", "wall_s", "decode_s_per_req", "graph_queries", "query_validated"]); w.writeheader(); w.writerows(rows)
    write("baseline", [{"cell": "c1", "wall_s": 10, "decode_s_per_req": 2.0, "graph_queries": 1, "query_validated": True},
                       {"cell": "c2", "wall_s": 20, "decode_s_per_req": 4.0, "graph_queries": 1, "query_validated": True}])
    write("eager", [{"cell": "c1", "wall_s": 13, "decode_s_per_req": 3.0, "graph_queries": 1, "query_validated": True},
                    {"cell": "c2", "wall_s": 25, "decode_s_per_req": 5.0, "graph_queries": 2, "query_validated": True}])
    rows = module.summarise(spec, tmp_path)
    eager = {r["cell"]: r for r in rows if r["variant"] == "eager"}
    assert eager["c1"]["funnel_matches_baseline"] is True and eager["c1"]["wall_s_delta_vs_baseline"] == 3.0
    assert eager["c2"]["funnel_matches_baseline"] is False and eager["c2"]["wall_s_delta_vs_baseline"] is None
    module.write_summary(rows, spec, tmp_path)
    md = (tmp_path / "serving-sweep-summary.md").read_text(encoding="utf-8")
    assert "| eager | 2 | 1 | +3.000 | +1.000 |" in md
