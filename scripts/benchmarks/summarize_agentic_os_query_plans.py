"""Operator / db-hit table from the PROFILE replays in GRBench run receipts (lab 021).

Reads ``cases[].query_plan_replays[]`` (content-free: operator names, rows, db hits,
page-cache counters, audit findings; query digests only) and writes, per pass x arm
x template family and per stratum:

* how many replays, replay wall p50/p90, server ms;
* the operator mix (share of plans whose *root-most seek* is NodeIndexSeek /
  NodeIndexSeekByRange / NodeIndexContainsScan / NodeByLabelScan / AllNodesScan,
  and whether an Expand(All) into a hub follows);
* total db hits and page-cache misses p50/p90;
* audit findings counts (global_node_scan, variable_length_expansion, …).

    python3 scripts/benchmarks/summarize_agentic_os_query_plans.py <run-root> --strata <strata-dir> [--labels <difficulty.jsonl>]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

SEEK_KINDS = ("NodeIndexSeek", "NodeUniqueIndexSeek", "NodeIndexSeekByRange", "NodeIndexContainsScan", "NodeIndexEndsWithScan", "NodeIndexScan", "NodeByLabelScan", "AllNodesScan")


def anchor_kind(operators: list[dict]) -> str:
    names = [str(o.get("operator", "")) for o in operators]
    for kind in SEEK_KINDS:
        if any(kind in n for n in names):
            return kind
    return "other"


def _q(v, p):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * p))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_root", type=Path)
    ap.add_argument("--strata", type=Path, required=True)
    ap.add_argument("--labels", type=Path, help="grbench-difficulty.jsonl (qid, family, stratum); defaults to <strata>/grbench-difficulty.jsonl")
    args = ap.parse_args()
    labels_path = args.labels or (args.strata / "grbench-difficulty.jsonl")
    labels = {str(json.loads(l)["qid"]): json.loads(l) for l in labels_path.read_text(encoding="utf-8").splitlines() if l.strip()}
    rows = []
    for pass_dir in sorted(args.run_root.glob("run-*")):
        for f in glob.glob(str(pass_dir / "e2e" / "grbench-single-tool-*" / "shard-*" / "attempts" / "*" / "run-receipt.json")):
            for c in json.loads(Path(f).read_text(encoding="utf-8")).get("cases", []):
                lab = labels.get(str(c.get("qid")), {})
                for r in c.get("query_plan_replays") or []:
                    plan = r.get("plan") or {}
                    ops = plan.get("operators") or []
                    rows.append({
                        "pass": pass_dir.name[4:], "arm": c.get("arm"), "qid": str(c.get("qid")), "family": lab.get("family"), "stratum": lab.get("stratum"),
                        "anchor_kind": anchor_kind(ops), "has_expand": any("Expand" in str(o.get("operator", "")) for o in ops),
                        "operators": len(ops), "total_db_hits": plan.get("total_db_hits"), "page_cache_misses": plan.get("total_page_cache_misses"),
                        "max_actual_rows": max((int(o.get("actual_rows") or 0) for o in ops), default=0),
                        "wall_ms": r.get("wall_ms"), "server_available_ms": r.get("server_available_ms"), "findings": ";".join(plan.get("findings") or []), "error_class": r.get("error_class"),
                    })
    out = args.run_root / "session-summary"
    out.mkdir(exist_ok=True)
    if not rows:
        print(json.dumps({"replays": 0}))
        return 0
    with (out / "query-plans.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    md = ["| pass | arm | group | replays | anchor kinds | expand share | db hits p50 / p90 | pc misses p50 / p90 | server ms p50 / p90 | findings |", "|---|---|---|---|---|---|---|---|---|---|"]
    def emit(key_fn, label):
        groups: dict = defaultdict(list)
        for r in rows:
            if r["error_class"]:
                continue
            groups[(r["pass"], r["arm"], key_fn(r))].append(r)
        for (p, arm, g), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            kinds = Counter(r["anchor_kind"] for r in rs)
            dbh = [r["total_db_hits"] for r in rs if isinstance(r["total_db_hits"], (int, float))]
            pcm = [r["page_cache_misses"] for r in rs if isinstance(r["page_cache_misses"], (int, float))]
            srv = [r["server_available_ms"] for r in rs if isinstance(r["server_available_ms"], (int, float))]
            fnd = Counter(x for r in rs for x in r["findings"].split(";") if x)
            md.append(f"| {p} | {arm} | {label}={g} | {len(rs)} | {', '.join(f'{k} {v}' for k, v in kinds.most_common(4))} | {sum(r['has_expand'] for r in rs)/len(rs):.2f} | {_q(dbh,.5)} / {_q(dbh,.9)} | {_q(pcm,.5)} / {_q(pcm,.9)} | {_q(srv,.5)} / {_q(srv,.9)} | {', '.join(f'{k} {v}' for k, v in fnd.most_common(4))} |")
    emit(lambda r: r["stratum"], "stratum")
    emit(lambda r: r["family"], "family")
    errors = Counter((r["pass"], r["error_class"]) for r in rows if r["error_class"])
    md.append("\n**Replay errors:** " + (", ".join(f"{p} {e}={n}" for (p, e), n in errors.items()) or "none"))
    (out / "query-plans.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"replays": len(rows), "errors": sum(errors.values()), "output": str(out / "query-plans.md")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
