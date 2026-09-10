#!/usr/bin/env python3
"""Per-request stage timings for the vLLM engineer, from the OTel span files.

Joins the client-side ``chat <model>`` spans (receipt exporter) with the
server-side spans vLLM exported for the same requests (``--otlp-traces-endpoint``
+ ``--collect-detailed-traces``), matched through W3C trace context: the vLLM
span's ``parent_span_id`` is the client span id the runner put in
``traceparent``.  One row per LLM request::

    lane, case, request ordinal, client latency, gap before request (tool/DB
    work between two LLM calls), server e2e, time in queue, TTFT, prefill,
    decode, model forward/execute (when detailed traces are on), tokens
    in/out/cached, batch/scheduler counters vLLM attaches, and the client vs
    server difference (network + HTTP + serialisation).

Then per lane: medians / p90 of every stage and the share of the case wall
each stage takes — the table that says "the time goes to X".  All inputs are
content-free span files; nothing here reads prompts or answers.

    python3 scripts/benchmarks/summarize_agentic_os_otel_stages.py \\
        --client <run-root>/run-<pass>/otel/spans.jsonl \\
        --server <run-root>/otel/collector-spans.jsonl \\
        --output-dir <run-root>/run-<pass>/otel
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from collections import defaultdict
from pathlib import Path
from typing import Any

# vLLM attribute names have moved across versions; accept all spellings.
STAGE_KEYS: dict[str, tuple[str, ...]] = {
    "server_e2e_s": ("gen_ai.latency.e2e", "gen_ai.latency.e2e_s", "vllm.latency.e2e"),
    "queue_s": ("gen_ai.latency.time_in_queue", "gen_ai.latency.queue", "vllm.latency.time_in_queue"),
    "ttft_s": ("gen_ai.latency.time_to_first_token", "gen_ai.server.time_to_first_token", "vllm.latency.time_to_first_token"),
    "prefill_s": ("gen_ai.latency.time_in_model_prefill", "vllm.latency.time_in_model_prefill"),
    "decode_s": ("gen_ai.latency.time_in_model_decode", "vllm.latency.time_in_model_decode"),
    "scheduler_s": ("gen_ai.latency.time_in_scheduler", "vllm.latency.time_in_scheduler"),
    "model_forward_s": ("gen_ai.latency.time_in_model_forward", "vllm.latency.time_in_model_forward"),
    "model_execute_s": ("gen_ai.latency.time_in_model_execute", "vllm.latency.time_in_model_execute"),
    "model_inference_s": ("gen_ai.latency.time_in_model_inference", "vllm.latency.time_in_model_inference"),
}
COUNT_KEYS: dict[str, tuple[str, ...]] = {
    "server_prompt_tokens": ("gen_ai.usage.prompt_tokens", "gen_ai.usage.input_tokens"),
    "server_completion_tokens": ("gen_ai.usage.completion_tokens", "gen_ai.usage.output_tokens"),
    "server_num_sequences": ("gen_ai.usage.num_sequences", "vllm.request.n"),
    "server_max_tokens": ("gen_ai.request.max_tokens",),
    "server_finish_reason": ("gen_ai.response.finish_reasons", "gen_ai.response.finish_reason"),
}


def _first(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return None


def _load(path: Path) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _pct(vals: list[float], q: float) -> float | str:
    if not vals:
        return ""
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(q * (len(vals) - 1)))], 3)


def build_rows(client_spans: list[dict[str, Any]], server_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in server_spans:
        if s.get("service_name") not in (None, "seocho-agentic-os") and s.get("parent_span_id"):
            by_parent[s["parent_span_id"]].append(s)
    cases: dict[str, dict[str, Any]] = {}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in client_spans:
        if s["name"] == "agentic_os.case":
            cases[s["span_id"]] = s
        elif s.get("parent_span_id"):
            children[s["parent_span_id"]].append(s)
    rows: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        kids = sorted(children.get(case_id, []), key=lambda x: x["start_unix_ns"])
        chats = [k for k in kids if k["name"].startswith("chat ")]
        prev_end = case["start_unix_ns"]
        for i, c in enumerate(chats):
            a = c["attributes"]
            srv = by_parent.get(c["span_id"], [])
            srv_attrs = srv[0]["attributes"] if srv else {}
            client_s = (c["end_unix_ns"] - c["start_unix_ns"]) / 1e9
            server_e2e = _first(srv_attrs, STAGE_KEYS["server_e2e_s"])
            if server_e2e is None and srv:
                server_e2e = (srv[0]["end_unix_ns"] - srv[0]["start_unix_ns"]) / 1e9
            row: dict[str, Any] = {
                "lane": case["attributes"].get("seocho.lane"),
                "case_id": case["attributes"].get("seocho.case_id"),
                "trace_id": c["trace_id"],
                "request_ordinal": i + 1,
                "client_span_id": c["span_id"],
                "server_span_present": bool(srv),
                "gap_before_s": round(max(c["start_unix_ns"] - prev_end, 0) / 1e9, 3),
                "client_latency_s": round(client_s, 3),
                "client_input_tokens": a.get("gen_ai.usage.input_tokens"),
                "client_output_tokens": a.get("gen_ai.usage.output_tokens"),
                "client_cached_tokens": a.get("seocho.usage.cached_tokens"),
                "client_output_kind": a.get("seocho.output_kind"),
                "client_timing": a.get("seocho.timing"),
            }
            for name, keys in STAGE_KEYS.items():
                v = _first(srv_attrs, keys)
                row[name] = round(float(v), 4) if isinstance(v, (int, float)) else (v if name == "server_e2e_s" and v is None and server_e2e is None else (round(float(server_e2e), 4) if name == "server_e2e_s" and server_e2e is not None else ""))
            for name, keys in COUNT_KEYS.items():
                row[name] = _first(srv_attrs, keys)
            if isinstance(row.get("server_e2e_s"), float):
                row["client_minus_server_s"] = round(client_s - row["server_e2e_s"], 4)
                if isinstance(row.get("prefill_s"), float) and isinstance(row.get("decode_s"), float):
                    row["server_unaccounted_s"] = round(row["server_e2e_s"] - row["prefill_s"] - row["decode_s"] - (row["queue_s"] if isinstance(row.get("queue_s"), float) else 0.0), 4)
            rows.append(row)
            prev_end = c["end_unix_ns"]
    return rows


def lane_summary(rows: list[dict[str, Any]], client_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_wall: dict[str, float] = defaultdict(float)
    for s in client_spans:
        if s["name"] == "agentic_os.case":
            case_wall[s["attributes"].get("seocho.lane")] += (s["end_unix_ns"] - s["start_unix_ns"]) / 1e9
    out = []
    for lane in sorted({r["lane"] for r in rows}):
        lr = [r for r in rows if r["lane"] == lane]
        def col(k: str) -> list[float]:
            return [float(r[k]) for r in lr if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
        wall = case_wall.get(lane) or 1.0
        summary: dict[str, Any] = {"lane": lane, "requests": len(lr), "server_spans_matched": sum(r["server_span_present"] for r in lr)}
        for k in ("gap_before_s", "client_latency_s", "server_e2e_s", "queue_s", "ttft_s", "prefill_s", "decode_s", "scheduler_s", "model_forward_s", "model_execute_s", "client_minus_server_s"):
            v = col(k)
            summary[f"{k}_p50"] = _pct(v, 0.5)
            summary[f"{k}_p90"] = _pct(v, 0.9)
            summary[f"{k}_share_of_wall"] = round(sum(v) / wall, 3) if v else ""
        summary["client_input_tokens_mean"] = round(st.mean(col("client_input_tokens")), 1) if col("client_input_tokens") else ""
        summary["client_output_tokens_mean"] = round(st.mean(col("client_output_tokens")), 1) if col("client_output_tokens") else ""
        summary["client_cached_share"] = round(sum(col("client_cached_tokens")) / sum(col("client_input_tokens")), 3) if col("client_input_tokens") and col("client_cached_tokens") else ""
        out.append(summary)
    return out


def _write(rows: list[dict[str, Any]], out: Path, name: str) -> None:
    if not rows:
        return
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("lane", "case_id", "request_ordinal", "requests"), k))
    with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    md = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)] + ["| " + " | ".join("" if r.get(f) is None else str(r.get(f)) for f in fields) + " |" for r in rows]
    (out / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", type=Path, required=True, help="receipt-exporter spans (otel/spans.jsonl)")
    ap.add_argument("--server", type=Path, help="collector file with vLLM spans (otel/collector-spans.jsonl); optional")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    client = _load(args.client)
    server = _load(args.server) if args.server else []
    rows = build_rows(client, server)
    summary = lane_summary(rows, client)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(rows, args.output_dir, "request-stages")
    _write(summary, args.output_dir, "lane-stages")
    vllm_attr_names = sorted({k for s in server for k in s.get("attributes", {}) if s.get("service_name") != "seocho-agentic-os"})
    print(json.dumps({
        "client_spans": len(client), "server_spans": len(server), "requests": len(rows),
        "requests_with_server_span": sum(r["server_span_present"] for r in rows),
        "vllm_attribute_names": vllm_attr_names[:40], "output_dir": str(args.output_dir),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
