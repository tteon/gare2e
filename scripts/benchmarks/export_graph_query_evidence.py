#!/usr/bin/env python3
"""Adapt existing direct/hop receipts to the existing experiment registry format."""

from __future__ import annotations
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from seocho.eval.agentic_os_otel import build_case_spans
from seocho.eval.experiment_evidence import query_record


def export(
    root: Path, output: Path, server_spans: Path | None = None
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("Use a fresh export directory")
    passes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    queries = []
    hashes = {}
    seen_traces = set()
    servers = []
    if server_spans:
        servers = [
            json.loads(line)
            for line in server_spans.read_text().splitlines()
            if line.strip()
        ]
        hashes["server_spans"] = hashlib.sha256(server_spans.read_bytes()).hexdigest()
    for path in sorted(root.rglob("run-receipt.json")):
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
            continue
        condition = next(
            (
                part
                for part in path.relative_to(root).parts
                if part.startswith("run-") and part != "run-receipt.json"
            ),
            path.parent.name,
        )
        hashes[str(path.relative_to(root))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for case in data["cases"]:
            # The canonical span builder retains its measured/fallback clock classification.
            spans = build_case_spans(
                case, lane="grbench-single", topology="single_agent", anchor_unix_s=0
            )
            anchor = spans[0]
            if anchor.trace_id in seen_traces:
                continue
            seen_traces.add(anchor.trace_id)
            start = anchor.start_unix_ns / 1e9
            turns = case.get("turns") or []
            task = {
                "task_id": anchor.trace_id,
                "trace_id": anchor.trace_id,
                "case_id": str(case.get("case_id") or case.get("qid")),
                "started_unix_s": start,
                "wall_ms": (anchor.end_unix_ns - anchor.start_unix_ns) / 1e6,
                "clock_source": anchor.attributes.get("seocho.timing.anchor")
                if start
                else "unknown_relative_only",
                "terminal": case.get("termination_reason")
                or case.get("terminal_status")
                or case.get("terminal"),
                "fully_contained": False,
                "input_tokens": sum(t.get("input_tokens") or 0 for t in turns)
                if turns and all(t.get("input_tokens") is not None for t in turns)
                else None,
                "output_tokens": sum(t.get("output_tokens") or 0 for t in turns)
                if turns and all(t.get("output_tokens") is not None for t in turns)
                else None,
            }
            requests = []
            for turn in turns:
                elapsed = turn.get("started_elapsed_ms")
                latency = turn.get("latency_ms")
                requests.append(
                    {
                        "trace_id": anchor.trace_id,
                        "client_span_id": turn.get("llm_request_id")
                        or turn.get("span_id"),
                        "request_started_unix_s": start + elapsed / 1000
                        if elapsed is not None
                        else None,
                        "client_latency_s": latency / 1000
                        if latency is not None
                        else None,
                        "client_input_tokens": turn.get("input_tokens"),
                        "client_output_tokens": turn.get("output_tokens"),
                        "server_span_present": False,
                    }
                )
            tools = [
                {**t, "trace_id": anchor.trace_id}
                for t in case.get("tools", case.get("tool_events", []))
            ]
            if server_spans:
                from scripts.benchmarks.summarize_agentic_os_otel_stages import (
                    build_rows,
                )

                requests = build_rows([span.to_json() for span in spans], servers)
                for request in requests:
                    matches = [
                        s
                        for s in servers
                        if s.get("trace_id") == request["trace_id"]
                        and s.get("parent_span_id") == request["client_span_id"]
                        and s.get("name") == "llm_request"
                        and s.get("service_name") not in (None, "seocho-agentic-os")
                    ]
                    if len(matches) == 1 and request["server_span_present"]:
                        request.update(
                            server_started_unix_s=matches[0]["start_unix_ns"] / 1e9,
                            server_finished_unix_s=matches[0]["end_unix_ns"] / 1e9,
                        )
            passes[condition].append(
                {"task": task, "requests": requests, "tools": tools}
            )
            for index, replay in enumerate(case.get("query_plan_replays") or []):
                queries.append(
                    {
                        "schema_version": "gare2e.query_attempt.v1",
                        "trace_id": anchor.trace_id,
                        "query_id": hashlib.sha256(
                            f"{anchor.trace_id}:profile:{index}".encode()
                        ).hexdigest()[:32],
                        "scope": "diagnostic_replay",
                        "mode": "historical",
                        "outcome": "error" if replay.get("error_class") else "ok",
                        "error_class": replay.get("error_class"),
                        "query_digest": replay.get("query_digest"),
                        "latency_ms": replay.get("wall_ms"),
                        "receipt": {
                            k: replay.get(k)
                            for k in ("server_available_ms", "server_consumed_ms")
                        },
                        "plan": replay.get("plan"),
                    }
                )
    for path in sorted(root.rglob("*.queries.jsonl")):
        hashes[str(path.relative_to(root))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for line in path.read_text().splitlines():
            raw = json.loads(line)
            if raw.get("trace_id") in seen_traces:
                queries.append(raw)
    captured_replays = {
        q.get("trace_id")
        for q in queries
        if q.get("scope") == "diagnostic_replay" and q.get("mode") != "historical"
    }
    queries = [
        q
        for q in queries
        if q.get("mode") != "historical" or q.get("trace_id") not in captured_replays
    ]
    data = {
        "passes": [
            {
                "pass": name,
                "all_pass_tasks": len(examples),
                "examples": examples,
                "requests": sum(len(e["requests"]) for e in examples),
                "matched_requests": sum(
                    bool(r.get("server_span_present"))
                    for e in examples
                    for r in e["requests"]
                )
                if server_spans
                else None,
                "agent": {
                    "wall_ms": {
                        "mean": sum(e["task"]["wall_ms"] for e in examples)
                        / len(examples)
                    }
                },
            }
            for name, examples in passes.items()
        ]
    }
    serialized_queries = "".join(
        json.dumps(query_record(q), ensure_ascii=False) + "\n" for q in queries
    )
    output.mkdir(parents=True)
    (output / "characterization.json").write_text(
        json.dumps(data, ensure_ascii=False) + "\n"
    )
    (output / "queries.jsonl").write_text(serialized_queries)
    manifest = {
        "schema_version": "gare2e.graph_evidence_export.v1",
        "source_sha256": hashes,
        "conditions": len(passes),
        "tasks": len(seen_traces),
        "queries": len(queries),
        "scope": "receipt_reanalysis",
        "limitations": [
            "No throughput window reconstructed.",
            "Server joins use exact recorded trace/parent IDs only."
            if server_spans
            else "Server joins require separate original OTel evidence.",
            "Quality unscored.",
        ],
    }
    (output / "evidence-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--server-spans",
        type=Path,
        help="Original collector-spans.jsonl for exact request joins",
    )
    args = parser.parse_args()
    result = export(args.run_root, args.output_dir, args.server_spans)
    print(json.dumps({k: result[k] for k in ("conditions", "tasks", "queries")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
