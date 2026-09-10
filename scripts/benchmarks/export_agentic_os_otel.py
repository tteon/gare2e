#!/usr/bin/env python3
"""Export AgenticOS attempt receipts as OpenTelemetry span trees.

Reads coordinator roots (``<run-root>/e2e/<lane>`` or ``.../shard-N``), builds one
trace per case (see ``seocho.eval.agentic_os_otel``), and writes them as
OTel-shaped JSON lines and/or pushes them to an OTLP/gRPC endpoint.  Only
timings, counts, outcome codes and digests are exported; never prompts,
queries, evidence or answers.

    python3 scripts/benchmarks/export_agentic_os_otel.py <run-root>/e2e/*-tool-* \\
        --jsonl <run-root>/otel/spans.jsonl [--otlp-endpoint http://collector:4317]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.agentic_os_otel import build_root_spans, export_jsonl, export_otlp


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path, help="coordinator roots (lane dirs or shard-N dirs)")
    ap.add_argument("--jsonl", type=Path, help="write spans as JSON lines here")
    ap.add_argument("--otlp-endpoint", help="OTLP/gRPC endpoint, e.g. http://127.0.0.1:4317 (needs seocho[otel])")
    ap.add_argument("--service-name", default="seocho-agentic-os")
    ap.add_argument("--model", help="gen_ai.request.model attribute (defaults to the run manifest model when present)")
    args = ap.parse_args()
    spans = []
    roots = []
    for r in args.roots:
        if (r / "attempts").is_dir():
            roots.append(r)
        else:
            roots += sorted(p for p in r.glob("shard-*") if (p / "attempts").is_dir())
    for root in roots:
        model = args.model
        manifest = root / "coordinator-manifest.json"
        if model is None and manifest.exists():
            try:
                model = json.loads(manifest.read_text(encoding="utf-8")).get("model")
            except json.JSONDecodeError:
                model = None
        spans += build_root_spans(root, model=model)
    out = {"roots": len(roots), "spans": len(spans), "traces": len({s.trace_id for s in spans})}
    if args.jsonl:
        out["jsonl"] = str(args.jsonl)
        export_jsonl(spans, args.jsonl)
    if args.otlp_endpoint:
        out["otlp_exported"] = export_otlp(spans, endpoint=args.otlp_endpoint, service_name=args.service_name)
        out["otlp_endpoint"] = args.otlp_endpoint
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
