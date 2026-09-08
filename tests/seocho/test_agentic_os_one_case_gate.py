from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/benchmarks/validate_agentic_os_one_case_gate.py"
SPEC = importlib.util.spec_from_file_location("validate_agentic_os_one_case_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_cell(root: Path, backend: str, topology: str, reasoning: str) -> None:
    lane = "gtsqa" if backend.startswith("gtsqa") else "grbench"
    lane += "-multi" if topology == "manager_specialist_as_tool" else "-single"
    path = root / f"gate-{lane}-tool-{'true' if reasoning == 'reasoning' else 'false'}" / "attempts/000-a"
    path.mkdir(parents=True)
    events = []
    if topology == "manager_specialist_as_tool":
        events = [
            {"event": "agent_tool", "outcome": "selected"},
            {"event": "agent_tool_return", "outcome": "ok"},
        ]
    receipt = {
        "run_id": f"{lane}-abc",
        "case_id": "1",
        "arm": "B0",
        "seed": 0,
        "repetition": 0,
        "cache_phase": "warm",
        "role": topology,
        "events": events,
        "retrieval_funnel": {
            "graph_tool_selected": True,
            "schema_discovery_calls": 1,
            "graph_query_calls": 1,
            "query_validated": True,
        },
        "resource_attribution": {"process_window": {"window_ms": 1}},
    }
    if topology == "single_agent" and backend.startswith("grbench"):
        (path / "run-receipt.json").write_text(json.dumps({"cases": [receipt]}), encoding="utf-8")
    else:
        (path / "receipts.jsonl").write_text(json.dumps(receipt) + "\n", encoding="utf-8")


def test_gate_requires_all_eight_query_paths(tmp_path: Path) -> None:
    for cell in MODULE.EXPECTED_CELLS:
        _write_cell(tmp_path, *cell)
    result = MODULE.evaluate_gate(tmp_path)
    assert result["passed"] is True
    assert result["observed_cell_count"] == 8


def test_gate_fails_closed_on_missing_query(tmp_path: Path) -> None:
    for cell in MODULE.EXPECTED_CELLS:
        _write_cell(tmp_path, *cell)
    target = next(tmp_path.glob("gate-gtsqa-single-tool-true/attempts/*/receipts.jsonl"))
    receipt = json.loads(target.read_text(encoding="utf-8"))
    receipt["retrieval_funnel"]["graph_query_calls"] = 0
    target.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    result = MODULE.evaluate_gate(tmp_path)
    assert result["passed"] is False
    assert any("query_attempted" in blocker for blocker in result["blockers"])


def test_b0_criterion_attempted_admits_unvalidated_queries_but_records_observation(tmp_path: Path) -> None:
    evaluate_gate = MODULE.evaluate_gate

    for backend in ("grbench_text2cypher", "gtsqa_text2sparql"):
        for topology in ("single_agent", "manager_specialist_as_tool"):
            for reasoning in ("reasoning", "non_reasoning"):
                _write_cell(tmp_path, backend, topology, reasoning)
    # flip one cell to a rejected query
    import json

    target = next(tmp_path.glob("*/attempts/*/run-receipt.json"))
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["cases"][0]["retrieval_funnel"]["query_validated"] = False
    target.write_text(json.dumps(payload), encoding="utf-8")

    strict = evaluate_gate(tmp_path, b0_criterion="validated")
    relaxed = evaluate_gate(tmp_path, b0_criterion="attempted")
    assert strict["passed"] is False and any("query_validated" in b for b in strict["blockers"])
    assert relaxed["passed"] is True and relaxed["b0_criterion"] == "attempted"
    observed = [c["checks"]["query_validated_observed"] for c in relaxed["cells"]]
    assert observed.count(False) == 1
