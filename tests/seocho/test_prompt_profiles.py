from __future__ import annotations

import pytest

from seocho.eval.prompt_profiles import PROMPT_PROFILES, prompt_profile_receipt, system_prompt_addendum


def test_baseline_profile_adds_nothing_and_strengthened_states_scope_rule() -> None:
    assert PROMPT_PROFILES == ("baseline", "strengthened")
    assert system_prompt_addendum("baseline", "cypher") == ""
    cypher = system_prompt_addendum("strengthened", "cypher")
    sparql = system_prompt_addendum("strengthened", "sparql")
    assert "_workspace_id: $workspace_id" in cypher and "missing_workspace_scope" in cypher
    assert "?s ?p ?o" in sparql and "LIMIT" in sparql
    assert "Finalisation rule" in cypher and "Finalisation rule" in sparql


def test_profile_receipt_is_content_free_and_stable() -> None:
    a = prompt_profile_receipt("strengthened", "cypher")
    b = prompt_profile_receipt("strengthened", "cypher")
    assert a == b and a["prompt_profile"] == "strengthened" and len(a["prompt_profile_addendum_sha256"]) == 64
    assert prompt_profile_receipt("baseline", "sparql")["prompt_profile_addendum_sha256"] != a["prompt_profile_addendum_sha256"]
    with pytest.raises(ValueError):
        system_prompt_addendum("bogus", "cypher")
    with pytest.raises(ValueError):
        system_prompt_addendum("strengthened", "gremlin")


def test_react_loop_appends_system_addendum() -> None:
    import importlib.util
    from pathlib import Path

    from seocho.eval.agentic_graph_agents import run_gtsqa_text2sparql_react

    spec = importlib.util.spec_from_file_location("test_agentic_graph_agents", Path(__file__).with_name("test_agentic_graph_agents.py"))
    helpers = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(helpers)
    _Executor, _Model, _case = helpers._Executor, helpers._Model, helpers._case

    seen: list[str] = []

    class Capture(_Model):
        def complete(self, *, messages, tools):
            seen.append(messages[0]["content"])
            return super().complete(messages=messages, tools=tools)

    run_gtsqa_text2sparql_react(_case(), model=Capture(), executor_factory=_Executor, system_addendum=" ADDENDUM-MARKER")
    assert seen and seen[0].endswith("ADDENDUM-MARKER")
