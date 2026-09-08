from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_raw_agentic_rag_pilot.py"
    spec = importlib.util.spec_from_file_location("raw_agentic_rag_pilot", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(tmp_path, lane: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    dataset = tmp_path / ("questions.jsonl" if lane == "grbench" else "test.parquet")
    dataset.write_text("test", encoding="utf-8")
    return argparse.Namespace(
        lane=lane,
        dataset=dataset,
        run_root=tmp_path / "runs",
        base_url="http://unused",
        metrics_url="http://unused/metrics",
        model="test-model",
        condition=["closed_book"],
        reasoning=["non_reasoning"],
        case_id=["7"],
        limit=1,
        repetitions=1,
        max_turns=4,
        db_pid=123,
    )


def test_raw_pilot_builds_backend_specific_commands(tmp_path) -> None:
    module = _module()
    grbench = _args(tmp_path / "g", "grbench")
    gtsqa = _args(tmp_path / "r", "gtsqa")
    gr_command = module._runner_command(
        grbench,
        condition="closed_book",
        reasoning="non_reasoning",
        repetition=0,
        root=grbench.run_root,
        run_id="gr",
    )
    rdf_command = module._runner_command(
        gtsqa,
        condition="closed_book",
        reasoning="non_reasoning",
        repetition=0,
        root=gtsqa.run_root,
        run_id="rdf",
    )
    assert "run_direct_react_text2cypher.py" in gr_command[1]
    assert "--qid" in gr_command
    assert "run_gtsqa_text2sparql_react.py" in rdf_command[1]
    assert "--case-id" in rdf_command
