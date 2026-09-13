"""GRBench router lane (lab 019): analyse the request, choose a lane, dispatch.

Command-line shape mirrors ``run_direct_react_text2cypher.py`` so the schedule
coordinator can treat ``grbench-router`` like any other lane.  ``--arm`` is the
router *policy* (V0 / V0A); the chosen context arm (S1 / O1 / B0) is passed to
the dispatched runner.  After the runner finishes, its ``run-receipt.json`` and
``manifest.json`` gain content-free ``request_analysis`` and ``router_plan``
blocks so every decision is auditable.  An ``abstain`` plan writes a synthetic
receipt with no LLM call.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from seocho.eval.grbench_runtime import GRBenchDozerExecutor  # noqa: E402
from seocho.eval.request_analysis import analyze_request  # noqa: E402
from seocho.eval.request_router import POLICIES, RouterPlan, plan_request  # noqa: E402

RUNNERS = {
    "grbench-single": _REPO_ROOT / "scripts/benchmarks/run_direct_react_text2cypher.py",
    "grbench-graphcot": _REPO_ROOT / "scripts/benchmarks/run_grbench_graphcot_react.py",
}
# Flags the dispatched runner does not accept for the chosen lane.
_DROP_FOR_LANE = {"grbench-graphcot": {"--zero-row-hint", "--profile-query-plans"}}


def _labeler():
    path = _REPO_ROOT / "scripts/benchmarks/label_agentic_os_difficulty.py"
    spec = importlib.util.spec_from_file_location("label_agentic_os_difficulty", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_question(path: Path, qid: str) -> dict[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if str(item.get("qid")) == str(qid):
            return item
    raise SystemExit(f"qid {qid} not found in {path}")


def build_dispatch_command(plan: RouterPlan, args: argparse.Namespace, passthrough: list[str]) -> list[str]:
    """Translate the router's plan into the dispatched runner's command line."""
    if plan.lane not in RUNNERS:
        raise ValueError(f"no runner for lane {plan.lane}")
    command = [
        sys.executable, str(RUNNERS[plan.lane]),
        "--questions", str(args.questions),
        "--receipt-out", str(args.receipt_out),
        "--predictions-out", str(args.predictions_out),
        "--qid", str(args.qid),
        "--arm", str(plan.arm),
        "--max-turns", str(plan.max_turns),
        "--token-budget", str(plan.token_budget),
        "--workspace-id", args.workspace_id,
        "--database", args.database,
        "--run-id", args.run_id,
        "--limit", str(args.limit),
    ]
    if args.manifest_out is not None:
        command.extend(("--manifest-out", str(args.manifest_out)))
    if args.discovery_cache is not None:
        command.extend(("--discovery-cache", str(args.discovery_cache)))
    if plan.row_cap is not None:
        command.extend(("--row-cap", str(plan.row_cap)))
    elif args.row_cap is not None:
        command.extend(("--row-cap", str(args.row_cap)))
    if args.byte_cap is not None:
        command.extend(("--byte-cap", str(args.byte_cap)))
    if plan.lane == "grbench-graphcot":
        command.extend(("--bolt-url", args.bolt_url))
    if plan.zero_row_hint and plan.lane == "grbench-single":
        command.append("--zero-row-hint")
    drop = _DROP_FOR_LANE.get(plan.lane, set())
    command.extend(tok for tok in passthrough if tok not in drop)
    return command


def abstain_receipt(args: argparse.Namespace, item: dict[str, Any], analysis_receipt: dict[str, Any], plan: RouterPlan, router_ms: float) -> dict[str, Any]:
    case = {
        "case_id": str(item["qid"]), "qid": str(item["qid"]), "arm": plan.arm, "role": "router",
        "answer_status": "abstained", "terminal": "router_abstain", "success": False,
        "turn_count": 0, "turns": [], "tools": [], "repair_count": 0, "query_retry_count": 0, "empty_result_retry_count": 0,
        "retrieval_funnel": {
            "schema_version": "seocho.retrieval_funnel.v1", "retrieval_decision": "router_abstain", "graph_tool_selected": False,
            "schema_discovery_calls": 0, "graph_query_calls": 0, "query_validated": False, "query_executed": False,
            "nonempty_result": False, "result_rows": 0, "effective_retrieval": False, "forced_tool": False,
            "llm_turns_before_first_graph_tool": None, "time_to_first_graph_tool_ms": None, "gold_evidence_retrieved": None,
            "evidence_class": "router_abstain",
        },
        "time": {"case_execution_wall_ms": round(router_ms, 3)},
        "request_analysis": analysis_receipt, "router_plan": plan.receipt(),
    }
    return {
        "schema_version": "seocho.agentic_os.router_receipt.v1",
        "execution_surface": "router_abstain_no_llm_call",
        "config": {"lane": "grbench-router", "policy": plan.policy, "run_id": args.run_id, "workspace_id": args.workspace_id},
        "router": {"policy": plan.policy, "dispatched_lane": "abstain"},
        "cases": [case],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--predictions-out", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--qid", required=True, help="one question per invocation (the schedule coordinator's unit of work)")
    parser.add_argument("--arm", choices=POLICIES, default="V0", help="router policy")
    parser.add_argument("--run-id", default="router-smoke")
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--bolt-url", default="bolt://127.0.0.1:7687")
    parser.add_argument("--row-cap", type=int)
    parser.add_argument("--byte-cap", type=int)
    parser.add_argument("--limit", type=int, default=1, help="accepted for parity; the router handles one qid")
    parser.add_argument("--max-turns", type=int, help="accepted for parity; the plan sets the turn budget")
    parser.add_argument("--token-budget", type=int, help="accepted for parity; the plan sets the token budget")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the dispatch command; do not run")
    args, passthrough = parser.parse_known_args()

    started = time.perf_counter()
    item = load_question(args.questions, args.qid)
    label = _labeler().label_grbench_question(args.qid, item["question"])
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    driver = None
    reader = None
    if password and not args.dry_run:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(args.bolt_url, auth=("neo4j", password))
        executor = GRBenchDozerExecutor(driver, database=args.database, workspace_id=args.workspace_id, row_cap=20, require_evidence_columns=False)
        reader = executor._diagnostic_rows
    analysis = analyze_request(item["question"], family=label.get("family"), run=reader)
    if driver is not None:
        driver.close()
    plan = plan_request(analysis, policy=args.arm)
    router_ms = (time.perf_counter() - started) * 1000
    analysis_receipt = analysis.receipt()
    analysis_receipt["labeler_stratum"] = label.get("stratum")

    if plan.lane == "abstain":
        receipt = abstain_receipt(args, item, analysis_receipt, plan, router_ms)
        if args.dry_run:
            print(json.dumps({"plan": plan.receipt(), "analysis": analysis_receipt}, sort_keys=True))
            return 0
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        args.predictions_out.write_text(json.dumps({str(item["qid"]): {"prediction": "", "answer": item.get("answer")}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.manifest_out is not None:
            args.manifest_out.write_text(json.dumps({"schema_version": "seocho.agentic_os.router_manifest.v1", "lane": "grbench-router", "arm": args.arm, "routed_lane": "abstain", "run_id": args.run_id, "question_file_sha256": hashlib.sha256(args.questions.read_bytes()).hexdigest(), "router": plan.receipt()}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"cases": 1, "final": 0, "routed": "abstain"}, sort_keys=True))
        return 0

    command = build_dispatch_command(plan, args, passthrough)
    if args.dry_run:
        print(json.dumps({"plan": plan.receipt(), "analysis": analysis_receipt, "command": command}, sort_keys=True))
        return 0
    completed = subprocess.run(command, check=False)
    if args.receipt_out.exists():
        receipt = json.loads(args.receipt_out.read_text(encoding="utf-8"))
        receipt["router"] = {"policy": plan.policy, "dispatched_lane": plan.lane, "router_ms": round(router_ms, 3), "exit_code": completed.returncode}
        for case in receipt.get("cases", []):
            case["request_analysis"] = analysis_receipt
            case["router_plan"] = plan.receipt()
        args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.manifest_out is not None and args.manifest_out.exists():
        manifest = json.loads(args.manifest_out.read_text(encoding="utf-8"))
        manifest.update({"lane": "grbench-router", "routed_lane": plan.lane, "routed_arm": plan.arm, "arm": args.arm, "router": plan.receipt()})
        args.manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
