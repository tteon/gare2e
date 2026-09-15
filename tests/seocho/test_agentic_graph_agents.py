from __future__ import annotations

import hashlib
import json

from agents.agent_output import AgentOutputSchema

from seocho.eval.agentic_graph_agents import (
    EvidenceHandoff,
    HandoffReceiptRecorder,
    NativeModelReply,
    NativeToolCall,
    build_agents_sdk_agent_tool_workflow,
    build_agents_sdk_handoff_workflow,
    classify_answer_status,
    gtsqa_react_tools,
    redacted_episode_error,
    run_gtsqa_closed_book,
    run_gtsqa_text2sparql_react,
)
from seocho.eval.gtsqa import case_from_mapping
from seocho.eval.oxigraph_runtime import OxigraphQueryResult


def _case():
    return case_from_mapping(
        {
            "id": 7,
            "question": "Which item is linked?",
            "sparql_query": "",
            "seed_entities": ["Alpha (Q1)"],
            "all_answers_wikikg2": ["Beta (Q2)"],
            "graph": [["Alpha (Q1)", "linked (P1)", "Beta (Q2)"], ["Alpha (Q1)", "other (P2)", "Gamma (Q3)"]],
            "full_answer_subgraph_wikikg2": [["Alpha (Q1)", "linked (P1)", "Beta (Q2)"]],
            "n_hops": 1,
            "graph_isomorphism": "chain",
            "test_type": ["test"],
        }
    )


class _Executor:
    def __init__(self, case):
        self.case = case

    def execute_select(self, query: str):
        assert "SELECT" in query
        return OxigraphQueryResult(
            rows=({"s": "<urn:seocho:gtsqa:entity:Q1>", "p": "<urn:seocho:gtsqa:predicate:P1>", "o": "<urn:seocho:gtsqa:entity:Q2>"},),
            receipt={"truncated": False},
        )


class _Model:
    def __init__(self):
        self.calls = 0

    def complete(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return NativeModelReply("", (NativeToolCall("inspect_graph_schema", {}),), {"prompt_tokens": 10, "completion_tokens": 2}, 0.001)
        if self.calls == 2:
            return NativeModelReply("", (NativeToolCall("execute_sparql", {"query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o }"}),), {"prompt_tokens": 15, "completion_tokens": 5}, 0.001)
        return NativeModelReply("Beta", (), {"prompt_tokens": 20, "completion_tokens": 3}, 0.001)


def test_gtsqa_react_scores_projected_evidence_without_leaking_text() -> None:
    result = run_gtsqa_text2sparql_react(_case(), model=_Model(), executor_factory=_Executor)
    assert result.answer == "Beta"
    assert result.retrieval.precision == 1.0
    assert result.retrieval.recall == 1.0
    assert result.receipt["turn_count"] == 3
    assert result.receipt["tools"]["by_name"] == {"execute_sparql": 1, "inspect_graph_schema": 1}
    assert result.receipt["retrieval_funnel"]["effective_retrieval"] is True
    assert "Which item" not in json.dumps(result.receipt)
    assert "Alpha" not in json.dumps(result.receipt)


def test_gtsqa_closed_book_has_no_retrieval_surface() -> None:
    class ClosedBookModel:
        def complete(self, *, messages, tools):
            assert tools == ()
            return NativeModelReply("Beta", (), {"prompt_tokens": 8, "completion_tokens": 2}, 0.001)

    result = run_gtsqa_closed_book(_case(), model=ClosedBookModel())
    assert result.answer == "Beta"
    assert result.retrieved_edges == ()
    assert result.receipt["condition"] == "closed_book"
    assert result.receipt["retrieval_funnel"]["graph_tool_selected"] is False


def test_plumbing_smoke_does_not_accept_final_before_retrieval() -> None:
    class PrematureFinalModel(_Model):
        def complete(self, *, messages, tools):
            if self.calls == 0:
                self.calls += 1
                return NativeModelReply("premature", (), {"prompt_tokens": 5, "completion_tokens": 1}, 0.001)
            return super().complete(messages=messages, tools=tools)

    result = run_gtsqa_text2sparql_react(
        _case(),
        model=PrematureFinalModel(),
        executor_factory=_Executor,
        max_turns=5,
        require_retrieval=True,
    )
    assert result.answer == "Beta"
    assert result.receipt["retrieval_required"] is True
    assert result.receipt["retrieved_triple_count"] == 1


def test_react_fails_closed_when_tool_markup_was_not_parsed_natively() -> None:
    class BrokenParserModel:
        def complete(self, *, messages, tools):
            return NativeModelReply(
                "<tool_call><function=inspect_graph_schema>{}</function></tool_call>",
                (),
                {"prompt_tokens": 5, "completion_tokens": 3},
                0.001,
                "textual_tool_call_without_native_tool_calls",
            )

    result = run_gtsqa_text2sparql_react(
        _case(),
        model=BrokenParserModel(),
        executor_factory=_Executor,
    )
    assert result.answer == ""
    assert result.receipt["success"] is False
    assert result.receipt["terminal_status"] == "tool_parse_error"
    assert result.receipt["turns"][0]["outcome"] == "tool_parse_error"


def test_react_fails_closed_when_shared_token_budget_is_exceeded() -> None:
    class BudgetMarkerModel:
        def complete(self, *, messages, tools):
            return NativeModelReply(
                "",
                (),
                {"prompt_tokens": 8, "completion_tokens": 4},
                0.001,
                "token_budget_exceeded",
            )

    result = run_gtsqa_text2sparql_react(
        _case(),
        model=BudgetMarkerModel(),
        executor_factory=_Executor,
    )

    assert result.answer == ""
    assert result.receipt["success"] is False
    assert result.receipt["terminal_status"] == "token_budget_exceeded"
    assert result.receipt["turns"][0]["outcome"] == "token_budget_exceeded"


def test_react_returns_structured_repair_code_for_sparql_failure() -> None:
    class RejectingExecutor:
        def __init__(self, case):
            self.case = case

        def execute_select(self, query: str):
            raise RuntimeError("provider text must not reach the agent")

    class RepairAwareModel:
        def __init__(self):
            self.calls = 0

        def complete(self, *, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return NativeModelReply(
                    "",
                    (NativeToolCall("execute_sparql", {"query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o }"}),),
                    {"prompt_tokens": 10, "completion_tokens": 2},
                    0.001,
                )
            assert json.loads(messages[-1]["content"]) == {
                "error_code": "query_syntax_or_execution_error"
            }
            return NativeModelReply(
                "unable to retrieve evidence",
                (),
                {"prompt_tokens": 12, "completion_tokens": 3},
                0.001,
            )

    result = run_gtsqa_text2sparql_react(
        _case(),
        model=RepairAwareModel(),
        executor_factory=RejectingExecutor,
    )

    assert result.receipt["terminal_status"] == "abstained"
    assert result.receipt["answer_status"] == "abstained"
    assert result.receipt["success"] is False
    event = result.receipt["tools"]["events"][0]
    assert event["details"] == {"error_code": "query_syntax_or_execution_error"}
    assert "provider text" not in json.dumps(result.receipt)


def test_evidence_handoff_is_typed_and_redacted() -> None:
    handoff = EvidenceHandoff.from_rows(rows=[{"s": "Q1", "p": "P1", "o": "Q2"}], query="SELECT * WHERE {}")
    receipt = handoff.redacted_receipt()
    assert receipt["evidence_count"] == 1
    assert receipt["query_digest"] == hashlib.sha256(b"SELECT * WHERE {}").hexdigest()
    assert "Q1" not in json.dumps(receipt)


def test_handoff_receipt_events_are_monotonically_ordered() -> None:
    recorder = HandoffReceiptRecorder()
    recorder.record("handoff", source="Orchestrator", target="GraphQuerySpecialist", outcome="ok")
    recorder.record("tool", name="inspect_graph_schema", outcome="ok")
    recorder.record("agent_tool", name="retrieve_graph_evidence", outcome="selected")
    recorder.record(
        "tool",
        role="query_specialist",
        name="execute_graph_query",
        outcome="error",
    )
    recorder.record(
        "tool",
        role="query_specialist",
        name="execute_graph_query",
        outcome="ok",
    )
    recorder.record("agent_tool_return", name="retrieve_graph_evidence", outcome="ok")
    receipt = recorder.receipt()
    assert [event["event_ordinal"] for event in receipt["events"]] == [1, 2, 3, 4, 5, 6]
    assert receipt["handoff_count"] == 1
    assert receipt["agent_tool_count"] == 1
    assert receipt["agent_tool_failures"] == 0
    assert receipt["repair_count"] == 1
    assert receipt["query_retry_count"] == 1
    assert receipt["empty_result_retry_count"] == 0
    assert receipt["productive_retry_count"] == 0
    assert receipt["role_characteristics"]["query_specialist"] == {
        "schema_calls": 0,
        "query_calls": 2,
        "query_failures": 1,
        "repair_attempts": 1,
        "repair_successes": 1,
        "query_retries": 1,
        "empty_result_retries": 0,
        "productive_retries": 0,
    }


def test_agents_sdk_workflow_separates_orchestrator_and_specialist_tools() -> None:
    handoff = EvidenceHandoff.from_rows(rows=[], query="SELECT ?s WHERE {}")
    orchestrator, specialist, config = build_agents_sdk_handoff_workflow(
        model="example-model",
        execute_query=lambda _query: handoff,
        schema_discovery=lambda: {"relations": []},
    )
    assert not orchestrator.tools
    assert len(specialist.tools) == 2
    assert len(orchestrator.handoffs) == 1
    assert config.tracing_disabled is True


def test_agents_sdk_agent_tool_keeps_control_with_manager() -> None:
    evidence = EvidenceHandoff.from_rows(rows=[], query="SELECT ?s WHERE {}")
    manager, specialist, config, state = build_agents_sdk_agent_tool_workflow(
        model="example-model",
        execute_query=lambda _query: evidence,
        schema_discovery=lambda: {"relations": []},
    )

    assert not manager.handoffs
    assert [tool.name for tool in manager.tools] == ["retrieve_graph_evidence"]
    assert manager.tools[0]._is_agent_tool is True
    assert manager.tools[0].params_json_schema["properties"]["task"]["type"] == "string"
    assert [tool.name for tool in specialist.tools] == [
        "inspect_graph_schema",
        "execute_graph_query",
    ]
    assert state.invocation_count == 0
    assert config.tracing_disabled is True


def test_agent_tool_description_has_trigger_and_single_invocation_contract() -> None:
    evidence = EvidenceHandoff.from_rows(rows=[], query="SELECT ?s WHERE {}")
    manager, specialist, _config, state = build_agents_sdk_agent_tool_workflow(
        model="example-model",
        execute_query=lambda _query: evidence,
        schema_discovery=lambda: {"relations": []},
    )

    tool = manager.tools[0]
    assert "named entities" in tool.description
    assert "Call exactly once" in tool.description
    assert "never call" in tool.description
    assert "exactly once" in specialist.instructions
    assert specialist.tools[0].description == gtsqa_react_tools()[0]["function"]["description"]
    assert callable(tool.is_enabled)
    assert tool.is_enabled(None, None) is True
    state.invocation_count = 1
    assert tool.is_enabled(None, None) is False


def test_native_sparql_tool_descriptions_define_trigger_and_repair_contract() -> None:
    schema_tool, query_tool = gtsqa_react_tools()
    schema_description = schema_tool["function"]["description"]
    query_description = query_tool["function"]["description"]

    assert "entities, attributes, relations, paths, counts, or provenance" in schema_description
    assert "Do not call for greetings" in schema_description
    assert "only after inspect_graph_schema" in query_description
    assert "never repeat an identical failed query" in query_description
    assert "not permission to answer from memory" in query_description


def test_answer_status_distinguishes_empty_abstention_and_unsupported_answer() -> None:
    assert classify_answer_status("", evidence_status="ok") == "empty"
    assert classify_answer_status("I must abstain due to insufficient evidence", evidence_status="error") == "abstained"
    assert classify_answer_status("Beta", evidence_status="not_called") == "direct_answer"
    assert classify_answer_status("Beta", evidence_status="ok") == "answered"
    assert classify_answer_status("Beta", evidence_status="empty") == "unsupported_answer"


def test_redacted_episode_error_retains_stage_without_message_content() -> None:
    report = redacted_episode_error(
        ValueError("PRIVATE query and answer content"),
        stage="manager_specialist_as_tool",
    )

    assert report["error_code"] == "agent_output_or_usage_validation_error"
    assert report["error_stage"] == "manager_specialist_as_tool"
    assert len(report["error_fingerprint"]) == 64
    assert "PRIVATE" not in json.dumps(report)


def test_evidence_handoff_supports_direct_path_and_semantic_error() -> None:
    direct = EvidenceHandoff.not_called()
    rejected = EvidenceHandoff.from_rows(
        rows=(),
        query="SELECT ?s WHERE {}",
        status="error",
        error_code="query_policy_rejected",
    )

    assert direct.execution_status == "not_called"
    assert direct.error_code is None
    assert rejected.redacted_receipt()["error_code"] == "query_policy_rejected"


def test_evidence_handoff_is_valid_agents_sdk_strict_output_schema() -> None:
    output_schema = AgentOutputSchema(EvidenceHandoff)
    schema = output_schema.json_schema()
    assert output_schema.is_strict_json_schema() is True
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["EvidenceTriple"]["additionalProperties"] is False


def test_react_uses_injected_discovery_and_policy_hook() -> None:
    class InspectingModel(_Model):
        def complete(self, *, messages, tools):
            if self.calls == 1:
                assert "CONTROLLED-MARKER" in messages[-1]["content"]
            return super().complete(messages=messages, tools=tools)

    calls: list[tuple[str, str]] = []
    result = run_gtsqa_text2sparql_react(
        _case(),
        model=InspectingModel(),
        executor_factory=_Executor,
        schema_discovery=lambda _case: {"slice": "CONTROLLED-MARKER"},
        before_tool=lambda name, case: calls.append((name, case.workspace_id)),
    )
    assert result.answer == "Beta"
    assert calls == [
        ("inspect_graph_schema", "gtsqa-case-7"),
        ("execute_sparql", "gtsqa-case-7"),
    ]
    assert result.receipt["tools"]["by_name"]["runtime_policy"] == 2


def test_llm_request_timer_records_completion_latency_without_bodies() -> None:
    import asyncio

    import httpx

    from seocho.eval.agentic_graph_agents import LLMRequestTimer

    timer = LLMRequestTimer()

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 3}})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [timer.on_request], "response": [timer.on_response]},
        ) as client:
            await client.post("http://vllm/v1/chat/completions", json={"messages": []})
            await client.post("http://vllm/v1/chat/completions", json={"messages": []})
            await client.get("http://vllm/metrics")

    asyncio.run(scenario())
    receipt = timer.receipt()
    assert receipt["request_count"] == 2
    assert receipt["llm_ms"] >= 40.0
    assert receipt["source"] == "client_http_request_timer"
    assert all(item["path"] == "/v1/chat/completions" for item in receipt["requests"])
    # span_id is the W3C traceparent span id (None here: no case trace is bound in this test)
    assert all(set(item) == {"path", "status_code", "started_elapsed_ms", "latency_ms", "span_id"} for item in receipt["requests"])
    assert all(item["span_id"] is None for item in receipt["requests"])
    timer.reset()
    assert timer.receipt()["request_count"] == 0 and timer.total_ms() == 0.0


def test_make_vllm_chat_model_installs_timer_hooks() -> None:
    from seocho.eval.agentic_graph_agents import LLMRequestTimer, make_vllm_chat_model

    timer = LLMRequestTimer()
    model = make_vllm_chat_model(base_url="http://127.0.0.1:9/v1", api_key="EMPTY", model="m", request_timer=timer)
    client = model._client if hasattr(model, "_client") else getattr(model, "openai_client", None)
    assert client is not None
    hooks = client._client._event_hooks if hasattr(client._client, "_event_hooks") else client._client.event_hooks
    assert timer.on_request in hooks["request"] and timer.on_response in hooks["response"]
