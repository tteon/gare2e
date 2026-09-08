#!/usr/bin/env python3
"""Per-attempt workload analysis for co-located AgenticOS runs.

Joins each case's monotonic window (from ``join_agentic_os_gpu_samples.py``)
with the raw GPU, host-process, and vLLM samples and the case receipt, then
writes a content-free decomposition table (CSV + Markdown) and one HTML page
with an inline-SVG timeline per cell.  Nothing here reads prompts, queries,
evidence, or model output; only timings, counters, gauges, and outcome codes.

Single-agent receipts carry turn/tool latencies but no absolute timestamps, so
their activity lane is a *reconstructed sequence* and is labelled as such.
Multi-agent receipts carry ``started_elapsed_ms`` per event and are placed
exactly.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

BUSY_UTIL_PCT = 50.0
MAX_POINTS = 400

# Reference dataviz palette (validated): slot order is the CVD-safety mechanism.
LIGHT = {
    "surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "grid": "#e6e5e1",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100", "critical": "#e34948",
}
DARK = {
    "surface": "#1a1a19", "text": "#ffffff", "text2": "#c3c2b7", "grid": "#33332f",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500", "critical": "#e66767",
}


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_from_root(root: Path) -> dict[str, str]:
    name = root.name
    reasoning = "reasoning" if name.endswith("-true") else "non_reasoning" if name.endswith("-false") else "unknown"
    lane = "GRBench" if "grbench" in name else "GTSQA" if "gtsqa" in name else "unknown"
    topology = "multi" if "-multi-" in name else "single"
    return {"cell": name, "lane": lane, "topology": topology, "reasoning": reasoning}


def load_case_receipts(root: Path) -> list[Mapping[str, Any]]:
    """Every attempt receipt under ``root`` (gate roots hold one, matrix roots many)."""
    receipts: list[Mapping[str, Any]] = []
    for attempt in sorted((root / "attempts").glob("*")) if (root / "attempts").is_dir() else []:
        run_receipt = attempt / "run-receipt.json"
        if run_receipt.is_file():
            payload = json.loads(run_receipt.read_text(encoding="utf-8"))
            receipts.extend(c for c in payload.get("cases") or [] if isinstance(c, Mapping))
            continue
        jsonl = attempt / "receipts.jsonl"
        if jsonl.is_file():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    receipts.append(json.loads(line))
    return receipts


def load_case_receipt(root: Path) -> Mapping[str, Any] | None:
    receipts = load_case_receipts(root)
    return receipts[0] if receipts else None


def load_join(path: Path) -> dict[Any, Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[Any, Mapping[str, Any]] = {}
    for case in payload.get("cases", []):
        corr = case.get("correlation") or {}
        mapping[(str(corr.get("run_id")), str(corr.get("case_id")))] = case
        mapping.setdefault(str(corr.get("run_id")), case)
    return mapping


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def gpu_window(rows: Iterable[dict[str, str]], start: float, end: float) -> dict[str, Any]:
    devices: dict[str, dict[str, Any]] = {}
    for row in rows:
        t = _num(row.get("timestamp_monotonic_s"))
        if t is None or t < start or t > end:
            continue
        dev = devices.setdefault(str(row.get("gpu_index")), {"t": [], "util": [], "power": [], "mem": []})
        dev["t"].append(t - start)
        dev["util"].append(_num(row.get("gpu_utilization_pct")) or 0.0)
        dev["power"].append(_num(row.get("power_w")) or 0.0)
        dev["mem"].append(_num(row.get("memory_used_bytes")) or 0.0)
    summary: dict[str, Any] = {"devices": devices}
    utils = [u for d in devices.values() for u in d["util"]]
    powers = [p for d in devices.values() for p in d["power"]]
    summary["busy_share"] = round(sum(1 for u in utils if u >= BUSY_UTIL_PCT) / len(utils), 3) if utils else None
    summary["mean_util_pct"] = round(sum(utils) / len(utils), 1) if utils else None
    summary["mean_power_w"] = round(sum(powers) / len(powers), 1) if powers else None
    energy = 0.0
    for d in devices.values():
        for (t0, p0), (t1, _p1) in zip(zip(d["t"], d["power"]), list(zip(d["t"], d["power"]))[1:]):
            energy += p0 * (t1 - t0)
    summary["energy_kj"] = round(energy / 1000, 2) if powers else None
    return summary


def host_window(rows: Iterable[dict[str, str]], start: float, end: float) -> dict[str, Any]:
    roles: dict[str, list[tuple[float, float, float]]] = {}
    for row in rows:
        t = _num(row.get("timestamp_monotonic_s"))
        if t is None or t < start or t > end:
            continue
        cpu = (_num(row.get("process_cpu_user_s")) or 0.0) + (_num(row.get("process_cpu_system_s")) or 0.0)
        rss = _num(row.get("process_rss_bytes")) or 0.0
        roles.setdefault(str(row.get("role")), []).append((t - start, cpu, rss))
    out: dict[str, Any] = {"roles": {}}
    for role, samples in roles.items():
        samples.sort()
        rate_t, rate = [], []
        for (t0, c0, _), (t1, c1, _) in zip(samples, samples[1:]):
            if t1 > t0:
                rate_t.append(t1)
                rate.append(max(0.0, (c1 - c0) / (t1 - t0)))
        out["roles"][role] = {
            "cpu_s": round(samples[-1][1] - samples[0][1], 2) if len(samples) > 1 else None,
            "rss_gb_max": round(max(s[2] for s in samples) / 1e9, 2),
            "rate_t": rate_t, "rate_cores": rate,
        }
    return out


COUNTERS = ("vllm:prefix_cache_hits", "vllm:prefix_cache_queries", "vllm:prompt_tokens", "vllm:generation_tokens", "vllm:request_success")


def vllm_window(path: Path, start: float, end: float, *, margin_s: float = 3.0) -> dict[str, Any]:
    """Gauge series inside the window plus cumulative counters (with a margin
    on both sides so per-turn deltas can bracket the first and last turn)."""
    kv_t, kv, running = [], [], []
    counters: list[tuple[float, dict[str, float]]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            sample = json.loads(line)
            t = _num(sample.get("timestamp_monotonic_s"))
            if t is None or t < start - margin_s or t > end + margin_s:
                continue
            metrics = sample.get("metrics") or {}
            counters.append((t - start, {name: (_num(metrics.get(name)) or 0.0) for name in COUNTERS}))
            if start <= t <= end:
                kv_t.append(t - start)
                kv.append(100.0 * (_num(metrics.get("vllm:kv_cache_usage_perc")) or 0.0))
                running.append(_num(metrics.get("vllm:num_requests_running")) or 0.0)
    return {"t": kv_t, "kv_pct": kv, "running": running, "counters": counters}


def counter_delta(counters: list[tuple[float, dict[str, float]]], t0: float, t1: float, name: str) -> float | None:
    """Delta of a cumulative counter between the last sample at/before ``t0``
    and the first sample at/after ``t1`` (1 s sampler: sub-second turns are
    reported as None rather than guessed)."""
    before = [c for c in counters if c[0] <= t0]
    after = [c for c in counters if c[0] >= t1]
    if not before or not after:
        return None
    return after[0][1].get(name, 0.0) - before[-1][1].get(name, 0.0)


def context_profile(receipt: Mapping[str, Any], spans: list[dict[str, Any]], counters: list[tuple[float, dict[str, float]]]) -> dict[str, Any]:
    """Per-turn context growth and per-turn prefix-cache reuse.

    Single-agent receipts carry input/output tokens per turn; the turn spans
    give time offsets, and the 1 s vLLM counters give hits/queries per turn.
    Multi-agent receipts only carry role totals, so their per-turn rows come
    from the timed LLM requests (when present) with token fields absent.
    """
    turns = [t for t in receipt.get("turns") or [] if isinstance(t, Mapping)]
    turn_spans = [sp for sp in spans if sp["kind"] == "turn"]
    rows: list[dict[str, Any]] = []
    base = _num(turns[0].get("input_tokens")) if turns else None
    for index, sp in enumerate(turn_spans):
        turn = turns[index] if index < len(turns) else {}
        hits = counter_delta(counters, sp["start"], sp["end"], "vllm:prefix_cache_hits")
        queries = counter_delta(counters, sp["start"], sp["end"], "vllm:prefix_cache_queries")
        prompt = counter_delta(counters, sp["start"], sp["end"], "vllm:prompt_tokens")
        inp = _num(turn.get("input_tokens"))
        rows.append({
            "turn": index + 1,
            "kind": turn.get("output_kind"),
            "input_tokens": inp,
            "growth": (inp - base) if (inp is not None and base is not None) else None,
            "output_tokens": _num(turn.get("output_tokens")),
            "start_s": round(sp["start"], 1), "end_s": round(sp["end"], 1),
            "prefix_hit": _ratio(hits, queries) if queries else None,
            "prefix_queries": queries, "prompt_tokens_served": prompt,
        })
    tools = [t for t in receipt.get("tools") or [] if isinstance(t, Mapping)]
    events = [e for e in receipt.get("events") or [] if isinstance(e, Mapping) and e.get("event") == "tool"]
    evidence_bytes = sum(int(_num(t.get("result_bytes")) or 0) for t in tools) + sum(int(_num(e.get("evidence_bytes")) or 0) for e in events)
    inputs = [r["input_tokens"] for r in rows if r["input_tokens"] is not None]
    outputs = [r["output_tokens"] for r in rows if r["output_tokens"] is not None]
    hits = [r["prefix_hit"] for r in rows if r["prefix_hit"] is not None]
    roles = receipt.get("role_characteristics") or {}
    role_usage = {role: (v.get("usage") if isinstance(v, Mapping) else None) for role, v in roles.items()} if isinstance(roles, Mapping) else {}
    return {
        "turns": rows,
        "prompt_first": inputs[0] if inputs else None,
        "prompt_last": inputs[-1] if inputs else None,
        "prompt_growth_per_turn": round((inputs[-1] - inputs[0]) / (len(inputs) - 1), 1) if len(inputs) > 1 else None,
        "evidence_bytes": evidence_bytes,
        "capped_output_turns": sum(1 for o in outputs if outputs and o == max(outputs) and o >= 2048),
        "turn_prefix_hit_mean": round(sum(hits) / len(hits), 3) if hits else None,
        "role_usage": role_usage,
    }


def _delta(case: Mapping[str, Any], name: str) -> float | None:
    return _num(((case.get("vllm") or {}).get("metrics_delta") or {}).get(name))


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return round(num / den, 3)


def activity_lane(receipt: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return (spans, exact).  Spans: kind, label, start_s, end_s, status."""
    events = [e for e in receipt.get("events") or [] if isinstance(e, Mapping)]
    timed = [e for e in events if _num(e.get("started_elapsed_ms")) is not None and _num(e.get("elapsed_ms")) is not None]
    llm_requests = [r for r in receipt.get("llm_requests") or [] if isinstance(r, Mapping) and _num(r.get("started_elapsed_ms")) is not None]
    if timed or llm_requests:
        spans = []
        for index, r in enumerate(llm_requests):
            s = _num(r.get("started_elapsed_ms")) / 1000
            f = (_num(r.get("latency_ms")) or 0.0) / 1000
            spans.append({"kind": "turn", "label": f"LLM request {index + 1} (HTTP {r.get('status_code')})", "start": s, "end": s + max(f, 0.05), "status": "ok", "code": None})
        for e in timed:
            s = _num(e.get("started_elapsed_ms")) / 1000
            f = _num(e.get("elapsed_ms")) / 1000
            kind = "tool" if e.get("event") in {"tool", "agent_tool", "agent_tool_return"} else "turn"
            status = "error" if e.get("outcome") in {"error", "rejected"} else "ok"
            spans.append({"kind": kind, "label": f"{e.get('event')}:{e.get('name') or e.get('outcome')}", "start": s, "end": max(f, s + 0.05), "status": status, "code": e.get("error_code")})
        return spans, True
    spans, cursor = [], 0.0
    tools = [t for t in receipt.get("tools") or [] if isinstance(t, Mapping)]
    for index, turn in enumerate(receipt.get("turns") or []):
        if not isinstance(turn, Mapping):
            continue
        latency = (_num(turn.get("latency_ms")) or 0.0) / 1000
        spans.append({"kind": "turn", "label": f"turn {index + 1} ({turn.get('output_kind')}, {turn.get('output_tokens')} out tok)", "start": cursor, "end": cursor + latency, "status": "ok", "code": None})
        cursor += latency
        if turn.get("output_kind") == "tool_calls" and tools:
            tool = tools.pop(0)
            latency = (_num(tool.get("latency_ms")) or 0.0) / 1000
            status = "error" if tool.get("outcome") in {"error", "rejected"} else "ok"
            spans.append({"kind": "tool", "label": f"{tool.get('name')} ({tool.get('outcome')})", "start": cursor, "end": cursor + max(latency, 0.05), "status": status, "code": tool.get("error_code")})
            cursor += latency
    for tool in tools:
        latency = (_num(tool.get("latency_ms")) or 0.0) / 1000
        status = "error" if tool.get("outcome") in {"error", "rejected"} else "ok"
        spans.append({"kind": "tool", "label": f"{tool.get('name')} ({tool.get('outcome')})", "start": cursor, "end": cursor + max(latency, 0.05), "status": status, "code": tool.get("error_code")})
        cursor += latency
    return spans, False


def load_strata(schedules: list[Path], labels: list[Path] | None = None) -> dict[tuple[str, str], str]:
    """Map (lane, qid) → stratum.

    Frozen schedules give the stratum a case was *scheduled* under; difficulty
    label files (``label_agentic_os_difficulty.py`` output) take precedence so
    a later rubric correction re-labels analysis without re-freezing the
    cohort that already ran.
    """
    mapping: dict[tuple[str, str], str] = {}
    for path in schedules:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lane = "GTSQA" if "gtsqa" in path.name else "GRBench"
        for entry in payload.get("entries", []):
            mapping[(lane, str(entry.get("qid")))] = str(entry.get("stratum") or "unstratified")
    for path in labels or []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            lane = "GTSQA" if str(row.get("lane", path.name)).lower().startswith("gtsqa") else "GRBench"
            if row.get("stratum"):
                mapping[(lane, str(row.get("qid")))] = str(row["stratum"])
    return mapping


def analyse(
    *, run_roots: list[Path], join_path: Path, gpu_path: Path, host_path: Path, vllm_path: Path,
    schedules: list[Path] | None = None, difficulty_labels: list[Path] | None = None,
) -> list[dict[str, Any]]:
    joined = load_join(join_path)
    strata = load_strata(schedules or [], difficulty_labels or [])
    gpu_rows = _read_csv(gpu_path)
    host_rows = _read_csv(host_path)
    results: list[dict[str, Any]] = []
    for root in run_roots:
      for receipt in load_case_receipts(root):
        run_id = str(receipt.get("run_id"))
        case = joined.get((run_id, str(receipt.get("case_id")))) or joined.get(run_id)
        window = (case or {}).get("window") or {}
        start, end = _num(window.get("started_monotonic_s")), _num(window.get("ended_monotonic_s"))
        cell = _cell_from_root(root)
        time_block = receipt.get("time") or {}
        funnel = receipt.get("retrieval_funnel") or {}
        turns = receipt.get("turns") or []
        # GTSQA single-agent receipts list tool *names* as strings; only mapping
        # entries carry outcomes and latencies.
        tools = [t for t in receipt.get("tools") or [] if isinstance(t, Mapping)]
        events = [e for e in receipt.get("events") or [] if isinstance(e, Mapping) and e.get("event") == "tool"]
        rejected = sum(1 for t in tools if t.get("outcome") in {"rejected", "error"}) + sum(1 for e in events if e.get("outcome") in {"error", "rejected"})
        row: dict[str, Any] = {
            **cell,
            "run_id": run_id,
            "case_id": str(receipt.get("case_id")),
            "stratum": strata.get((cell["lane"], str(receipt.get("case_id"))), "unstratified"),
            "condition": receipt.get("condition") or ((receipt.get("retrieval_funnel") or {}).get("evidence_class")),
            "retrieval_triggered": bool(funnel.get("graph_tool_selected")),
            "schema_calls": funnel.get("schema_discovery_calls"),
            "terminal": receipt.get("terminal") or receipt.get("terminal_status"),
            "wall_s": round((_num(time_block.get("wall_ms")) or 0) / 1000, 1),
            "llm_s": round((_num(time_block.get("llm_ms")) or 0) / 1000, 1),
            "tool_s": round((_num(time_block.get("tool_ms")) or 0) / 1000, 1),
            "residual_s": round(((_num(time_block.get("wall_ms")) or 0) - (_num(time_block.get("llm_ms")) or 0) - (_num(time_block.get("tool_ms")) or 0) - (_num(time_block.get("observability_overhead_ms")) or 0)) / 1000, 1),
            "observability_s": round((_num(time_block.get("observability_overhead_ms")) or 0) / 1000, 2),
            "turns": receipt.get("turn_count") or len(turns),
            "output_tokens": sum(int(_num(t.get("output_tokens")) or 0) for t in turns) or None,
            "graph_queries": funnel.get("graph_query_calls"),
            "rejected_calls": rejected,
            "query_validated": funnel.get("query_validated"),
            "nonempty_result": funnel.get("nonempty_result"),
        }
        if start is not None and end is not None:
            gpu = gpu_window(gpu_rows, start, end)
            host = host_window(host_rows, start, end)
            vllm = vllm_window(vllm_path, start, end)
            req = _delta(case, "vllm:request_success") or _delta(case, "vllm:e2e_request_latency_seconds_count")
            row.update({
                "window_s": round(end - start, 1),
                "gpu_busy_share": gpu["busy_share"], "gpu_mean_util_pct": gpu["mean_util_pct"],
                "gpu_mean_power_w": gpu["mean_power_w"], "gpu_energy_kj": gpu["energy_kj"],
                "cpu_s_vllm": (host["roles"].get("vllm") or {}).get("cpu_s"),
                "cpu_s_dozerdb": (host["roles"].get("dozerdb") or {}).get("cpu_s"),
                "cpu_s_agent": (host["roles"].get("agent") or {}).get("cpu_s"),
                "vllm_requests": req,
                "prefill_s_per_req": _ratio(_delta(case, "vllm:request_prefill_time_seconds_sum"), _delta(case, "vllm:request_prefill_time_seconds_count")),
                "decode_s_per_req": _ratio(_delta(case, "vllm:request_decode_time_seconds_sum"), _delta(case, "vllm:request_decode_time_seconds_count")),
                "queue_s_per_req": _ratio(_delta(case, "vllm:request_queue_time_seconds_sum"), _delta(case, "vllm:request_queue_time_seconds_count")),
                "ttft_s_avg": _ratio(_delta(case, "vllm:time_to_first_token_seconds_sum"), _delta(case, "vllm:time_to_first_token_seconds_count")),
                "itl_ms_avg": (lambda r: None if r is None else round(r * 1000, 1))(_ratio(_delta(case, "vllm:inter_token_latency_seconds_sum"), _delta(case, "vllm:inter_token_latency_seconds_count"))),
                "prefix_hit_ratio": _ratio(_delta(case, "vllm:prefix_cache_hits"), _delta(case, "vllm:prefix_cache_queries")),
                "prompt_tokens": _delta(case, "vllm:prompt_tokens"), "generation_tokens": _delta(case, "vllm:generation_tokens"),
                "kv_usage_max_pct": round(max(vllm["kv_pct"]), 2) if vllm["kv_pct"] else None,
                "_series": {"gpu": gpu, "host": host, "vllm": vllm},
            })
        spans, exact = activity_lane(receipt)
        row["_spans"] = spans
        row["activity_timing"] = "measured" if exact else "reconstructed"
        counters = ((row.get("_series") or {}).get("vllm") or {}).get("counters") or []
        profile = context_profile(receipt, spans, counters)
        row["_context"] = profile
        row.update({k: profile[k] for k in ("prompt_first", "prompt_last", "prompt_growth_per_turn", "evidence_bytes", "capped_output_turns", "turn_prefix_hit_mean")})
        results.append(row)
    return results


TABLE_COLUMNS = [
    "cell", "lane", "topology", "reasoning", "case_id", "stratum", "condition", "retrieval_triggered", "schema_calls", "terminal", "wall_s", "llm_s", "tool_s", "residual_s", "observability_s",
    "turns", "output_tokens", "graph_queries", "rejected_calls", "query_validated", "nonempty_result", "window_s",
    "gpu_busy_share", "gpu_mean_util_pct", "gpu_mean_power_w", "gpu_energy_kj", "cpu_s_vllm", "cpu_s_dozerdb", "cpu_s_agent",
    "vllm_requests", "prefill_s_per_req", "decode_s_per_req", "queue_s_per_req", "ttft_s_avg", "itl_ms_avg", "prefix_hit_ratio",
    "prompt_tokens", "generation_tokens", "kv_usage_max_pct", "activity_timing",
    "prompt_first", "prompt_last", "prompt_growth_per_turn", "evidence_bytes", "capped_output_turns", "turn_prefix_hit_mean",
]


def write_table(rows: list[dict[str, Any]], out_dir: Path) -> None:
    with (out_dir / "attempt-decomposition.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TABLE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in TABLE_COLUMNS})
    short = ["cell", "terminal", "wall_s", "llm_s", "tool_s", "residual_s", "turns", "graph_queries", "rejected_calls", "gpu_busy_share", "gpu_mean_power_w", "cpu_s_dozerdb", "cpu_s_agent", "prefill_s_per_req", "decode_s_per_req", "ttft_s_avg", "prefix_hit_ratio", "kv_usage_max_pct"]
    lines = ["| " + " | ".join(short) + " |", "|" + "---|" * len(short)]
    for row in rows:
        lines.append("| " + " | ".join("" if row.get(k) is None else str(row.get(k)) for k in short) + " |")
    (out_dir / "attempt-decomposition.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    strata = sorted({str(r.get("stratum")) for r in rows})
    if strata and strata != ["unstratified"]:
        import statistics

        def med(rs: list[dict[str, Any]], key: str) -> str:
            values = [r[key] for r in rs if r.get(key) is not None]
            return f"{statistics.median(values):.2f}" if values else ""

        lines2 = ["| lane | topology | reasoning | stratum | N | triggered | attempted | validated | nonempty | median wall_s | median llm_s | median tool_s | median decode_s/req | median gpu_busy |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        tool_rows = [r for r in rows if str(r.get("condition")) != "closed_book"]
        groups = sorted({(r["lane"], r["topology"], r["reasoning"], str(r.get("stratum"))) for r in tool_rows}, key=lambda g: (g[0], g[1], g[2], ["easy", "medium", "hard"].index(g[3]) if g[3] in ("easy", "medium", "hard") else 9))
        for lane, topo, reasoning, st in groups:
            rs = [r for r in tool_rows if (r["lane"], r["topology"], r["reasoning"], str(r.get("stratum"))) == (lane, topo, reasoning, st)]
            n = len(rs)
            pct = lambda k: f"{sum(1 for r in rs if r.get(k))}/{n}"
            att = f"{sum(1 for r in rs if (r.get('graph_queries') or 0) >= 1)}/{n}"
            lines2.append(f"| {lane} | {topo} | {reasoning} | {st} | {n} | {pct('retrieval_triggered')} | {att} | {pct('query_validated')} | {pct('nonempty_result')} | {med(rs, 'wall_s')} | {med(rs, 'llm_s')} | {med(rs, 'tool_s')} | {med(rs, 'decode_s_per_req')} | {med(rs, 'gpu_busy_share')} |")
        (out_dir / "stratum-summary.md").write_text("\n".join(lines2) + "\n", encoding="utf-8")
    ctx = ["| cell | turn | kind | input tokens | growth vs turn 1 | output tokens | prefix hit (turn) | prompt tokens served |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        for t in (row.get("_context") or {}).get("turns", []):
            ctx.append("| " + " | ".join(str(v) if v is not None else "" for v in (row["cell"], t["turn"], t["kind"], t["input_tokens"], t["growth"], t["output_tokens"], t["prefix_hit"], t["prompt_tokens_served"])) + " |")
        usage = (row.get("_context") or {}).get("role_usage") or {}
        for role, u in usage.items():
            if isinstance(u, Mapping):
                ctx.append(f"| {row['cell']} | role:{role} | {u.get('requests')} requests | {u.get('input_tokens')} | | {u.get('output_tokens')} | | |")
    (out_dir / "context-growth.md").write_text("\n".join(ctx) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- SVG rendering
W, PAD_L, PAD_R = 960, 150, 24


def _x(t: float, span: float) -> float:
    return PAD_L + (W - PAD_L - PAD_R) * (t / span if span else 0)


def _polyline(ts: list[float], vs: list[float], span: float, y0: float, h: float, vmax: float, color: str) -> str:
    if not ts:
        return ""
    step = max(1, len(ts) // MAX_POINTS)
    pts = " ".join(f"{_x(t, span):.1f},{y0 + h - h * min(v, vmax) / vmax:.1f}" for t, v in list(zip(ts, vs))[::step])
    return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'


def _panel(y0: float, h: float, title: str, vmax_label: str) -> str:
    return (
        f'<line x1="{PAD_L}" y1="{y0 + h}" x2="{W - PAD_R}" y2="{y0 + h}" class="grid"/>'
        f'<line x1="{PAD_L}" y1="{y0}" x2="{W - PAD_R}" y2="{y0}" class="grid"/>'
        f'<text x="8" y="{y0 + 14}" class="t2">{html.escape(title)}</text>'
        f'<text x="{PAD_L - 6}" y="{y0 + 10}" class="t3" text-anchor="end">{html.escape(vmax_label)}</text>'
        f'<text x="{PAD_L - 6}" y="{y0 + h}" class="t3" text-anchor="end">0</text>'
    )


def _legend(x: float, y: float, items: list[tuple[str, str]]) -> str:
    parts, cx = [], x
    for label, color in items:
        parts.append(f'<rect x="{cx}" y="{y - 9}" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{cx + 14}" y="{y}" class="t3">{html.escape(label)}</text>')
        cx += 14 + 7 * len(label) + 18
    return "".join(parts)


def render_timeline(row: dict[str, Any]) -> str:
    span = row.get("window_s") or max((s["end"] for s in row.get("_spans", [])), default=1.0) or 1.0
    series = row.get("_series") or {}
    parts: list[str] = []
    y = 28
    # activity lane
    parts.append(_panel(y, 44, f"activity ({row['activity_timing']})", ""))
    for s in row.get("_spans", []):
        x0, x1 = _x(s["start"], span), _x(min(s["end"], span), span)
        color = "var(--s1)" if s["kind"] == "turn" else ("var(--critical)" if s["status"] == "error" else "var(--s3)")
        yy = y + 6 if s["kind"] == "turn" else y + 26
        label = s["label"] + (f" [{s['code']}]" if s.get("code") else "")
        parts.append(f'<rect x="{x0:.1f}" y="{yy}" width="{max(x1 - x0 - 2, 2):.1f}" height="14" rx="3" fill="{color}"><title>{html.escape(label)}: {s["start"]:.1f}s → {s["end"]:.1f}s</title></rect>')
        if s["kind"] == "tool" and s["status"] == "error":
            parts.append(f'<text x="{x0 + 2:.1f}" y="{yy + 11}" class="mark">✕</text>')
    parts.append(_legend(PAD_L, y - 6, [("LLM turn", LIGHT["s1"]), ("graph tool ok", LIGHT["s3"]), ("graph tool rejected ✕", LIGHT["critical"])]))
    y += 60
    ctx = row.get("_context") or {}
    turns = [t for t in ctx.get("turns", []) if t.get("input_tokens") is not None]
    if turns:
        tmax = max(max(t["input_tokens"] for t in turns), max((t["output_tokens"] or 0) for t in turns), 1.0)
        parts.append(_panel(y, 70, "context per turn (tokens)", f"{tmax:.0f}"))
        base = turns[0]["input_tokens"]
        for t in turns:
            x0 = _x(t["start_s"], span); x1 = _x(min(t["end_s"], span), span)
            w = max(min(x1 - x0 - 2, 24), 4)
            h_base = 70 * min(base, t["input_tokens"]) / tmax
            h_grow = 70 * max(t["input_tokens"] - base, 0) / tmax
            parts.append(f'<rect x="{x0:.1f}" y="{y + 70 - h_base:.1f}" width="{w:.1f}" height="{h_base:.1f}" fill="var(--s1)"><title>turn {t["turn"]}: input {t["input_tokens"]:.0f} tokens (turn-1 base {base:.0f})</title></rect>')
            if h_grow > 0:
                parts.append(f'<rect x="{x0:.1f}" y="{y + 70 - h_base - h_grow - 2:.1f}" width="{w:.1f}" height="{h_grow:.1f}" rx="3" fill="var(--s3)"><title>turn {t["turn"]}: +{t["growth"]:.0f} tokens vs turn 1</title></rect>')
            if t.get("output_tokens"):
                h_out = 70 * t["output_tokens"] / tmax
                parts.append(f'<rect x="{x0 + w + 2:.1f}" y="{y + 70 - h_out:.1f}" width="{w * 0.5:.1f}" height="{h_out:.1f}" rx="3" fill="var(--s2)"><title>turn {t["turn"]}: output {t["output_tokens"]:.0f} tokens ({t["kind"]})</title></rect>')
            if t.get("prefix_hit") is not None:
                parts.append(f'<text x="{x0:.1f}" y="{y - 8}" class="t3">hit {t["prefix_hit"]:.2f}</text>')
        parts.append(_legend(PAD_L + 120, y - 6, [("input: turn-1 base", LIGHT["s1"]), ("input growth", LIGHT["s3"]), ("output", LIGHT["s2"])]))
        y += 86
    gpu = series.get("gpu") or {}
    devices = (gpu.get("devices") or {})
    colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
    if devices:
        parts.append(_panel(y, 70, "GPU utilisation %", "100"))
        for (idx, dev), color in zip(sorted(devices.items()), colors):
            parts.append(_polyline(dev["t"], dev["util"], span, y, 70, 100, color))
        parts.append(_legend(PAD_L, y - 6, [(f"GPU {idx}", LIGHT[f"s{i + 1}"]) for i, idx in enumerate(sorted(devices))]))
        y += 86
        pmax = max((max(dev["power"]) for dev in devices.values() if dev["power"]), default=400) or 400
        parts.append(_panel(y, 60, "GPU power W", f"{pmax:.0f}"))
        for (idx, dev), color in zip(sorted(devices.items()), colors):
            parts.append(_polyline(dev["t"], dev["power"], span, y, 60, pmax, color))
        y += 76
    vllm = series.get("vllm") or {}
    if vllm.get("t"):
        kmax = max(max(vllm["kv_pct"]), 1.0)
        parts.append(_panel(y, 50, "vLLM KV-cache usage %", f"{kmax:.1f}"))
        parts.append(_polyline(vllm["t"], vllm["kv_pct"], span, y, 50, kmax, "var(--s3)"))
        y += 66
    host = (series.get("host") or {}).get("roles") or {}
    if host:
        rmax = max((max(r["rate_cores"]) for r in host.values() if r["rate_cores"]), default=1.0) or 1.0
        parts.append(_panel(y, 60, "process CPU (cores)", f"{rmax:.1f}"))
        roles = [r for r in ("vllm", "dozerdb", "agent") if r in host]
        for role, color in zip(roles, colors):
            parts.append(_polyline(host[role]["rate_t"], host[role]["rate_cores"], span, y, 60, rmax, color))
        parts.append(_legend(PAD_L, y - 6, [(role, LIGHT[f"s{i + 1}"]) for i, role in enumerate(roles)]))
        y += 76
    # time axis
    ticks = 6
    for i in range(ticks + 1):
        t = span * i / ticks
        parts.append(f'<text x="{_x(t, span):.1f}" y="{y + 12}" class="t3" text-anchor="middle">{t:.0f}s</text>')
    height = y + 24
    return f'<svg viewBox="0 0 {W} {height}" width="100%" role="img" aria-label="timeline {html.escape(row["cell"])}">{"".join(parts)}</svg>'


def write_html(rows: list[dict[str, Any]], out_dir: Path, title: str) -> None:
    style = f"""
<style>
.viz-root{{color-scheme:light;--surface:{LIGHT['surface']};--text:{LIGHT['text']};--text2:{LIGHT['text2']};--grid:{LIGHT['grid']};--s1:{LIGHT['s1']};--s2:{LIGHT['s2']};--s3:{LIGHT['s3']};--s4:{LIGHT['s4']};--critical:{LIGHT['critical']};
 background:var(--surface);color:var(--text);font:14px 'IBM Plex Sans',system-ui,sans-serif;padding:16px 20px 40px;max-width:1080px;margin:auto}}
body{{background:{LIGHT['surface']};margin:0}} .viz-root p.summary{{font-size:15px;font-weight:600;margin:0 0 6px}}
table td,table th{{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}} th{{color:var(--text2);font-weight:400}}
@media (prefers-color-scheme: dark){{:root:where(:not([data-theme="light"])) .viz-root{{color-scheme:dark;--surface:{DARK['surface']};--text:{DARK['text']};--text2:{DARK['text2']};--grid:{DARK['grid']};--s1:{DARK['s1']};--s2:{DARK['s2']};--s3:{DARK['s3']};--s4:{DARK['s4']};--critical:{DARK['critical']}}}}}
:root[data-theme="dark"] .viz-root{{color-scheme:dark;--surface:{DARK['surface']};--text:{DARK['text']};--text2:{DARK['text2']};--grid:{DARK['grid']};--s1:{DARK['s1']};--s2:{DARK['s2']};--s3:{DARK['s3']};--s4:{DARK['s4']};--critical:{DARK['critical']}}}
svg .grid{{stroke:var(--grid);stroke-width:1}} svg text{{fill:var(--text);font-size:12px}} svg .t2{{fill:var(--text2);font-size:12px}} svg .t3{{fill:var(--text2);font-size:10px}} svg .mark{{fill:#fff;font-size:10px}}
table{{border-collapse:collapse;font-size:12px;width:100%;overflow-x:auto;display:block}} th,td{{padding:4px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}} th:first-child,td:first-child{{text-align:left}}
h1{{font-size:20px}} h2{{font-size:15px;margin:28px 0 4px}} p.note{{color:var(--text2)}}
</style>"""
    busy = [r["gpu_busy_share"] for r in rows if r.get("gpu_busy_share") is not None]
    decode = [r["decode_s_per_req"] for r in rows if r.get("decode_s_per_req") is not None]
    summary = (
        f"{len(rows)} attempts · GPU busy share {min(busy):.2f}–{max(busy):.2f} · decode {min(decode):.1f}–{max(decode):.1f} s/request · "
        f"{sum(1 for r in rows if r.get('query_validated'))}/{len(rows)} cells with a validator-accepted query"
        if busy and decode else f"{len(rows)} attempts"
    )
    body = [f"<title>{html.escape(title)}</title>",
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">',
            style, '<div class="viz-root">', f"<h1>{html.escape(title)}</h1>", f'<p class="summary">{html.escape(summary)}</p>',
            '<p class="note">Content-free workload decomposition per attempt. Gate data is one case per cell: describe, do not generalise. '
            'Single-agent activity lanes are reconstructed from turn/tool latencies in order (no absolute timestamps); multi-agent lanes use measured event offsets.</p>']
    body.append("<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in TABLE_COLUMNS) + "</tr></thead><tbody>")
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{html.escape('' if row.get(c) is None else str(row.get(c)))}</td>" for c in TABLE_COLUMNS) + "</tr>")
    body.append("</tbody></table>")
    for row in rows:
        body.append(f"<h2>{html.escape(row['cell'])} — {html.escape(str(row.get('terminal')))}</h2>")
        body.append(render_timeline(row))
    body.append("</div>")
    (out_dir / "attempt-timelines.html").write_text("\n".join(body), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--telemetry-join", required=True, type=Path)
    parser.add_argument("--gpu-samples", required=True, type=Path)
    parser.add_argument("--host-samples", required=True, type=Path)
    parser.add_argument("--vllm-samples", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--title", default="AgenticOS attempt-window analysis")
    parser.add_argument("--schedule", action="append", type=Path, default=[], help="Frozen schedule file(s) providing the stratum per qid.")
    parser.add_argument("--difficulty-labels", action="append", type=Path, default=[], help="label_agentic_os_difficulty.py JSONL; overrides schedule strata.")
    args = parser.parse_args()
    rows = analyse(run_roots=args.run_roots, join_path=args.telemetry_join, gpu_path=args.gpu_samples, host_path=args.host_samples, vllm_path=args.vllm_samples, schedules=args.schedule, difficulty_labels=args.difficulty_labels)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_table(rows, args.output_dir)
    write_html(rows, args.output_dir, args.title)
    print(json.dumps({"attempts": len(rows), "output_dir": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
