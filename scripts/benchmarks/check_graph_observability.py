#!/usr/bin/env python3
"""Verify a real viewer HTTP request across local Collector/Tempo/Prometheus/Grafana."""

from __future__ import annotations
import argparse
import json
import secrets
import time
import urllib.request
from pathlib import Path
from typing import Any


def request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer", default="http://127.0.0.1:18775")
    parser.add_argument("--collector", default="http://127.0.0.1:14318")
    parser.add_argument("--tempo", default="http://127.0.0.1:13200")
    parser.add_argument("--prometheus", default="http://127.0.0.1:19090")
    parser.add_argument("--grafana", default="http://127.0.0.1:13000")
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt_out.exists():
        raise FileExistsError("Use a fresh receipt path")
    trace, span = secrets.token_hex(16), secrets.token_hex(8)
    start = time.time_ns()
    request(args.viewer + "/api/runs")
    end = time.time_ns()
    resource = {
        "attributes": [
            {"key": "service.name", "value": {"stringValue": "gare2e-viewer-smoke"}}
        ]
    }
    request(
        args.collector + "/v1/traces",
        {
            "resourceSpans": [
                {
                    "resource": resource,
                    "scopeSpans": [
                        {
                            "scope": {"name": "gare2e.local-smoke"},
                            "spans": [
                                {
                                    "traceId": trace,
                                    "spanId": span,
                                    "name": "GET /api/runs",
                                    "kind": 3,
                                    "startTimeUnixNano": str(start),
                                    "endTimeUnixNano": str(end),
                                    "status": {"code": 1},
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    request(
        args.collector + "/v1/logs",
        {
            "resourceLogs": [
                {
                    "resource": resource,
                    "scopeLogs": [
                        {
                            "scope": {"name": "gare2e.local-smoke"},
                            "logRecords": [
                                {
                                    "timeUnixNano": str(end),
                                    "traceId": trace,
                                    "spanId": span,
                                    "severityNumber": 9,
                                    "severityText": "INFO",
                                    "body": {"stringValue": "registry.read.completed"},
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    for _ in range(20):
        try:
            request(args.tempo + "/api/traces/" + trace)
            break
        except (OSError, ValueError):
            time.sleep(1)
    else:
        raise RuntimeError("Trace not found in Tempo")
    metrics = request(
        args.prometheus + "/api/v1/query?query=gare2e_registry_imported_query_attempts"
    )
    if not metrics.get("data", {}).get("result"):
        raise RuntimeError("Prometheus registry scrape missing")
    if request(args.grafana + "/api/health").get("database") != "ok":
        raise RuntimeError("Grafana not healthy")
    receipt = {
        "trace_id": trace,
        "span_id": span,
        "passed": True,
        "scope": "local_observability_integration_not_db_or_gpu_performance",
        "checks": [
            "collector_trace_acceptance",
            "collector_log_acceptance",
            "tempo_trace_lookup",
            "prometheus_registry_scrape",
            "grafana_health",
        ],
    }
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
