from __future__ import annotations

import pytest

from seocho.eval.raw_agentic_baseline import openai_tool_fields, raw_condition_spec


_TOOLS = (
    {"type": "function", "function": {"name": "inspect_graph_schema"}},
    {"type": "function", "function": {"name": "execute_sparql"}},
)


def test_closed_book_has_no_tool_surface() -> None:
    spec = raw_condition_spec("closed_book", stage="smoke")
    assert spec.evidence_class == "behavioral_baseline"
    assert openai_tool_fields(
        spec,
        tools=_TOOLS,
        schema_observed=False,
        query_executed=False,
        schema_tool_name="inspect_rdf_schema",
        query_tool_name="execute_sparql",
    ) == {}


def test_forced_plumbing_advances_schema_query_then_auto() -> None:
    spec = raw_condition_spec("forced_plumbing", stage="smoke")
    inspect = openai_tool_fields(
        spec,
        tools=_TOOLS,
        schema_observed=False,
        query_executed=False,
        schema_tool_name="inspect_rdf_schema",
        query_tool_name="execute_sparql",
    )
    query = openai_tool_fields(
        spec,
        tools=_TOOLS,
        schema_observed=True,
        query_executed=False,
        schema_tool_name="inspect_rdf_schema",
        query_tool_name="execute_sparql",
    )
    final = openai_tool_fields(
        spec,
        tools=_TOOLS,
        schema_observed=True,
        query_executed=True,
        schema_tool_name="inspect_rdf_schema",
        query_tool_name="execute_sparql",
    )
    assert inspect["tool_choice"]["function"]["name"] == "inspect_rdf_schema"
    assert query["tool_choice"]["function"]["name"] == "execute_sparql"
    assert final["tool_choice"] == "auto"


def test_forced_plumbing_cannot_enter_main_results() -> None:
    with pytest.raises(ValueError, match="smoke-only"):
        raw_condition_spec("forced_plumbing", stage="main")
