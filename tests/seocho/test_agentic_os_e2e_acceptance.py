from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/benchmarks/validate_agentic_os_e2e_acceptance.py"
SPEC = importlib.util.spec_from_file_location("validate_agentic_os_e2e_acceptance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_acceptance_requires_every_live_gate(tmp_path: Path) -> None:
    service = _write(tmp_path / "service.json", {"passed": True})
    vllm = _write(tmp_path / "vllm.json", {"passed": True})
    one = _write(tmp_path / "one.json", {"passed": True})
    workload = _write(
        tmp_path / "workload.json",
        {"workload_exit_code": 0, "telemetry_acceptance": {"passed": True}},
    )
    join = _write(tmp_path / "join.json", {"passed": True})

    assert MODULE.evaluate(
        service_readiness=service,
        vllm_preflight=vllm,
        one_case_gate=one,
        workload_receipt=workload,
        telemetry_join=join,
    )["passed"] is True

    join.write_text(json.dumps({"passed": False}), encoding="utf-8")
    failed = MODULE.evaluate(
        service_readiness=service,
        vllm_preflight=vllm,
        one_case_gate=one,
        workload_receipt=workload,
        telemetry_join=join,
    )
    assert failed["passed"] is False
    assert "gate_failed:case_telemetry_join" in failed["blockers"]


def test_acceptance_treats_missing_as_failure(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    result = MODULE.evaluate(
        service_readiness=missing,
        vllm_preflight=missing,
        one_case_gate=missing,
        workload_receipt=missing,
        telemetry_join=missing,
    )
    assert result["passed"] is False
    assert any(item.startswith("missing_receipt:") for item in result["blockers"])
