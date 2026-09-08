#!/usr/bin/env python3
"""Aggregate paid-run admission evidence into one fail-closed receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "agentic_os.e2e_acceptance.v1"


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"receipt must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evaluate(
    *,
    service_readiness: Path,
    vllm_preflight: Path,
    one_case_gate: Path,
    workload_receipt: Path,
    telemetry_join: Path,
    stage: str = "pre_main_matrix",
) -> dict[str, Any]:
    if stage not in {"pre_main_matrix", "final_raw_matrix"}:
        raise ValueError("unsupported acceptance stage")
    sources = {
        "service_readiness": service_readiness,
        "vllm_preflight": vllm_preflight,
        "one_case_gate": one_case_gate,
        "workload_telemetry": workload_receipt,
        "case_telemetry_join": telemetry_join,
    }
    blockers: list[str] = []
    checks: dict[str, bool] = {}
    receipts: dict[str, Mapping[str, Any]] = {}
    source_receipts: list[dict[str, Any]] = []
    for name, path in sources.items():
        if not path.is_file():
            checks[name] = False
            blockers.append(f"missing_receipt:{name}")
            continue
        try:
            payload = _load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks[name] = False
            blockers.append(f"invalid_receipt:{name}:{type(exc).__name__}")
            continue
        receipts[name] = payload
        source_receipts.append({"name": name, "path": str(path), "sha256": _sha256(path)})

    checks["service_readiness"] = receipts.get("service_readiness", {}).get("passed") is True
    checks["vllm_preflight"] = receipts.get("vllm_preflight", {}).get("passed") is True
    checks["one_case_gate"] = receipts.get("one_case_gate", {}).get("passed") is True
    workload = receipts.get("workload_telemetry", {})
    checks["workload_telemetry"] = bool(
        workload.get("workload_exit_code") == 0
        and isinstance(workload.get("telemetry_acceptance"), Mapping)
        and workload["telemetry_acceptance"].get("passed") is True
    )
    checks["case_telemetry_join"] = receipts.get("case_telemetry_join", {}).get("passed") is True
    for name, passed in checks.items():
        if not passed:
            blockers.append(f"gate_failed:{name}")
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "unknown_is_failure": True,
        "checks": checks,
        "source_receipts": sorted(source_receipts, key=lambda item: item["name"]),
        "blockers": sorted(set(blockers)),
        "passed": bool(checks) and all(checks.values()) and not blockers,
    }


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-readiness", required=True, type=Path)
    parser.add_argument("--vllm-preflight", required=True, type=Path)
    parser.add_argument("--one-case-gate", required=True, type=Path)
    parser.add_argument("--workload-receipt", required=True, type=Path)
    parser.add_argument("--telemetry-join", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument(
        "--stage",
        choices=("pre_main_matrix", "final_raw_matrix"),
        default="pre_main_matrix",
    )
    args = parser.parse_args()
    result = evaluate(
        service_readiness=args.service_readiness,
        vllm_preflight=args.vllm_preflight,
        one_case_gate=args.one_case_gate,
        workload_receipt=args.workload_receipt,
        telemetry_join=args.telemetry_join,
        stage=args.stage,
    )
    _write(args.receipt_out, result)
    print(json.dumps({"passed": result["passed"], "blockers": result["blockers"]}, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
