from seocho.eval.agent_execution import AgentExecutionRecorder, build_retrieval_funnel, observe_tool


def test_agent_execution_receipt_separates_turns_tools_and_cache() -> None:
    recorder = AgentExecutionRecorder()
    recorder.record_llm(usage={"prompt_tokens": 100, "completion_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 10}, "tool_call_tokens": 8, "cached_tokens": 60}, latency_s=0.02)
    assert observe_tool(recorder, "concept_resolve", lambda: "ok") == "ok"
    receipt = recorder.receipt(success=True)
    assert receipt["turn_count"] == 1
    assert receipt["generated_tokens"] == {"total": 30, "thinking": 10, "tool_call": 8, "message": 12}
    assert receipt["cache"]["provider_cache_hit_ratio"] == 0.6
    assert receipt["tools"]["calls"] == 1
    assert receipt["turns"][0]["event_ordinal"] == 1
    assert receipt["tools"]["events"][0]["event_ordinal"] == 2


def test_retrieval_funnel_distinguishes_direct_answer_from_effective_graph_retrieval() -> None:
    direct = build_retrieval_funnel(
        turns=[{"latency_ms": 4, "output_kind": "message"}],
        tools=[],
        success=True,
        evidence_class="behavioral_baseline",
    )
    assert direct["retrieval_decision"] == "direct_answer"
    assert direct["graph_tool_selected"] is False
    assert direct["effective_retrieval"] is None

    graph = build_retrieval_funnel(
        turns=[{"latency_ms": 4, "output_kind": "tool_calls"}],
        tools=[
            {"name": "inspect_rdf_schema", "outcome": "ok"},
            {"name": "execute_sparql", "outcome": "ok"},
        ],
        success=True,
        evidence_class="main_comparable",
        query_validated=True,
        result_rows=3,
        gold_evidence_retrieved=True,
    )
    assert graph["retrieval_decision"] == "graph_tool"
    assert graph["schema_discovery_calls"] == 1
    assert graph["graph_query_calls"] == 1
    assert graph["query_executed"] is True
    assert graph["effective_retrieval"] is True


def test_retrieval_funnel_treats_specialist_agent_tool_as_trigger_not_query() -> None:
    funnel = build_retrieval_funnel(
        turns=[],
        tools=[
            {"name": "retrieve_graph_evidence", "outcome": "ok"},
            {"name": "inspect_graph_schema", "outcome": "ok"},
            {"name": "execute_graph_query", "outcome": "empty"},
        ],
        success=True,
        evidence_class="behavioral_baseline",
        query_validated=True,
        query_executed=True,
        result_rows=0,
    )

    assert funnel["graph_tool_selected"] is True
    assert funnel["schema_discovery_calls"] == 1
    assert funnel["graph_query_calls"] == 1
    assert funnel["retrieval_decision"] == "graph_tool"
