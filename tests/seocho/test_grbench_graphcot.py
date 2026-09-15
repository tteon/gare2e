"""Graph-CoT hop actions (lab 017): templates pass the read policy, dispatch binds parameters, receipts stay content-free."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from seocho.eval.agent_execution import GRAPH_QUERY_TOOLS, build_retrieval_funnel
from seocho.eval.agentic_os_artifacts import canonical_digest
from seocho.eval.grbench_graphcot import HOP_TOOL_NAMES, RELATIONS, GraphCoTTools, graph_description, graphcot_tools, templates
from seocho.eval.grbench_runtime import QueryPolicyError, validate_grbench_read_query

ROOT = Path(__file__).resolve().parents[2]


class _FakeExecutor:
    def __init__(self, rows_by_call=None):
        self.calls: list[tuple[str, dict]] = []
        self.rows_by_call = list(rows_by_call or [])

    def execute_rows(self, query, parameters=None):
        validate_grbench_read_query(query, row_cap=20, require_evidence_columns=False, require_anchor=True)
        self.calls.append((query, dict(parameters or {})))
        rows = self.rows_by_call.pop(0) if self.rows_by_call else 1
        return "[]", {"rows": rows, "result_bytes": 2, "wall_ms": 1.0, "query_digest": "x"}


def test_every_template_passes_the_read_policy_with_anchor_and_scope():
    for name, text in templates().items():
        out = validate_grbench_read_query(text, row_cap=20, require_evidence_columns=False, require_anchor=True)
        assert "LIMIT" in out, name
        assert out.count("_workspace_id: $workspace_id") == out.count(":GRBenchNode"), name


def test_dispatch_binds_parameters_and_falls_back_to_contains_only_when_exact_is_empty():
    ex = _FakeExecutor(rows_by_call=[0, 2])
    hop = GraphCoTTools(ex, retrieve_cap=10)
    _, receipt = hop.call("RetrieveNode", {"text": "Some Paper Title"})
    assert [c[1]["text"] for c in ex.calls] == ["some paper title", "some paper title"]
    assert "CONTAINS" in ex.calls[1][0] and receipt["match"] == "contains"
    ex2 = _FakeExecutor(rows_by_call=[1])
    GraphCoTTools(ex2).call("RetrieveNode", {"text": "x y"})
    assert len(ex2.calls) == 1  # exact hit: no fallback
    ex3 = _FakeExecutor()
    hop3 = GraphCoTTools(ex3)
    hop3.call("NeighbourCheck", {"node_id": "42", "relation": "author"})
    hop3.call("NodeDegree", {"node_id": "42", "relation": "cited_by"})
    hop3.call("NodeFeature", {"node_id": "42", "feature": "title"})
    assert ex3.calls[0][1] == {"node_id": "42", "relation": "author"}
    assert ex3.calls[2][1] == {"node_id": "42", "prop": "feature_title"}
    assert "count(m)" in ex3.calls[1][0]


def test_dispatch_rejects_bad_arguments_with_policy_codes():
    hop = GraphCoTTools(_FakeExecutor())
    with pytest.raises(QueryPolicyError, match="unknown_relation"):
        hop.call("NeighbourCheck", {"node_id": "1", "relation": "friend_of"})
    with pytest.raises(QueryPolicyError, match="unknown_feature"):
        hop.call("NodeFeature", {"node_id": "1", "feature": "salary"})
    with pytest.raises(QueryPolicyError, match="missing_argument"):
        hop.call("NodeDegree", {"relation": "author"})
    with pytest.raises(ValueError):
        hop.call("execute_cypher", {"query": "MATCH (n) RETURN n"})


def test_tool_schema_and_funnel_recognise_hop_actions():
    tools = graphcot_tools()
    assert tuple(t["function"]["name"] for t in tools) == HOP_TOOL_NAMES
    assert set(HOP_TOOL_NAMES) <= GRAPH_QUERY_TOOLS
    digest = canonical_digest({"tools": list(tools)})
    assert len(digest) == 64
    events = [{"name": "RetrieveNode", "outcome": "ok", "rows": 2}, {"name": "NeighbourCheck", "outcome": "ok", "rows": 5}]
    funnel = build_retrieval_funnel(turns=[{"output_kind": "tool_calls"}], tools=events, success=True, evidence_class="graph", query_validated=True, query_executed=True, result_rows=7)
    assert funnel["retrieval_decision"] == "graph_tool" and funnel["graph_query_calls"] == 2 and funnel["nonempty_result"]


def test_graph_description_carries_direction_and_o1_semantics():
    base = graph_description(semantics=False)
    rich = graph_description(semantics=True)
    assert "reference (paper->paper)" in base and "meanings" not in base
    assert all(RELATIONS[k][2] in rich for k in RELATIONS)
    assert "cited_by" in rich and "Citations are stored in both directions" in rich


def test_coordinator_builds_the_graphcot_command_without_a_discovery_cache(tmp_path):
    spec = importlib.util.spec_from_file_location("coord", ROOT / "scripts" / "benchmarks" / "run_agentic_os_schedule.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    args = SimpleNamespace(lane="grbench-graphcot", base_url="http://x", model="m", enable_thinking="false", max_output_tokens=512, gpu_sampling="appliance", metrics_url=None, dataset=tmp_path / "q.json", discovery_cache=tmp_path / "d.json", max_turns=12, token_budget=16384, condition="tool_auto", db_pid=7, profile_query_plans=False, prompt_profile="baseline", grbench_workspace_id="grbench-maple-physics", row_cap=None, byte_cap=None)
    entry = {"arm": "O1", "seed": 0, "repetition": 0, "cache_phase": "warm", "qid": "84"}
    cmd = mod._runner_command(args, entry, tmp_path, "run")
    assert cmd[1].endswith("run_grbench_graphcot_react.py")
    assert "--discovery-cache" not in cmd and cmd[cmd.index("--arm") + 1] == "O1" and cmd[cmd.index("--max-turns") + 1] == "12"
    assert mod._LANE_ARMS["grbench-graphcot"] == {"B0", "O1"}
