"""Receipt → OpenTelemetry span trees for AgenticOS experiments.

The runners write content-free receipts (turn latencies, tool outcomes, token
usage, digests).  This module turns one case receipt into a span tree::

    agentic_os.case                           (INTERNAL, root)
    ├── chat <model>                          (CLIENT, one per LLM request; gen_ai.*)
    ├── execute_tool <name>                   (INTERNAL, one per tool call)
    │   └── <db.system> query                 (CLIENT, graph-database call; db.*)
    └── invoke_agent <specialist>             (INTERNAL, multi-agent only)

Ids follow :mod:`seocho.eval.agentic_os_trace`: the trace id is derived from
``(run_id, case_id)``; a chat span reuses the span id the client put in its
``traceparent`` header (so spans vLLM exports for the same request nest under
it); everything else gets a deterministic id, so re-exporting is idempotent.

Attribute names follow the OpenTelemetry semantic conventions where one exists
(``gen_ai.operation.name``, ``gen_ai.request.model``, ``gen_ai.usage.*``,
``gen_ai.tool.name``, ``gen_ai.agent.name``, ``db.system.name``,
``db.operation.name``, ``db.query.summary`` is *not* set — the receipt only has
a digest, exported as ``seocho.query_digest``).  Everything experiment-specific
is under ``seocho.*``.  No attribute ever carries prompt, query or answer text.

Two sinks: ``export_jsonl`` (dependency-free, one span per line, the neutral
artifact) and ``export_otlp`` (OpenTelemetry SDK + OTLP/gRPC, ``seocho[otel]``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .agentic_os_trace import case_trace_id, derived_span_id

GRAPH_QUERY_TOOLS = {"execute_graph_query", "execute_sparql", "execute_cypher"}
SCHEMA_TOOLS = {"inspect_graph_schema"}


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str  # INTERNAL | CLIENT | SERVER
    start_unix_ns: int
    end_unix_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"  # OK | ERROR | UNSET
    status_description: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": "seocho.agentic_os_otel_span.v1",
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "start_unix_ns": self.start_unix_ns,
            "end_unix_ns": self.end_unix_ns,
            "attributes": self.attributes,
            "status": self.status,
            "status_description": self.status_description,
        }


def _ns(unix_s: float) -> int:
    return int(round(unix_s * 1_000_000_000))


def _clean(attrs: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None and v != ""}


def _db_system(lane: str, event_name: str) -> str:
    if "sparql" in event_name or lane.startswith("gtsqa"):
        return "oxigraph"
    return "neo4j"


def build_case_spans(
    receipt: Mapping[str, Any],
    *,
    lane: str,
    topology: str,
    anchor_unix_s: float,
    model: str | None = None,
    run_id: str | None = None,
    extra_attributes: Mapping[str, Any] | None = None,
) -> list[SpanRecord]:
    """Build the span tree for one case receipt.

    ``anchor_unix_s`` is the wall-clock instant the case's elapsed-ms offsets
    are measured from (the coordinator's ``runner_started_unix_s`` is the
    closest anchor we have; receipts carry monotonic offsets only).
    """
    run_id = str(run_id or receipt.get("run_id") or "")
    case_id = str(receipt.get("case_id") or receipt.get("qid") or "")
    trace = receipt.get("trace") if isinstance(receipt.get("trace"), Mapping) else {}
    trace_id = str(trace.get("trace_id") or case_trace_id(run_id, case_id))
    root_id = str(trace.get("root_span_id") or derived_span_id(run_id, case_id, "case"))
    time_block = receipt.get("time") if isinstance(receipt.get("time"), Mapping) else {}
    wall_ms = float(time_block.get("wall_ms") or receipt.get("wall_ms") or 0.0)
    start_ns = _ns(anchor_unix_s)
    end_ns = start_ns + int(wall_ms * 1_000_000)
    funnel = receipt.get("retrieval_funnel") if isinstance(receipt.get("retrieval_funnel"), Mapping) else {}
    root_attrs = _clean({
        "seocho.run_id": run_id,
        "seocho.case_id": case_id,
        "seocho.lane": lane,
        "seocho.topology": topology,
        "seocho.arm": receipt.get("arm"),
        "seocho.cache_phase": receipt.get("cache_phase"),
        "seocho.seed": receipt.get("seed"),
        "seocho.answer_status": receipt.get("answer_status"),
        "seocho.terminal_status": receipt.get("terminal_status") or receipt.get("terminal"),
        "seocho.turn_count": receipt.get("turn_count"),
        "seocho.query_retry_count": receipt.get("query_retry_count"),
        "seocho.repair_count": receipt.get("repair_count"),
        "seocho.retrieval.triggered": funnel.get("graph_tool_selected"),
        "seocho.retrieval.query_validated": funnel.get("query_validated"),
        "seocho.retrieval.query_executed": funnel.get("query_executed"),
        "seocho.retrieval.nonempty_result": funnel.get("nonempty_result"),
        "seocho.time.llm_ms": time_block.get("llm_ms"),
        "seocho.time.tool_ms": time_block.get("tool_ms"),
        "seocho.time.wall_ms": wall_ms or None,
        "seocho.timing.anchor": "coordinator_runner_start",
        **(extra_attributes or {}),
    })
    spans: list[SpanRecord] = [SpanRecord(trace_id, root_id, None, "agentic_os.case", "INTERNAL", start_ns, end_ns, root_attrs,
                                          "ERROR" if receipt.get("success") is False and receipt.get("error_code") else "OK")]

    def child(span_id: str, parent: str, name: str, kind: str, s_ms: float, d_ms: float, attrs: Mapping[str, Any], status: str = "OK", desc: str | None = None) -> None:
        s = start_ns + int(max(s_ms, 0.0) * 1_000_000)
        spans.append(SpanRecord(trace_id, span_id, parent, name, kind, s, s + int(max(d_ms, 0.0) * 1_000_000), _clean(attrs), status, desc))

    # --- single-agent turns (LLM requests with usage) -----------------------
    cursor_ms = 0.0
    turns = receipt.get("turns") if isinstance(receipt.get("turns"), list) else []
    for i, t in enumerate(turns):
        lat = float(t.get("latency_ms") or 0.0)
        started = t.get("started_elapsed_ms")
        s_ms = float(started) if isinstance(started, (int, float)) else cursor_ms
        cursor_ms = s_ms + lat
        sid = str(t.get("span_id") or derived_span_id(run_id, case_id, "turn", i))
        outcome = str(t.get("outcome") or "ok")
        child(sid, root_id, f"chat {model or 'llm'}", "CLIENT", s_ms, lat, {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "vllm",
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": t.get("input_tokens"),
            "gen_ai.usage.output_tokens": t.get("output_tokens"),
            "seocho.usage.cached_tokens": t.get("cached_tokens"),
            "seocho.usage.reasoning_tokens": t.get("thinking_tokens") or t.get("reasoning_tokens"),
            "seocho.output_kind": t.get("output_kind"),
            "seocho.turn_ordinal": t.get("event_ordinal", i + 1),
            "seocho.timing": "measured" if isinstance(started, (int, float)) else "reconstructed",
        }, "OK" if outcome == "ok" else "ERROR", None if outcome == "ok" else outcome)

    # --- multi-agent llm requests (client timer) -------------------------------
    for i, r in enumerate(receipt.get("llm_requests") or []):
        sid = str(r.get("span_id") or derived_span_id(run_id, case_id, "llm_request", i))
        status = int(r.get("status_code") or 200)
        child(sid, root_id, f"chat {model or 'llm'}", "CLIENT", float(r.get("started_elapsed_ms") or 0), float(r.get("latency_ms") or 0), {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "vllm",
            "gen_ai.request.model": model,
            "http.response.status_code": status,
            "url.path": r.get("path"),
            "seocho.request_ordinal": i + 1,
        }, "OK" if status < 400 else "ERROR")

    # --- tools: single-agent list / dict, multi-agent events -------------------
    tool_items: list[tuple[str, Mapping[str, Any], str]] = []  # (role, event, source)
    tools = receipt.get("tools")
    if isinstance(tools, list):
        tool_items += [("single_agent", t, "tools") for t in tools if isinstance(t, Mapping)]
    elif isinstance(tools, Mapping):
        tool_items += [("single_agent", t, "tools.events") for t in tools.get("events", []) if isinstance(t, Mapping)]
    events = receipt.get("events") if isinstance(receipt.get("events"), list) else []
    agent_tool_span: str | None = None
    if events:
        # manager -> specialist: agent_tool ... agent_tool_return bracket the specialist's work
        sel = next((e for e in events if e.get("event") == "agent_tool"), None)
        ret = next((e for e in events if e.get("event") == "agent_tool_return"), None)
        if sel is not None:
            agent_tool_span = derived_span_id(run_id, case_id, "agent_tool", sel.get("event_ordinal"))
            s_ms = float(sel.get("started_elapsed_ms") or sel.get("elapsed_ms") or 0)
            e_ms = float(ret.get("elapsed_ms") or 0) if ret is not None else s_ms
            child(agent_tool_span, root_id, f"invoke_agent {sel.get('name') or 'specialist'}", "INTERNAL", s_ms, max(e_ms - s_ms, 0.0), {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "GraphQuerySpecialist",
                "seocho.caller_role": sel.get("role"),
                "seocho.payload_bytes": sel.get("payload_bytes"),
                "seocho.outcome": (ret or {}).get("outcome"),
                "seocho.evidence_count": (ret or {}).get("evidence_count"),
                "seocho.execution_status": (ret or {}).get("execution_status"),
            }, "OK" if (ret or {}).get("outcome") not in ("error",) else "ERROR")
        tool_items += [(str(e.get("role") or ""), e, "events") for e in events if e.get("event") == "tool"]

    tool_cursor_ms = 0.0
    for i, (role, e, source) in enumerate(tool_items):
        name = str(e.get("name") or "tool")
        lat = float(e.get("latency_ms") or 0.0)
        started = e.get("started_elapsed_ms")
        if isinstance(started, (int, float)):
            s_ms = float(started)
        else:
            # GRBench single tools carry latency only: place each call right after the
            # LLM turn that produced it (turns and tools alternate in the receipt order).
            turn_idx = min(i + 1, len(turns) - 1) if turns else 0
            t_prev = turns[turn_idx] if turns else {}
            base = t_prev.get("started_elapsed_ms")
            s_ms = float(base) + float(t_prev.get("latency_ms") or 0) if isinstance(base, (int, float)) else tool_cursor_ms
        tool_cursor_ms = s_ms + lat
        parent = agent_tool_span if (source == "events" and agent_tool_span and role == "query_specialist") else root_id
        tool_sid = derived_span_id(run_id, case_id, "tool", source, e.get("event_ordinal", i))
        outcome = str(e.get("outcome") or "ok")
        error_code = e.get("error_code")
        is_err = outcome in ("error", "rejected", "unsupported") or bool(error_code)
        child(tool_sid, parent, f"execute_tool {name}", "INTERNAL", s_ms, lat, {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.type": "function",
            "seocho.tool.outcome": outcome,
            "seocho.tool.error_code": error_code,
            "seocho.tool.result_bytes": e.get("result_bytes") or e.get("output_bytes") or e.get("evidence_bytes"),
            "seocho.tool.rows": e.get("rows") if e.get("rows") is not None else e.get("evidence_count"),
            "seocho.tool.argument_decode_ms": e.get("tool_argument_decode_ms"),
            "seocho.agent_role": role or None,
            "seocho.timing": "measured" if isinstance(started, (int, float)) else "reconstructed",
        }, "ERROR" if is_err else "OK", str(error_code or outcome) if is_err else None)
        reached_db = name in GRAPH_QUERY_TOOLS and outcome != "rejected" and error_code != "query_policy_rejected"
        if reached_db:  # the validator rejects before any Bolt/Oxigraph call
            # the database round trip is the tool's inner call; the validator ran before it
            child(derived_span_id(run_id, case_id, "db", source, e.get("event_ordinal", i)), tool_sid, f"{_db_system(lane, name)} query", "CLIENT", s_ms, lat, {
                "db.system.name": _db_system(lane, name),
                "db.operation.name": "query",
                "db.namespace": receipt.get("workspace_id"),
                "seocho.query_digest": e.get("query_digest") or e.get("schema_slice_digest"),
                "seocho.db.rows": e.get("rows") if e.get("rows") is not None else e.get("evidence_count"),
                "seocho.db.result_bytes": e.get("result_bytes") or e.get("evidence_bytes"),
                "seocho.db.truncated": e.get("truncated"),
            }, "ERROR" if is_err else "OK", str(error_code) if error_code else None)
    return spans


def iter_case_receipts(root: Path) -> Iterable[tuple[Mapping[str, Any], float]]:
    """Yield (receipt, anchor_unix_s) for every attempt under a coordinator root."""
    anchors: dict[str, float] = {}
    coord = root / "coordinator-receipts.jsonl"
    if coord.exists():
        for line in coord.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            anchors[str(row.get("case_id"))] = float(row.get("runner_started_unix_s") or 0)
    for attempt in sorted((root / "attempts").glob("*")) if (root / "attempts").is_dir() else []:
        run_receipt = attempt / "run-receipt.json"
        receipts = attempt / "receipts.jsonl"
        cases: list[Mapping[str, Any]] = []
        if run_receipt.exists():
            cases = list(json.loads(run_receipt.read_text(encoding="utf-8")).get("cases", []))
        elif receipts.exists():
            cases = [json.loads(l) for l in receipts.read_text(encoding="utf-8").splitlines() if l.strip()]
        for c in cases:
            anchor = anchors.get(str(c.get("case_id") or c.get("qid")))
            if anchor is None:
                anchor = float(c.get("started_unix_s") or 0.0)
            yield c, anchor


def lane_of(root: Path) -> tuple[str, str]:
    name = root.name if not root.name.startswith("shard-") else root.parent.name
    lane = "grbench" if "grbench" in name else "gtsqa" if "gtsqa" in name else "unknown"
    topology = "manager_specialist_as_tool" if "-multi-" in name else "single_agent"
    return lane + ("-multi" if topology != "single_agent" else "-single"), topology


def build_root_spans(root: Path, *, model: str | None = None) -> list[SpanRecord]:
    lane, topology = lane_of(root)
    manifest = root / "coordinator-manifest.json"
    run_id = None
    if manifest.exists():
        try:
            run_id = json.loads(manifest.read_text(encoding="utf-8")).get("run_id")
        except json.JSONDecodeError:
            run_id = None
    out: list[SpanRecord] = []
    for receipt, anchor in iter_case_receipts(root):
        out += build_case_spans(receipt, lane=lane, topology=topology, anchor_unix_s=anchor, model=model, run_id=receipt.get("run_id") or run_id)
    return out


def export_jsonl(spans: Iterable[SpanRecord], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as handle:
        for s in spans:
            handle.write(json.dumps(s.to_json(), sort_keys=True) + "\n")
            n += 1
    return n


class _PresetIds:
    """OTel IdGenerator that hands out the ids we already committed to in receipts."""

    def __init__(self) -> None:
        self.next_trace: int = 0
        self.next_span: int = 0

    def generate_trace_id(self) -> int:  # pragma: no cover - exercised through the SDK
        return self.next_trace

    def generate_span_id(self) -> int:  # pragma: no cover - exercised through the SDK
        return self.next_span


def export_otlp(spans: list[SpanRecord], *, endpoint: str | None = None, service_name: str = "seocho-agentic-os", exporter: Any = None) -> int:
    """Replay span records into an OTLP endpoint (or a caller-supplied SpanExporter)."""
    try:
        from opentelemetry import trace as ot_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.id_generator import IdGenerator
        from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, Status, StatusCode, TraceFlags
    except ImportError as exc:  # pragma: no cover
        raise ImportError("OTLP export requires the seocho[otel] extra") from exc
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        target = endpoint or "http://localhost:4317"
        exporter = OTLPSpanExporter(endpoint=target, insecure=target.startswith("http://"))

    class Ids(_PresetIds, IdGenerator):
        pass

    ids = Ids()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}), id_generator=ids)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("seocho.eval.agentic_os_otel")
    kinds = {"INTERNAL": SpanKind.INTERNAL, "CLIENT": SpanKind.CLIENT, "SERVER": SpanKind.SERVER}
    # parents first
    order = sorted(spans, key=lambda s: (s.parent_span_id is not None, s.start_unix_ns))
    seen: set[str] = set()
    pending = list(order)
    emitted = 0
    while pending:
        progressed = False
        rest = []
        for s in pending:
            if s.parent_span_id and s.parent_span_id not in seen and any(x.span_id == s.parent_span_id for x in spans):
                rest.append(s)
                continue
            ids.next_trace = int(s.trace_id, 16)
            ids.next_span = int(s.span_id, 16)
            ctx = None
            if s.parent_span_id:
                parent = SpanContext(trace_id=int(s.trace_id, 16), span_id=int(s.parent_span_id, 16), is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED))
                ctx = ot_trace.set_span_in_context(NonRecordingSpan(parent))
            span = tracer.start_span(s.name, context=ctx, kind=kinds.get(s.kind, SpanKind.INTERNAL), attributes={k: v for k, v in s.attributes.items() if isinstance(v, (str, bool, int, float))}, start_time=s.start_unix_ns)
            if s.status == "ERROR":
                span.set_status(Status(StatusCode.ERROR, s.status_description))
            span.end(end_time=s.end_unix_ns)
            seen.add(s.span_id)
            emitted += 1
            progressed = True
        if not progressed:  # orphan parents: emit anyway
            for s in rest:
                s.parent_span_id = None
            pending = rest
            continue
        pending = rest
    provider.force_flush()
    provider.shutdown()
    return emitted
