#!/usr/bin/env python3
"""Fail closed unless every raw graph-agent cell reaches its graph query path.

The gate consumes only content-free runner/coordinator receipts.  It is not a
quality evaluation: empty evidence and abstention remain valid outcomes here.
Its single purpose is to prove, for both backends, topologies and reasoning
modes, that the endogenous tool trigger reached schema discovery and issued at
least one validated graph query before a paid full matrix is admitted.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "agentic_os.one_case_gate.v1"
EXPECTED_CELLS = {
    (backend, topology, reasoning)
    for backend in ("grbench_text2cypher", "gtsqa_text2sparql")
    for topology in ("single_agent", "manager_specialist_as_tool")
    for reasoning in ("reasoning", "non_reasoning")
}


def _iter_case_receipts(root: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
    for path in sorted(root.glob("*/attempts/*/run-receipt.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload.get("cases", ()):
            if isinstance(case, Mapping):
                yield path, case
    for path in sorted(root.glob("*/attempts/*/receipts.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, Mapping):
                    yield path, payload


def _cell(path: Path, receipt: Mapping[str, Any]) -> tuple[str, str, str]:
    run_id = str(receipt.get("run_id") or path)
    backend = "gtsqa_text2sparql" if "gtsqa" in run_id else "grbench_text2cypher"
    role = str(receipt.get("role") or receipt.get("topology") or "")
    topology = "manager_specialist_as_tool" if role == "manager_specialist_as_tool" else "single_agent"
    parts = set(path.parts)
    if any(part.endswith("-true") for part in parts):
        reasoning = "reasoning"
    elif any(part.endswith("-false") for part in parts):
        reasoning = "non_reasoning"
    else:
        raise ValueError(f"reasoning mode is not encoded in gate root: {path}")
    return backend, topology, reasoning


B0_CRITERIA = ("validated", "attempted")


def evaluate_gate(root: Path, *, b0_criterion: str = "validated") -> dict[str, Any]:
    blockers: list[str] = []
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path, receipt in _iter_case_receipts(root):
        cell = _cell(path, receipt)
        if cell in cells:
            blockers.append("duplicate_cell:" + ":".join(cell))
            continue
        funnel = receipt.get("retrieval_funnel")
        funnel = funnel if isinstance(funnel, Mapping) else {}
        events = receipt.get("events")
        events = events if isinstance(events, list) else []
        agent_tool_selected = any(
            isinstance(event, Mapping)
            and event.get("event") == "agent_tool"
            and event.get("outcome") == "selected"
            for event in events
        )
        agent_tool_returned = any(
            isinstance(event, Mapping) and event.get("event") == "agent_tool_return"
            for event in events
        )
        checks = {
            "correlation_complete": all(
                receipt.get(field) is not None
                for field in ("run_id", "case_id", "arm", "seed", "repetition", "cache_phase", "role")
            ),
            "raw_b0": receipt.get("arm") == "B0",
            "retrieval_triggered": bool(funnel.get("graph_tool_selected")),
            "schema_discovered": int(funnel.get("schema_discovery_calls") or 0) >= 1,
            "query_attempted": int(funnel.get("graph_query_calls") or 0) >= 1,
            # Raw B0 admission criterion (lab request 001 / bead seocho-q174):
            # "validated" demands a validator-accepted query in every cell,
            # which the raw model failed 5/8 in V17; "attempted" admits the
            # matrix once the endogenous trigger issued a query and reports
            # the validation-failure rate as a baseline metric instead.
            "query_validated": bool(funnel.get("query_validated")) if b0_criterion == "validated" else True,
            "query_validated_observed": bool(funnel.get("query_validated")),
            "process_window_closed": isinstance(
                (receipt.get("resource_attribution") or {}).get("process_window"), Mapping
            ),
            "agent_tool_selected": agent_tool_selected if cell[1] == "manager_specialist_as_tool" else True,
            "agent_tool_returned": agent_tool_returned if cell[1] == "manager_specialist_as_tool" else True,
        }
        failed = sorted(name for name, passed in checks.items() if not passed and name != "query_validated_observed")
        if failed:
            blockers.append("cell_failed:" + ":".join(cell) + ":" + ",".join(failed))
        cells[cell] = {
            "backend": cell[0],
            "topology": cell[1],
            "reasoning_mode": cell[2],
            "case_id": str(receipt.get("case_id")),
            "checks": checks,
            "terminal": receipt.get("terminal", receipt.get("terminal_status")),
            "answer_status": receipt.get("answer_status"),
            "source_receipt": str(path.relative_to(root)),
        }
    missing = sorted(EXPECTED_CELLS - set(cells))
    extra = sorted(set(cells) - EXPECTED_CELLS)
    blockers.extend("missing_cell:" + ":".join(cell) for cell in missing)
    blockers.extend("unexpected_cell:" + ":".join(cell) for cell in extra)
    return {
        "schema_version": SCHEMA_VERSION,
        "gate_scope": "raw_b0_endogenous_tool_trigger",
        "b0_criterion": b0_criterion,
        "quality_claim": False,
        "expected_cell_count": len(EXPECTED_CELLS),
        "observed_cell_count": len(cells),
        "cells": [cells[key] for key in sorted(cells)],
        "blockers": sorted(set(blockers)),
        "passed": not blockers and set(cells) == EXPECTED_CELLS,
    }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--b0-criterion", choices=B0_CRITERIA, default="validated")
    args = parser.parse_args()
    payload = evaluate_gate(args.run_root, b0_criterion=args.b0_criterion)
    _atomic_write(args.receipt_out, payload)
    print(json.dumps({"passed": payload["passed"], "blockers": payload["blockers"]}, sort_keys=True))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
