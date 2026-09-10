from __future__ import annotations

import json
from pathlib import Path

import pytest

from seocho.eval import agentic_os_trace as tr
from seocho.eval.agentic_os_otel import SpanRecord, build_case_spans, export_jsonl, export_otlp


def test_case_trace_ids_are_deterministic_and_w3c_shaped() -> None:
    a = tr.case_trace_id("run-1", "108")
    assert a == tr.case_trace_id("run-1", "108") and len(a) == 32 and int(a, 16) >= 0
    assert a != tr.case_trace_id("run-1", "109")
    sid = tr.new_span_id()
    assert len(sid) == 16
    tp = tr.traceparent(a, sid)
    assert tp.split("-") == ["00", a, sid, "01"]


def test_traceparent_is_injected_only_inside_a_bound_case() -> None:
    headers: dict[str, str] = {}
    assert tr.inject_traceparent(headers) is None and headers == {}
    with tr.case_trace("run-1", "108") as ct:
        sid = tr.inject_traceparent(headers)
        assert sid and headers["traceparent"] == tr.traceparent(ct.trace_id, sid)
        assert tr.current_case_trace() is ct
        assert ct.receipt()["propagation"] == "w3c_traceparent"
    assert tr.current_case_trace() is None


SINGLE = {
    "run_id": "grbench-single-abc", "case_id": "108", "arm": "B0", "cache_phase": "warm", "seed": 0,
    "answer_status": "answered", "terminal": "final", "turn_count": 2, "success": True,
    "time": {"wall_ms": 3000.0, "llm_ms": 1500.0, "tool_ms": 1200.0},
    "turns": [
        {"event_ordinal": 1, "started_elapsed_ms": 0.0, "latency_ms": 500.0, "input_tokens": 600, "output_tokens": 20, "cached_tokens": 0, "output_kind": "tool_calls", "outcome": "ok", "span_id": "aaaaaaaaaaaaaaaa"},
        {"event_ordinal": 3, "started_elapsed_ms": 1700.0, "latency_ms": 1000.0, "input_tokens": 900, "output_tokens": 80, "cached_tokens": 512, "output_kind": "message", "outcome": "ok"},
    ],
    "tools": [{"name": "execute_graph_query", "latency_ms": 1200.0, "outcome": "ok", "rows": 2, "result_bytes": 120, "schema_slice_digest": "d" * 64}],
    "retrieval_funnel": {"graph_tool_selected": True, "query_validated": True, "query_executed": True, "nonempty_result": True},
}
MULTI = {
    "run_id": "grbench-multi-xyz", "case_id": "111", "arm": "B0", "answer_status": "abstained", "terminal_status": "abstained",
    "turn_count": 4, "time": {"wall_ms": 9000.0, "llm_ms": 8500.0, "tool_ms": 2.0}, "success": False, "error_code": None,
    "llm_requests": [
        {"path": "/v1/chat/completions", "status_code": 200, "started_elapsed_ms": 300.0, "latency_ms": 4000.0, "span_id": "bbbbbbbbbbbbbbbb"},
        {"path": "/v1/chat/completions", "status_code": 200, "started_elapsed_ms": 5000.0, "latency_ms": 3000.0},
    ],
    "events": [
        {"event_ordinal": 1, "event": "agent_tool", "role": "manager", "name": "retrieve_graph_evidence", "outcome": "selected", "started_elapsed_ms": 4300.0, "elapsed_ms": 4300.0, "latency_ms": 0.0, "payload_bytes": 62},
        {"event_ordinal": 2, "event": "tool", "role": "query_specialist", "name": "inspect_graph_schema", "outcome": "ok", "started_elapsed_ms": 5100.0, "latency_ms": 0.2, "output_bytes": 300},
        {"event_ordinal": 3, "event": "tool", "role": "query_specialist", "name": "execute_graph_query", "outcome": "error", "error_code": "query_policy_rejected", "started_elapsed_ms": 8200.0, "latency_ms": 1.0, "evidence_count": 0, "evidence_bytes": 2, "query_digest": "e" * 64},
        {"event_ordinal": 4, "event": "agent_tool_return", "role": "manager", "name": "retrieve_graph_evidence", "outcome": "error", "elapsed_ms": 8300.0, "execution_status": "error", "evidence_count": 0},
    ],
}


def _by_name(spans: list[SpanRecord]) -> dict[str, list[SpanRecord]]:
    out: dict[str, list[SpanRecord]] = {}
    for s in spans:
        out.setdefault(s.name.split(" ")[0], []).append(s)
    return out


def test_single_agent_receipt_becomes_a_case_tree_with_gen_ai_and_db_spans() -> None:
    spans = build_case_spans(SINGLE, lane="grbench-single", topology="single_agent", anchor_unix_s=1_700_000_000.0, model="m")
    names = _by_name(spans)
    root = names["agentic_os.case"][0]
    assert root.parent_span_id is None and root.trace_id == tr.case_trace_id("grbench-single-abc", "108")
    assert root.attributes["seocho.lane"] == "grbench-single" and root.attributes["seocho.retrieval.nonempty_result"] is True
    chats = names["chat"]
    assert len(chats) == 2 and chats[0].span_id == "aaaaaaaaaaaaaaaa"  # the traceparent span id is reused
    assert chats[0].attributes["gen_ai.operation.name"] == "chat" and chats[0].attributes["gen_ai.usage.input_tokens"] == 600
    assert chats[0].kind == "CLIENT" and chats[0].end_unix_ns - chats[0].start_unix_ns == 500_000_000
    tool = names["execute_tool"][0]
    assert tool.attributes["gen_ai.tool.name"] == "execute_graph_query" and tool.attributes["seocho.timing"] == "reconstructed"
    db = names["neo4j"][0]
    assert db.parent_span_id == tool.span_id and db.attributes["db.system.name"] == "neo4j" and db.attributes["seocho.query_digest"] == "d" * 64
    ids = {s.span_id for s in spans}
    assert all(s.parent_span_id in ids for s in spans if s.parent_span_id)
    # idempotent: same ids on re-export
    again = build_case_spans(SINGLE, lane="grbench-single", topology="single_agent", anchor_unix_s=1_700_000_000.0, model="m")
    assert [s.span_id for s in again] == [s.span_id for s in spans]
    # content-free: no attribute longer than a digest
    assert all(len(v) <= 64 for s in spans for v in s.attributes.values() if isinstance(v, str))


def test_multi_agent_receipt_nests_specialist_tools_under_invoke_agent() -> None:
    spans = build_case_spans(MULTI, lane="grbench-multi", topology="manager_specialist_as_tool", anchor_unix_s=1_700_000_000.0, model="m")
    names = _by_name(spans)
    agent = names["invoke_agent"][0]
    assert agent.attributes["gen_ai.agent.name"] == "GraphQuerySpecialist" and agent.status == "ERROR"
    assert agent.end_unix_ns - agent.start_unix_ns == 4_000_000_000  # agent_tool -> agent_tool_return
    tools = names["execute_tool"]
    assert {t.parent_span_id for t in tools} == {agent.span_id}
    rejected = next(t for t in tools if t.attributes["gen_ai.tool.name"] == "execute_graph_query")
    assert rejected.status == "ERROR" and rejected.status_description == "query_policy_rejected"
    assert "neo4j" not in names  # a policy-rejected query never reached the database
    chats = names["chat"]
    assert chats[0].span_id == "bbbbbbbbbbbbbbbb" and len(chats[1].span_id) == 16


def test_jsonl_and_otlp_exports_preserve_ids(tmp_path: Path) -> None:
    spans = build_case_spans(SINGLE, lane="grbench-single", topology="single_agent", anchor_unix_s=1_700_000_000.0, model="m")
    out = tmp_path / "spans.jsonl"
    assert export_jsonl(spans, out) == len(spans)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert rows[0]["schema_version"] == "seocho.agentic_os_otel_span.v1" and {r["span_id"] for r in rows} == {s.span_id for s in spans}
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    mem = InMemorySpanExporter()
    assert export_otlp(spans, exporter=mem) == len(spans)
    got = {format(s.context.span_id, "016x"): s for s in mem.get_finished_spans()}
    for s in spans:
        o = got[s.span_id]
        assert format(o.context.trace_id, "032x") == s.trace_id
        assert (o.parent.span_id if o.parent else None) == (int(s.parent_span_id, 16) if s.parent_span_id else None)
        assert o.start_time == s.start_unix_ns and o.end_time == s.end_unix_ns


def test_openai_compatible_client_sends_traceparent_and_records_span_id(monkeypatch) -> None:
    import io
    import json as _json

    from seocho.eval import agentic_graph_agents as aga

    captured: dict[str, str] = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    def fake_urlopen(request, timeout=0):
        captured.update({k.lower(): v for k, v in request.header_items()})
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return _Resp(_json.dumps(body).encode())

    monkeypatch.setattr(aga, "urlopen", fake_urlopen)
    model = aga.OpenAICompatibleNativeToolModel(base_url="http://127.0.0.1:1", model="m")
    reply = model.complete(messages=({"role": "user", "content": "q"},), tools=())
    assert reply.span_id is None and "traceparent" not in captured   # unbound: nothing added
    with tr.case_trace("run-1", "108") as ct:
        reply = model.complete(messages=({"role": "user", "content": "q"},), tools=())
    assert reply.span_id and captured["traceparent"] == tr.traceparent(ct.trace_id, reply.span_id)
    rec = aga.AgentExecutionRecorder()
    rec.record_llm(usage=reply.usage, latency_s=reply.latency_s, span_id=reply.span_id)
    assert rec.turns[0]["span_id"] == reply.span_id
