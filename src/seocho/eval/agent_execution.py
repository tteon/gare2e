"""Content-free execution receipts for provider-backed agent experiments.

Measures repeated model re-entry separately from external tool work.  Cache
numbers are provider-reported when available; otherwise they remain null rather
than being presented as an observed cache hit rate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, TypeVar


SCHEMA_DISCOVERY_TOOLS = frozenset({"inspect_graph_schema", "inspect_rdf_schema", "schema_introspect"})
GRAPH_ROUTE_TOOLS = frozenset({"retrieve_graph_evidence"})
GRAPH_QUERY_TOOLS = frozenset(
    {
        "execute_cypher",
        "execute_sparql",
        "execute_graph_query",
        "text2cypher",
        "text2sparql",
        "validate_cypher",
        "validate_sparql",
    }
)
GRAPH_TOOLS = GRAPH_ROUTE_TOOLS | SCHEMA_DISCOVERY_TOOLS | GRAPH_QUERY_TOOLS


def _usage_value(usage: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


@dataclass
class AgentExecutionRecorder:
    """One serialized task lifetime; no prompts, answers, or tool payloads."""
    turns: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    _started: float = field(default_factory=time.perf_counter)
    _event_ordinal: int = 0

    def _next_event(self, *, latency_s: float) -> tuple[int, float]:
        self._event_ordinal += 1
        completed_ms = (time.perf_counter() - self._started) * 1000
        return self._event_ordinal, max(0.0, completed_ms - latency_s * 1000)

    def record_llm(self, *, usage: Mapping[str, Any], latency_s: float, outcome: str = "ok", output_kind: str = "message") -> None:
        event_ordinal, started_elapsed_ms = self._next_event(latency_s=latency_s)
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), Mapping) else {}
        completion_details = (
            usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), Mapping)
            else {}
        )
        cached = details.get("cached_tokens") if isinstance(details.get("cached_tokens"), int) else usage.get("cached_tokens")
        reasoning_tokens = _usage_value(completion_details, "reasoning_tokens") or _usage_value(
            usage, "reasoning_tokens", "thinking_tokens"
        )
        self.turns.append({"event_ordinal": event_ordinal, "started_elapsed_ms": round(started_elapsed_ms, 3), "input_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"), "output_tokens": _usage_value(usage, "completion_tokens", "output_tokens"), "thinking_tokens": reasoning_tokens, "tool_call_tokens": _usage_value(usage, "tool_call_tokens"), "cached_tokens": int(cached) if isinstance(cached, int) else None, "latency_ms": round(latency_s * 1000, 3), "outcome": outcome, "output_kind": output_kind})

    def record_tool(
        self,
        *,
        name: str,
        latency_s: float,
        outcome: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        event_ordinal, started_elapsed_ms = self._next_event(latency_s=latency_s)
        event = {
            "event_ordinal": event_ordinal,
            "started_elapsed_ms": round(started_elapsed_ms, 3),
            "name": name,
            "latency_ms": round(latency_s * 1000, 3),
            "outcome": outcome,
        }
        if details:
            # Callers are responsible for passing counts, timings and digests
            # only. Keeping details nested makes the telemetry boundary easy to
            # audit and prevents raw observations becoming top-level fields.
            event["details"] = dict(details)
        self.tools.append(event)

    def receipt(self, *, success: bool) -> dict[str, Any]:
        total_input = sum(item["input_tokens"] for item in self.turns)
        total_output = sum(item["output_tokens"] for item in self.turns)
        cached = [item["cached_tokens"] for item in self.turns if item["cached_tokens"] is not None]
        llm_ms = sum(item["latency_ms"] for item in self.turns)
        tool_ms = sum(item["latency_ms"] for item in self.tools)
        receipt = {"schema_version": "seocho.agent_execution_receipt.v1", "success": success, "turn_count": len(self.turns), "accumulated_context_tokens": total_input, "generated_tokens": {"total": total_output, "thinking": sum(item["thinking_tokens"] for item in self.turns), "tool_call": sum(item["tool_call_tokens"] for item in self.turns), "message": max(0, total_output - sum(item["thinking_tokens"] + item["tool_call_tokens"] for item in self.turns))}, "cache": {"provider_reported_cached_tokens": sum(cached) if cached else None, "provider_cache_hit_ratio": round(sum(cached) / total_input, 6) if cached and total_input else None}, "time": {"llm_ms": round(llm_ms, 3), "tool_ms": round(tool_ms, 3), "wall_ms": round((time.perf_counter() - self._started) * 1000, 3)}, "tools": {"calls": len(self.tools), "failures": sum(item["outcome"] != "ok" for item in self.tools), "by_name": {name: sum(item["name"] == name for item in self.tools) for name in sorted({item["name"] for item in self.tools})}, "events": self.tools}, "turns": self.turns}
        receipt["retrieval_funnel"] = build_retrieval_funnel(
            turns=self.turns,
            tools=self.tools,
            success=success,
            evidence_class="unspecified",
        )
        return receipt


def build_retrieval_funnel(
    *,
    turns: list[Mapping[str, Any]],
    tools: list[Mapping[str, Any]],
    success: bool,
    evidence_class: str,
    forced_tool: bool = False,
    abstained: bool = False,
    query_validated: bool | None = None,
    query_executed: bool | None = None,
    result_rows: int | None = None,
    gold_evidence_retrieved: bool | None = None,
) -> dict[str, Any]:
    """Build one content-free trigger-to-evidence funnel from ordered events."""
    graph_events = [event for event in tools if str(event.get("name", "")) in GRAPH_TOOLS]
    schema_calls = sum(str(event.get("name", "")) in SCHEMA_DISCOVERY_TOOLS for event in tools)
    query_events = [event for event in tools if str(event.get("name", "")) in GRAPH_QUERY_TOOLS]
    first = graph_events[0] if graph_events else None
    first_ordinal = first.get("event_ordinal") if isinstance(first, Mapping) else None
    if isinstance(first_ordinal, int):
        turns_before = sum(
            isinstance(turn.get("event_ordinal"), int) and int(turn["event_ordinal"]) < first_ordinal
            for turn in turns
        )
    else:
        turns_before = next(
            (index + 1 for index, turn in enumerate(turns) if str(turn.get("output_kind", "")).startswith("tool")),
            None,
        )
    first_tool_ms = first.get("started_elapsed_ms") if isinstance(first, Mapping) else None
    if first_tool_ms is None and turns_before:
        first_tool_ms = sum(float(turn.get("latency_ms") or 0) for turn in turns[:turns_before])
    selected = bool(graph_events)
    inferred_query_executed = any(
        event.get("outcome") in {"ok", "empty"}
        and str(event.get("name", "")) in {"execute_cypher", "execute_sparql", "execute_graph_query"}
        for event in query_events
    )
    executed = inferred_query_executed if query_executed is None else query_executed
    nonempty = result_rows > 0 if result_rows is not None else None
    effective = gold_evidence_retrieved if gold_evidence_retrieved is not None else nonempty
    decision = "abstain" if abstained else ("graph_tool" if selected else ("direct_answer" if success else "unresolved"))
    return {
        "schema_version": "seocho.retrieval_funnel.v1",
        "evidence_class": evidence_class,
        "retrieval_decision": decision,
        "forced_tool": forced_tool,
        "graph_tool_selected": selected,
        "time_to_first_graph_tool_ms": round(float(first_tool_ms), 3) if isinstance(first_tool_ms, (int, float)) else None,
        "llm_turns_before_first_graph_tool": turns_before,
        "schema_discovery_calls": schema_calls,
        "graph_query_calls": len(query_events),
        "query_validated": query_validated,
        "query_executed": executed,
        "result_rows": result_rows,
        "nonempty_result": nonempty,
        "gold_evidence_retrieved": gold_evidence_retrieved,
        "effective_retrieval": effective,
    }


T = TypeVar("T")


def observe_tool(
    recorder: AgentExecutionRecorder,
    name: str,
    call: Callable[..., T],
    *args: Any,
    receipt: Callable[[T], Mapping[str, Any]] | None = None,
    error_receipt: Callable[[Exception], Mapping[str, Any]] | None = None,
    **kwargs: Any,
) -> T:
    started = time.perf_counter()
    try:
        result = call(*args, **kwargs)
    except Exception as error:
        details = error_receipt(error) if error_receipt is not None else None
        recorder.record_tool(
            name=name,
            latency_s=time.perf_counter() - started,
            outcome="error",
            details=details,
        )
        raise
    details = receipt(result) if receipt is not None else None
    recorder.record_tool(
        name=name,
        latency_s=time.perf_counter() - started,
        outcome="ok",
        details=details,
    )
    return result
