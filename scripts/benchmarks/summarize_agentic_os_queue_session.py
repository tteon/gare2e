#!/usr/bin/env python3
"""Summarise a queue-mode lab session (run_lab_queue.sh) across its passes.

Three content-free tables, written as CSV + Markdown under ``<run>/session-summary``:

* ``pass-serving.csv`` — per pass: queue wall, vLLM counter deltas (requests,
  tokens, prefix-hit ratio, mean TTFT/ITL, prefill/decode/queue per request),
  GPU utilisation and integrated energy.  Counter deltas are pass-level, so
  they stay valid at any concurrency (per-attempt vLLM windows do not).
* ``lane-client.csv`` — per pass × lane: client-observed decode speed
  (output tokens / request latency for requests with >= 64 output tokens),
  request-latency median/p90, output tokens, LLM and wall seconds per case,
  provider-reported cached-token share.  Single-agent receipts carry turns;
  multi-agent receipts carry ``llm_requests`` + manager/specialist usage.
* ``lane-bottleneck.csv`` — per pass × lane (× stratum when ``--schedule`` files
  are given): where the wall goes — LLM time, graph-database tool time,
  agent-framework residual (wall − llm − tool) — plus the Graph-CoT shape:
  turns, LLM turns before the first graph tool, time to first graph tool,
  query retries / repairs, graph-DB call latency p50/p90, and reasoning vs
  tool-call output tokens for single-agent lanes.
* ``lane-funnel.csv`` — per pass × lane: retrieval funnel from the catalog
  ``groups.csv`` (retrieval triggered, validated, executed, non-empty, tool
  failure rate, turns, wall p50) so serving changes can be checked for
  quality parity before their latency is compared.

Reads only receipts, samples and catalogs; never prompts, queries or answers.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from pathlib import Path
from typing import Any

LANES = ("grbench-single", "gtsqa-single", "grbench-multi", "gtsqa-multi")


def _delta(a: dict[str, Any], b: dict[str, Any], key: str) -> float:
    return float(b.get(key, 0) or 0) - float(a.get(key, 0) or 0)


def pass_serving(run: Path, pass_id: str, wall_s: float | None) -> dict[str, Any]:
    w = run / f"run-{pass_id}" / "workload"
    samples = [json.loads(l) for l in (w / "vllm-metrics.jsonl").read_text().splitlines() if l.strip()]
    samples = [d for d in samples if d.get("available") and d.get("metrics")]
    a, b = samples[0]["metrics"], samples[-1]["metrics"]
    reqs = _delta(a, b, "vllm:e2e_request_latency_seconds_count")
    gen = _delta(a, b, "vllm:generation_tokens")
    span = samples[-1]["timestamp_monotonic_s"] - samples[0]["timestamp_monotonic_s"]
    q = _delta(a, b, "vllm:prefix_cache_queries")
    gpu = list(csv.DictReader((w / "gpu.csv").open(encoding="utf-8")))
    per: dict[str, list[tuple[float, float]]] = {}
    for r in gpu:
        per.setdefault(r["gpu_index"], []).append((float(r["timestamp_monotonic_s"]), float(r["power_w"])))
    energy = 0.0
    for s in per.values():
        s.sort()
        energy += sum((s[i + 1][0] - s[i][0]) * s[i][1] for i in range(len(s) - 1))
    return {
        "pass": pass_id,
        "pass_wall_s": round(wall_s, 1) if wall_s is not None else "",
        "vllm_requests": int(reqs),
        "generation_tokens": int(gen),
        "prompt_tokens": int(_delta(a, b, "vllm:prompt_tokens")),
        "generation_tok_per_s": round(gen / span, 1) if span else "",
        "prefix_hit_ratio": round(_delta(a, b, "vllm:prefix_cache_hits") / q, 3) if q else "",
        "ttft_s_mean": round(_delta(a, b, "vllm:time_to_first_token_seconds_sum") / max(_delta(a, b, "vllm:time_to_first_token_seconds_count"), 1), 3),
        "itl_ms_mean": round(1000 * _delta(a, b, "vllm:inter_token_latency_seconds_sum") / max(_delta(a, b, "vllm:inter_token_latency_seconds_count"), 1), 1),
        "prefill_s_per_req": round(_delta(a, b, "vllm:request_prefill_time_seconds_sum") / max(reqs, 1), 3),
        "decode_s_per_req": round(_delta(a, b, "vllm:request_decode_time_seconds_sum") / max(reqs, 1), 3),
        "queue_s_per_req": round(_delta(a, b, "vllm:request_queue_time_seconds_sum") / max(reqs, 1), 3),
        "kv_usage_max_pct": round(100 * max(float(d["metrics"].get("vllm:kv_cache_usage_perc", 0) or 0) for d in samples), 1),
        "gpu_util_mean_pct": round(st.mean(float(r["gpu_utilization_pct"]) for r in gpu), 1),
        "gpu_energy_kj": round(energy / 1000, 1),
        "gpu_sample_count": len(gpu),
    }


def _receipts(run: Path, pass_id: str, lane: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = run / f"run-{pass_id}" / "e2e"
    for f in base.glob(f"{lane}-tool-*/**/run-receipt.json"):
        out += json.loads(f.read_text()).get("cases", [])
    for f in base.glob(f"{lane}-tool-*/**/receipts.jsonl"):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


def lane_client(run: Path, pass_id: str, lane: str) -> dict[str, Any] | None:
    cases = _receipts(run, pass_id, lane)
    if not cases:
        return None
    speeds: list[float] = []
    lats: list[float] = []
    out: list[float] = []
    llm: list[float] = []
    wall: list[float] = []
    cached = inp = 0
    for c in cases:
        t = c.get("time", {})
        wall.append(float(t.get("wall_ms", c.get("wall_ms", 0))) / 1000)
        llm.append(float(t.get("llm_ms", 0)) / 1000)
        if "turns" in c:
            ts = [x for x in c["turns"] if isinstance(x.get("latency_ms"), (int, float))]
            lats += [x["latency_ms"] / 1000 for x in ts]
            speeds += [x["output_tokens"] / (x["latency_ms"] / 1000) for x in ts if (x.get("output_tokens") or 0) >= 64 and x["latency_ms"] > 0]
            out.append(sum(x.get("output_tokens") or 0 for x in ts))
            cached += sum(x.get("cached_tokens") or 0 for x in ts)
            inp += sum(x.get("input_tokens") or 0 for x in ts)
        else:
            reqs = c.get("llm_requests", []) or []
            lats += [r["latency_ms"] / 1000 for r in reqs if isinstance(r.get("latency_ms"), (int, float))]
            o = sum(int((c.get(u) or {}).get("output_tokens", 0)) for u in ("manager_usage", "specialist_usage"))
            out.append(o)
            tot = sum(r["latency_ms"] for r in reqs) / 1000
            if tot > 0 and o >= 64:
                speeds.append(o / tot)
            for u in ("manager_usage", "specialist_usage"):
                cached += int((c.get(u) or {}).get("cached_tokens", 0))
                inp += int((c.get(u) or {}).get("input_tokens", 0))
    lats.sort()
    return {
        "pass": pass_id,
        "lane": lane,
        "cases": len(cases),
        "llm_requests": len(lats),
        "decode_tok_per_s_median": round(st.median(speeds), 1) if speeds else "",
        "request_latency_s_p50": round(st.median(lats), 2) if lats else "",
        "request_latency_s_p90": round(lats[int(0.9 * (len(lats) - 1))], 2) if lats else "",
        "output_tokens_per_case_median": round(st.median(out)) if out else "",
        "llm_s_per_case_median": round(st.median(llm), 1),
        "wall_s_per_case_median": round(st.median(wall), 1),
        "cached_token_share": round(cached / inp, 3) if inp else "",
    }


def _pct(vals: list[float], q: float) -> float | str:
    if not vals:
        return ""
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(q * (len(vals) - 1)))], 3)


def _graph_tool_latencies(c: dict[str, Any]) -> list[float]:
    """Graph-database call latencies (ms) from any receipt shape; schema inspection excluded."""
    out: list[float] = []
    tools = c.get("tools")
    if isinstance(tools, list):  # GRBench single: [{name, latency_ms, outcome}]
        out += [float(t["latency_ms"]) for t in tools if isinstance(t.get("latency_ms"), (int, float)) and "schema" not in str(t.get("name", ""))]
    elif isinstance(tools, dict):  # GTSQA single: {events: [...]}
        out += [float(t["latency_ms"]) for t in tools.get("events", []) if isinstance(t.get("latency_ms"), (int, float)) and "schema" not in str(t.get("name", ""))]
    for ev in c.get("events", []) or []:  # multi-agent: tool events on the specialist
        if ev.get("event") == "tool" and "schema" not in str(ev.get("name", "")) and isinstance(ev.get("latency_ms"), (int, float)):
            out.append(float(ev["latency_ms"]))
    return out


def lane_bottleneck(run: Path, pass_id: str, lane: str, strata: dict[str, str] | None) -> list[dict[str, Any]]:
    cases = _receipts(run, pass_id, lane)
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        key = (strata or {}).get(str(c.get("qid") or c.get("case_id")), "all") if strata else "all"
        groups.setdefault(key, []).append(c)
    rows: list[dict[str, Any]] = []
    for stratum, cs in sorted(groups.items()):
        wall = [float(c.get("time", {}).get("wall_ms", c.get("wall_ms", 0))) / 1000 for c in cs]
        llm = [float(c.get("time", {}).get("llm_ms", 0)) / 1000 for c in cs]
        tool = [float(c.get("time", {}).get("tool_ms", 0)) / 1000 for c in cs]
        gdb = [sum(_graph_tool_latencies(c)) / 1000 for c in cs]           # graph-DB calls only
        resid = [max(w - l - t, 0.0) for w, l, t in zip(wall, llm, tool)]
        rf = [c.get("retrieval_funnel") or {} for c in cs]
        turns = [float(c.get("turn_count") or 0) for c in cs]
        before = [float(r.get("llm_turns_before_first_graph_tool")) for r in rf if isinstance(r.get("llm_turns_before_first_graph_tool"), (int, float))]
        ttfg = [float(r.get("time_to_first_graph_tool_ms")) / 1000 for r in rf if isinstance(r.get("time_to_first_graph_tool_ms"), (int, float))]
        retries = [float(c.get("query_retry_count") or 0) + float(c.get("repair_count") or 0) for c in cs]
        gq = [float(r.get("graph_query_calls") or 0) for r in rf]
        lat = [x for c in cs for x in _graph_tool_latencies(c)]
        think_tok = tool_tok = 0
        for c in cs:
            for t in c.get("turns", []) or []:
                if t.get("output_kind") == "tool_calls":
                    tool_tok += int(t.get("output_tokens") or 0)
                else:
                    think_tok += int(t.get("output_tokens") or 0)
        tw = sum(wall) or 1.0
        rows.append({
            "pass": pass_id, "lane": lane, "stratum": stratum, "cases": len(cs),
            "wall_s_median": round(st.median(wall), 1),
            "llm_share": round(sum(llm) / tw, 3),
            "graph_db_share": round(sum(gdb) / tw, 3),
            "tool_other_share": round(max(sum(tool) - sum(gdb), 0) / tw, 3),
            "framework_residual_share": round(sum(resid) / tw, 3),
            "graph_db_s_p50": _pct(gdb, 0.5), "graph_db_s_p90": _pct(gdb, 0.9),
            "graph_call_ms_p50": _pct(lat, 0.5), "graph_call_ms_p90": _pct(lat, 0.9),
            "turns_median": round(st.median(turns), 1) if turns else "",
            "llm_turns_before_first_graph_tool_median": round(st.median(before), 1) if before else "",
            "time_to_first_graph_tool_s_median": round(st.median(ttfg), 2) if ttfg else "",
            "query_retries_plus_repairs_mean": round(sum(retries) / len(cs), 2),
            "graph_query_calls_mean": round(sum(gq) / len(cs), 2),
            "reasoning_output_tokens": think_tok if any("turns" in c for c in cs) else "",
            "tool_call_output_tokens": tool_tok if any("turns" in c for c in cs) else "",
        })
    return rows


def load_strata(paths: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        for e in json.loads(p.read_text()).get("entries", []):
            if e.get("qid") is not None and e.get("stratum"):
                out[str(e["qid"])] = e["stratum"]
    return out


FUNNEL = ("retrieval_triggered", "query_validated", "query_executed", "nonempty_result", "tool_failure_rate", "turn_count")


def lane_funnel(run: Path, pass_id: str) -> list[dict[str, Any]]:
    groups = run / f"run-{pass_id}" / "catalog" / "groups.csv"
    rows: list[dict[str, Any]] = []
    if not groups.exists():
        return rows
    for r in csv.DictReader(groups.open(encoding="utf-8")):
        lane = ("grbench" if "GRBench" in r["lane"] else "gtsqa") + ("-multi" if "manager" in r["topology"] else "-single")
        row: dict[str, Any] = {"pass": pass_id, "lane": lane, "reasoning": r["reasoning_mode"], "condition": r["condition"], "attempts": r["attempts_total"]}
        for c in FUNNEL:
            row[c] = r.get(f"{c}_mean", "")
        row["wall_ms_p50"] = r.get("wall_ms_p50", "")
        rows.append(row)
    return rows


def _write(rows: list[dict[str, Any]], out: Path, name: str) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    md = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    md += ["| " + " | ".join(str(r[f]) for f in fields) + " |" for r in rows]
    (out / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path, help="lifecycle run root holding queue-receipt.jsonl and run-<pass>/ dirs")
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--schedule", action="append", type=Path, default=[], help="frozen schedule JSON(s) giving the stratum per qid (repeatable)")
    args = ap.parse_args()
    run: Path = args.run
    out = args.output_dir or run / "session-summary"
    out.mkdir(parents=True, exist_ok=True)
    queue = [json.loads(l) for l in (run / "queue-receipt.jsonl").read_text().splitlines() if l.strip()]
    passes = [(q["pass_id"], q.get("wall_s")) for q in queue if q.get("kind", "e2e") == "e2e" and (run / f"run-{q['pass_id']}" / "workload").is_dir()]
    serving = [pass_serving(run, p, w) for p, w in passes]
    client = [r for p, _ in passes for lane in LANES if (r := lane_client(run, p, lane))]
    funnel = [r for p, _ in passes for r in lane_funnel(run, p)]
    strata = load_strata(args.schedule) if args.schedule else None
    bottleneck = [r for p, _ in passes for lane in LANES for r in lane_bottleneck(run, p, lane, strata)]
    _write(bottleneck, out, "lane-bottleneck")
    _write(serving, out, "pass-serving")
    _write(client, out, "lane-client")
    _write(funnel, out, "lane-funnel")
    print(json.dumps({"passes": len(passes), "lane_rows": len(client), "funnel_rows": len(funnel), "bottleneck_rows": len(bottleneck), "output_dir": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
