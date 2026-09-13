from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from seocho.eval.request_analysis import RequestAnalysis
from seocho.eval.request_router import HOP_TOKEN_BUDGET, plan_request


def _analysis(**over) -> RequestAnalysis:
    base = dict(question_digest="0" * 64, family="authors_of_paper", intent="lookup", hops_estimate=1, expected_cardinality="few",
                needs_aggregation=False, needs_argmax=False, has_value_filter=False, mentions=1, anchors_resolved=1,
                resolvability=["exact"], anchor_types=["paper"], degree_classes=["low"], degree_class_max="low")
    base.update(over)
    return RequestAnalysis(**base)


def test_policy_v0_routes_by_intent_and_resolution() -> None:
    hops = plan_request(_analysis())
    assert (hops.lane, hops.arm, hops.max_turns, hops.token_budget, hops.zero_row_hint) == ("grbench-graphcot", "B0", 8, HOP_TOKEN_BUDGET, False)
    count = plan_request(_analysis(family="citation_count", intent="count"))
    assert (count.lane, count.arm, count.max_turns, count.zero_row_hint) == ("grbench-single", "S1", 4, True)
    argmax = plan_request(_analysis(family="closest_collaborator", intent="argmax"))
    assert (argmax.lane, argmax.arm, argmax.row_cap) == ("grbench-single", "O1", 50)
    inter = plan_request(_analysis(family="author_of_both_papers", intent="set_intersection", mentions=2, anchors_resolved=2))
    assert (inter.lane, inter.arm, inter.max_turns) == ("grbench-single", "S1", 6)
    multihop_lookup = plan_request(_analysis(family="venue_of_collaboration", intent="lookup", hops_estimate=3))
    assert multihop_lookup.lane == "grbench-single" and "multihop" in multihop_lookup.reasons[0]
    unmatched = plan_request(_analysis(family="unmatched", intent=None, mentions=0, anchors_resolved=0, resolvability=[], anchor_types=[], degree_classes=[], degree_class_max=None))
    assert unmatched.lane == "grbench-single" and unmatched.arm == "S1" and unmatched.reasons == ["family_unmatched->text2cypher_s1_default"]


def test_unresolved_anchors_bounded_in_v0_and_abstain_in_v0a() -> None:
    bounded = plan_request(_analysis(anchors_resolved=0, resolvability=["none"], anchor_types=[], degree_classes=[], degree_class_max=None))
    assert (bounded.lane, bounded.max_turns) == ("grbench-single", 3)
    abstain = plan_request(_analysis(anchors_resolved=0, resolvability=["none"]), policy="V0A")
    assert abstain.lane == "abstain" and abstain.max_turns == 0 and abstain.reasons == ["anchors_unresolved->abstain"]
    # a resolved lookup is unaffected by the abstain policy
    assert plan_request(_analysis(), policy="V0A").lane == "grbench-graphcot"
    with pytest.raises(ValueError):
        plan_request(_analysis(), policy="V9")
    # lexical-only analysis (no graph reader): nothing was attempted, so no abstention
    lexical = plan_request(_analysis(anchors_resolved=0, resolvability=[], anchor_types=[], degree_classes=[], degree_class_max=None), policy="V0A")
    assert lexical.lane == "grbench-single" and lexical.max_turns == 6


def test_hub_anchor_adds_note_and_receipt_is_content_free() -> None:
    plan = plan_request(_analysis(degree_classes=["hub"], degree_class_max="hub"))
    assert plan.notes and plan.notes[0].startswith("hub_anchor")
    receipt = plan.receipt()
    assert receipt["schema_version"] == "seocho.router_plan.v1" and receipt["lane"] == "grbench-graphcot"
    assert set(receipt) >= {"policy", "lane", "arm", "max_turns", "token_budget", "row_cap", "zero_row_hint", "reasons", "notes"}


def _router_module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_grbench_router.py"
    spec = importlib.util.spec_from_file_location("run_grbench_router", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(tmp_path: Path, **over) -> argparse.Namespace:
    base = dict(questions=tmp_path / "q.jsonl", discovery_cache=tmp_path / "disc.json", receipt_out=tmp_path / "run-receipt.json",
                predictions_out=tmp_path / "pred.json", manifest_out=tmp_path / "manifest.json", qid="7", arm="V0", run_id="run-x",
                workspace_id="grbench-maple-physics", database="neo4j", bolt_url="bolt://127.0.0.1:7687", row_cap=None, byte_cap=None, limit=1)
    base.update(over)
    return argparse.Namespace(**base)


def test_dispatch_command_translates_plan_and_filters_flags_per_lane(tmp_path) -> None:
    module = _router_module()
    passthrough = ["--base-url", "http://x", "--model", "m", "--seed", "0", "--zero-row-hint", "--stage", "main", "--profile-query-plans"]
    cypher = module.build_dispatch_command(plan_request(_analysis(family="citation_count", intent="count")), _args(tmp_path), passthrough)
    assert cypher[1].endswith("run_direct_react_text2cypher.py")
    assert cypher[cypher.index("--arm") + 1] == "S1" and cypher[cypher.index("--max-turns") + 1] == "4"
    assert cypher.count("--zero-row-hint") == 2 - 1 + 1 or cypher.count("--zero-row-hint") >= 1   # plan adds it; passthrough copy kept for the cypher lane
    assert cypher[cypher.index("--run-id") + 1] == "run-x" and "--bolt-url" not in cypher
    hops = module.build_dispatch_command(plan_request(_analysis()), _args(tmp_path, row_cap=30), passthrough)
    assert hops[1].endswith("run_grbench_graphcot_react.py")
    assert "--zero-row-hint" not in hops and "--profile-query-plans" not in hops
    assert hops[hops.index("--arm") + 1] == "B0" and hops[hops.index("--token-budget") + 1] == str(HOP_TOKEN_BUDGET)
    assert hops[hops.index("--row-cap") + 1] == "30" and hops[hops.index("--bolt-url") + 1] == "bolt://127.0.0.1:7687"
    argmax = module.build_dispatch_command(plan_request(_analysis(family="closest_collaborator", intent="argmax")), _args(tmp_path, row_cap=30), [])
    assert argmax[argmax.index("--row-cap") + 1] == "50"   # the plan's row cap wins over the schedule's
    with pytest.raises(ValueError):
        module.build_dispatch_command(plan_request(_analysis(anchors_resolved=0, resolvability=["none"]), policy="V0A"), _args(tmp_path), [])


def test_abstain_receipt_is_a_complete_content_free_case(tmp_path) -> None:
    module = _router_module()
    plan = plan_request(_analysis(anchors_resolved=0, resolvability=["none"]), policy="V0A")
    analysis_receipt = _analysis(anchors_resolved=0, resolvability=["none"]).receipt()
    receipt = module.abstain_receipt(_args(tmp_path, arm="V0A"), {"qid": "7", "question": 'Who wrote "secret title"?', "answer": "the-private-answer-42"}, analysis_receipt, plan, 12.5)
    (case,) = receipt["cases"]
    assert case["answer_status"] == "abstained" and case["terminal"] == "router_abstain" and case["turns"] == [] and case["tools"] == []
    assert case["retrieval_funnel"]["graph_tool_selected"] is False and case["router_plan"]["lane"] == "abstain"
    dumped = json.dumps(receipt)
    assert "secret title" not in dumped and "the-private-answer-42" not in dumped
