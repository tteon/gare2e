#!/usr/bin/env python3
"""Fail-closed live service gates before co-located graph-agent cases."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.request import urlopen

from seocho.eval.agentic_os_admission import (
    probe_dozerdb,
    probe_gtsqa_oxigraph,
    validate_grbench_import_receipt,
)
from seocho.eval.agentic_os_observability import (
    REQUIRED_VLLM_METRIC_NAMES,
    VllmMetricsClient,
)


def _probe_vllm(base_url: str, model: str) -> dict[str, Any]:
    started = time.perf_counter()
    blockers: list[str] = []
    try:
        with urlopen(f"{base_url.rstrip('/')}/v1/models", timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        model_ids = sorted(
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        )
    except Exception as exc:  # live gate preserves unavailability as evidence
        return {
            "passed": False,
            "error_class": type(exc).__name__,
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    if model not in model_ids:
        blockers.append("requested_model_not_served")
    snapshot = VllmMetricsClient(f"{base_url.rstrip('/')}/metrics", timeout_s=5).snapshot()
    names = set(snapshot.metrics)
    missing = sorted(REQUIRED_VLLM_METRIC_NAMES - names)
    if not snapshot.available:
        blockers.append("metrics_unavailable")
    if missing:
        blockers.append("required_metric_families_missing")
    cache_config = snapshot.metadata.get("vllm:cache_config_info") or {}
    if str(cache_config.get("enable_prefix_caching", "")).lower() != "true":
        blockers.append("prefix_caching_not_enabled")
    return {
        "passed": not blockers,
        "served_models": model_ids,
        "required_metric_names": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "missing_metric_names": missing,
        "cache_config_info": cache_config,
        "blockers": blockers,
        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--grbench-import-receipt", required=True, type=Path)
    parser.add_argument("--gtsqa-parquet", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args()

    import_gate = validate_grbench_import_receipt(args.grbench_import_receipt)
    workspace_id = str(import_gate.detail.get("workspace_id", ""))
    dozer = probe_dozerdb(
        uri="bolt://127.0.0.1:7687",
        username="neo4j",
        password=os.environ.get("NEO4J_PASSWORD", "").strip(),
        database="neo4j",
        workspace_id=workspace_id,
    )
    oxigraph = probe_gtsqa_oxigraph(args.gtsqa_parquet)
    gates = {
        "vllm": _probe_vllm(args.base_url, args.model),
        "grbench_import": import_gate.to_dict(),
        "dozerdb": dozer.to_dict(),
        "oxigraph": oxigraph.to_dict(),
    }
    passed = bool(
        gates["vllm"].get("passed")
        and gates["grbench_import"].get("status") == "pass"
        and gates["dozerdb"].get("status") == "pass"
        and gates["oxigraph"].get("status") == "pass"
    )
    receipt = {
        "schema_version": "agentic_os.service_readiness.v1",
        "created_unix_s": time.time(),
        "passed": passed,
        "gates": gates,
    }
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
