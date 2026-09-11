"""Graph-CoT hop actions over the GRBench DozerDB projection (lab 017).

The original Graph-CoT agent (Jin et al.) walks the graph one hop at a time
with four actions instead of writing a whole query::

    RetrieveNode(text)              -> node ids whose name / title matches
    NodeFeature(node_id, feature)   -> one property of a node
    NeighbourCheck(node_id, relation) -> ids of neighbours along one relation
    NodeDegree(node_id, relation)   -> how many such neighbours exist

Here every action is a fixed, parameterised Cypher template that runs through
the same :class:`GRBenchDozerExecutor` boundary as the Text2Cypher lane (same
validator, workspace scope, 15 s Bolt timeout, row / byte caps).  The model
never writes Cypher; the receipt records the same content-free fields as the
Text2Cypher tool events so the funnel, bottleneck and turn summaries apply
unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError

NODE_TYPES = ("paper", "author", "venue")
RELATIONS = {
    "author": ("paper", "author", "the paper was written by the author"),
    "paper": ("author", "paper", "the author wrote the paper"),
    "venue": ("paper", "venue", "the paper appeared at the venue"),
    "reference": ("paper", "paper", "the source paper cites the target paper"),
    "cited_by": ("paper", "paper", "the target paper cites the source paper"),
}
FEATURES = {
    "name": "feature_name",
    "title": "feature_title",
    "year": "feature_year",
    "label": "feature_label",
    "abstract": "feature_abstract",
    "node_type": "node_type",
}
HOP_TOOL_NAMES = ("RetrieveNode", "NodeFeature", "NeighbourCheck", "NodeDegree")

_SCOPED = "(n:GRBenchNode {_workspace_id: $workspace_id, id: $node_id})"
_TEMPLATES = {
    "retrieve_exact": (
        "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
        "WHERE n.feature_name = $text OR n.feature_title = $text "
        "RETURN n.id AS id, n.node_type AS node_type, coalesce(n.feature_name, n.feature_title) AS name"
    ),
    "retrieve_contains": (
        "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
        "WHERE n.feature_title CONTAINS $text OR n.feature_name CONTAINS $text "
        "RETURN n.id AS id, n.node_type AS node_type, coalesce(n.feature_name, n.feature_title) AS name"
    ),
    "feature": f"MATCH {_SCOPED} RETURN n.node_type AS node_type, n[$prop] AS value",
    "neighbours": (
        f"MATCH {_SCOPED}-[r:GRBENCH_REL {{relation_name: $relation}}]->(m:GRBenchNode {{_workspace_id: $workspace_id}}) "
        "RETURN m.id AS id, m.node_type AS node_type, coalesce(m.feature_name, m.feature_title) AS name"
    ),
    "degree": (
        f"MATCH {_SCOPED}-[r:GRBENCH_REL {{relation_name: $relation}}]->(m:GRBenchNode {{_workspace_id: $workspace_id}}) "
        "RETURN count(m) AS degree"
    ),
}


def graphcot_tools() -> tuple[dict[str, Any], ...]:
    """The exact native tool schema presented to the Graph-CoT model."""
    rel_list = ", ".join(f"{name} ({src}->{dst})" for name, (src, dst, _) in RELATIONS.items())
    return (
        {
            "type": "function",
            "function": {
                "name": "RetrieveNode",
                "description": (
                    "Find graph nodes whose exact name (author, venue) or exact title (paper) equals the "
                    "given text; falls back to a substring match when nothing matches exactly. Returns up to "
                    "10 candidates as id, node_type, name. Call this first to anchor on the entities the "
                    "question names; never guess ids."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "name or title, lower-case as written in the question"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "NodeFeature",
                "description": (
                    "Return one property of a node by id. feature is one of name, title, year, label, "
                    "abstract, node_type."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "feature": {"type": "string", "enum": sorted(FEATURES)},
                    },
                    "required": ["node_id", "feature"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "NeighbourCheck",
                "description": (
                    "List the neighbours of a node along one relation (up to the row cap) as id, node_type, "
                    f"name. Relations and their direction: {rel_list}. Use NodeDegree when only the count matters."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "relation": {"type": "string", "enum": sorted(RELATIONS)},
                    },
                    "required": ["node_id", "relation"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "NodeDegree",
                "description": "Count the neighbours of a node along one relation (exact, not capped).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "relation": {"type": "string", "enum": sorted(RELATIONS)},
                    },
                    "required": ["node_id", "relation"],
                    "additionalProperties": False,
                },
            },
        },
    )


def graph_description(*, semantics: bool) -> str:
    """Prompt text describing the graph. ``semantics`` adds the O1 relation meanings."""
    lines = [
        "The graph is a physics literature graph with node types paper, author and venue.",
        "Node properties: papers have title, year, label (subject labels), abstract; authors and venues have name.",
        "Relations (direction source->target): " + ", ".join(f"{k} ({v[0]}->{v[1]})" for k, v in RELATIONS.items()) + ".",
    ]
    if semantics:
        lines.append("Relation meanings: " + "; ".join(f"{k}: {v[2]}" for k, v in RELATIONS.items()) + ".")
        lines.append(
            "Citations are stored in both directions: from the citing paper use reference; from the cited paper use cited_by. "
            "Names and titles are stored lower-case; RetrieveNode matches them exactly first."
        )
    return " ".join(lines)


class GraphCoTTools:
    """Dispatch a hop action to its template through the single execution boundary."""

    def __init__(self, executor: GRBenchDozerExecutor, *, retrieve_cap: int = 10) -> None:
        self.executor = executor
        self.retrieve_cap = retrieve_cap

    def call(self, name: str, arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        if name == "RetrieveNode":
            text = str(arguments.get("text", "")).strip().casefold()
            if not text:
                raise QueryPolicyError("missing_argument")
            observation, receipt = self.executor.execute_rows(f"{_TEMPLATES['retrieve_exact']} LIMIT {self.retrieve_cap}", {"text": text})
            receipt["match"] = "exact"
            if receipt.get("rows", 0) == 0 and len(text) >= 3:
                observation, second = self.executor.execute_rows(f"{_TEMPLATES['retrieve_contains']} LIMIT {self.retrieve_cap}", {"text": text})
                second["match"] = "contains"
                second["wall_ms"] = round(receipt["wall_ms"] + second["wall_ms"], 3)
                receipt = second
            return observation, receipt
        node_id = str(arguments.get("node_id", "")).strip()
        if not node_id:
            raise QueryPolicyError("missing_argument")
        if name == "NodeFeature":
            prop = FEATURES.get(str(arguments.get("feature", "")).strip())
            if prop is None:
                raise QueryPolicyError("unknown_feature")
            return self.executor.execute_rows(_TEMPLATES["feature"], {"node_id": node_id, "prop": prop})
        relation = str(arguments.get("relation", "")).strip()
        if relation not in RELATIONS:
            raise QueryPolicyError("unknown_relation")
        if name == "NeighbourCheck":
            return self.executor.execute_rows(_TEMPLATES["neighbours"], {"node_id": node_id, "relation": relation})
        if name == "NodeDegree":
            return self.executor.execute_rows(_TEMPLATES["degree"], {"node_id": node_id, "relation": relation})
        raise ValueError("unrecognized tool")


def templates() -> dict[str, str]:
    """Expose the templates for validation tests."""
    return dict(_TEMPLATES)


def tool_schema_digest_payload() -> dict[str, Any]:
    return {"tools": list(graphcot_tools())}


__all__ = ["FEATURES", "HOP_TOOL_NAMES", "NODE_TYPES", "RELATIONS", "GraphCoTTools", "graph_description", "graphcot_tools", "templates", "tool_schema_digest_payload"]
