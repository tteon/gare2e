"""Turn-level and layer-level view of a queue-mode run (lab 014, Experiment 0).

Two content-free tables per run, written under ``<run>/session-summary``:

* ``turn-summary.{csv,md}`` — per pass x lane x query class x turn ordinal:
  cases reaching the turn, input / cached / output tokens, turn latency p50,
  and the tool-use gap to the next turn split into graph-tool time and the
  residual (parse + serialise + prompt build + client). Single-agent lanes only
  (multi-agent receipts carry llm_requests without turn ordinals).
* ``layer-shares.{csv,md}`` — per pass x lane x query class: where the case wall
  goes across four layers — Graph (tool latency), Agent (wall - LLM requests -
  graph), Host (vLLM queue + server-unaccounted + client-minus-server), GPU
  prefill, GPU decode — plus the decode ratios the talk needs:
  decode / inference (per request, median) and sum(decode) / E2E wall.

Query classes: L1 = easy, L2 = medium, L3 = hard (the structural rubric of
label_agentic_os_difficulty.py; the letters are a presentation alias only).

    python3 scripts/benchmarks/summarize_agentic_os_turns.py <run-root> --strata <strata-dir>
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

HOP_TOOLS = ("RetrieveNode", "NodeFeature", "NeighbourCheck", "NodeDegree")


def _is_graph_call(name):
    name = str(name or "")
    return "execute" in name or name in HOP_TOOLS



CLASS = {"easy": "L1", "medium": "L2", "hard": "L3"}


def _q(v: list[float], p: float) -> float:
    if not v:
        return float("nan")
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * p))]


def load_strata(strata_dir: Path) -> dict[tuple[str, str], str]:
    out = {}
    for lane in ("grbench", "gtsqa"):
        p = strata_dir / f"{lane}-cases.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = json.loads(line)
                    out[(lane, str(c["qid"]))] = c["stratum"]
    return out


def iter_cases(lane_dir: Path):
    for f in glob.glob(str(lane_dir / "shard-*" / "attempts" / "*" / "run-receipt.json")):
        for c in json.loads(Path(f).read_text(encoding="utf-8")).get("cases", []):
            yield c
    for f in glob.glob(str(lane_dir / "shard-*" / "attempts" / "*" / "receipts.jsonl")):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def tool_events(c: dict) -> list[dict]:
    tools = c.get("tools")
    if isinstance(tools, list):
        return tools
    if isinstance(tools, dict):
        return tools.get("events") or []
    return [e for e in (c.get("events") or []) if "tool" in str(e.get("event", ""))]


def graph_ms(c: dict) -> float:
    return float(sum((t.get("latency_ms") or 0) for t in tool_events(c) if _is_graph_call(t.get("name"))))


def llm_ms(c: dict) -> float:
    if c.get("turns"):
        return float(sum((t.get("latency_ms") or 0) for t in c["turns"]))
    return float(sum((r.get("latency_ms") or 0) for r in (c.get("llm_requests") or [])))


def wall_ms(c: dict) -> float:
    t = c.get("time") or {}
    if t.get("wall_ms"):
        return float(t["wall_ms"])
    return float(((c.get("resource_attribution") or {}).get("critical_path_exclusive_ms") or {}).get("wall") or 0)


def load_stage_rows(pass_dir: Path) -> dict[tuple[str, str], dict[str, float]]:
    """Sum vLLM stage seconds per (lane, case_id) from otel-stages/request-stages.csv."""
    p = pass_dir / "otel-stages" / "request-stages.csv"
    agg: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if not p.exists():
        return agg
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["lane"], str(r["case_id"]))
            for col in ("queue_s", "prefill_s", "decode_s", "server_unaccounted_s", "client_minus_server_s", "server_e2e_s", "client_latency_s"):
                try:
                    agg[key][col] += float(r.get(col) or 0)
                except ValueError:
                    pass
            agg[key]["requests"] += 1
            try:
                d, e = float(r.get("decode_s") or 0), float(r.get("server_e2e_s") or 0)
                if e > 0:
                    agg[key].setdefault("_ratios", [])  # type: ignore[arg-type]
                    agg[key]["_ratios"].append(d / e)  # type: ignore[union-attr]
            except ValueError:
                pass
    return agg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_root", type=Path)
    ap.add_argument("--strata", type=Path, required=True, help="strata dir with grbench-cases.jsonl / gtsqa-cases.jsonl")
    args = ap.parse_args()
    run = args.run_root.resolve()
    strata = load_strata(args.strata)
    out_dir = run / "session-summary"
    out_dir.mkdir(exist_ok=True)

    turn_rows: list[dict] = []
    layer_rows: list[dict] = []
    for pass_dir in sorted(run.glob("run-*")):
        pass_id = pass_dir.name.replace("run-", "")
        stages = load_stage_rows(pass_dir)
        for lane_dir in sorted((pass_dir / "e2e").glob("*-tool-*")):
            lane = lane_dir.name.rsplit("-tool-", 1)[0]
            tail = lane_dir.name.rsplit("-tool-", 1)[1]
            arm_suffix = tail.split("-", 1)[1] if "-" in tail else "B0"
            fam = "grbench" if lane.startswith("grbench") else "gtsqa"
            by_class: dict[tuple[str, str], list[dict]] = defaultdict(list)
            for c in iter_cases(lane_dir):
                qid = str(c.get("qid") or c.get("case_id")).split(":")[-1]
                by_class[(str(c.get("arm") or arm_suffix), CLASS.get(strata.get((fam, qid), ""), "?"))].append(c)
            for (arm, klass), cases in sorted(by_class.items()):
                # ---- turn table (single-agent lanes)
                per_turn: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
                for c in cases:
                    turns = c.get("turns") or []
                    tools = tool_events(c)
                    for i, t in enumerate(turns):
                        d = per_turn[i + 1]
                        d["in"].append(t.get("input_tokens") or 0)
                        d["cached"].append(t.get("cached_tokens") or 0)
                        d["out"].append(t.get("output_tokens") or 0)
                        d["lat"].append(t.get("latency_ms") or 0)
                        if i + 1 < len(turns) and t.get("started_elapsed_ms") is not None and turns[i + 1].get("started_elapsed_ms") is not None:
                            end = t["started_elapsed_ms"] + (t.get("latency_ms") or 0)
                            nxt = turns[i + 1]["started_elapsed_ms"]
                            g = sum((x.get("latency_ms") or 0) for x in tools if x.get("started_elapsed_ms") is not None and end - 5 <= x["started_elapsed_ms"] <= nxt)
                            d["gap"].append(nxt - end)
                            d["gap_graph"].append(g)
                            d["gap_residual"].append(nxt - end - g)
                for k in sorted(per_turn):
                    d = per_turn[k]
                    turn_rows.append({
                        "pass": pass_id, "lane": lane, "arm": arm, "class": klass, "turn": k, "cases": len(d["in"]),
                        "input_tokens_mean": round(statistics.mean(d["in"])), "cached_tokens_mean": round(statistics.mean(d["cached"])),
                        "output_tokens_mean": round(statistics.mean(d["out"])), "latency_ms_p50": round(statistics.median(d["lat"])),
                        "gap_ms_p50": round(statistics.median(d["gap"])) if d["gap"] else "", "gap_ms_p90": round(_q(d["gap"], 0.9)) if d["gap"] else "",
                        "gap_graph_ms_p50": round(statistics.median(d["gap_graph"])) if d["gap_graph"] else "",
                        "gap_residual_ms_p90": round(_q(d["gap_residual"], 0.9)) if d["gap_residual"] else "",
                    })
                # ---- layer shares
                walls, graph, agent, host, prefill, decode, ratios, sum_dec_ratio = [], [], [], [], [], [], [], []
                joined = 0
                for c in cases:
                    w = wall_ms(c) / 1000.0
                    if w <= 0:
                        continue
                    g = graph_ms(c) / 1000.0
                    llm = llm_ms(c) / 1000.0
                    qid = str(c.get("qid") or c.get("case_id")).split(":")[-1]
                    st = stages.get((lane, qid))
                    walls.append(w)
                    graph.append(g / w)
                    agent.append(max(0.0, w - llm - g) / w)
                    if st and st.get("requests"):
                        joined += 1
                        host.append((st["queue_s"] + st["server_unaccounted_s"] + st["client_minus_server_s"]) / w)
                        prefill.append(st["prefill_s"] / w)
                        decode.append(st["decode_s"] / w)
                        sum_dec_ratio.append(st["decode_s"] / w)
                        ratios.extend(st.get("_ratios") or [])
                if walls:
                    layer_rows.append({
                        "pass": pass_id, "lane": lane, "arm": arm, "class": klass, "cases": len(walls), "cases_with_vllm_spans": joined,
                        "wall_s_p50": round(statistics.median(walls), 1),
                        "graph_share": round(statistics.mean(graph), 3), "agent_share": round(statistics.mean(agent), 3),
                        "host_share": round(statistics.mean(host), 3) if host else "", "prefill_share": round(statistics.mean(prefill), 3) if prefill else "",
                        "decode_share": round(statistics.mean(decode), 3) if decode else "",
                        "decode_ratio_per_request_p50": round(statistics.median(ratios), 3) if ratios else "",
                        "sum_decode_over_e2e_p50": round(statistics.median(sum_dec_ratio), 3) if sum_dec_ratio else "",
                    })

    def write(name: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        with (out_dir / f"{name}.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        md = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        md += "".join("| " + " | ".join(str(r[c]) for c in cols) + " |\n" for r in rows)
        (out_dir / f"{name}.md").write_text(md, encoding="utf-8")

    write("turn-summary", turn_rows)
    write("layer-shares", layer_rows)
    print(json.dumps({"turn_rows": len(turn_rows), "layer_rows": len(layer_rows), "output_dir": str(out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
