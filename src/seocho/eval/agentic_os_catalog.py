"""Deterministic catalog and statistics for AgenticOS graph-agent runs.

The catalog is deliberately answer-independent.  It consumes content-free run
manifests and receipts, retains every attempted case in the denominator, and
optionally joins scores produced by a separate blinded judge job.  It never
calls a model provider or reads private predictions/evidence.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from .agentic_os_artifacts import assert_content_free, canonical_digest


CATALOG_SCHEMA_VERSION = "agentic_os.result_catalog.v1"
ATTEMPT_DIMENSIONS = (
    "run_id",
    "case_id",
    "lane",
    "arm",
    "seed",
    "topology",
    "cache_phase",
    "role",
    "repetition",
    "model",
    "reasoning_mode",
    "condition",
    "evidence_class",
)
GROUP_DIMENSIONS = ("lane", "arm", "topology", "cache_phase", "role", "model", "reasoning_mode", "condition", "evidence_class")
PAIR_DIMENSIONS = (
    "lane",
    "topology",
    "cache_phase",
    "role",
    "model",
    "reasoning_mode",
    "condition",
    "evidence_class",
    "seed",
)
CONDITION_PAIR_DIMENSIONS = (
    "lane",
    "arm",
    "topology",
    "cache_phase",
    "role",
    "model",
    "reasoning_mode",
    "evidence_class",
    "seed",
)
JUDGE_METRICS = ("answer_score", "rationale_score", "ar_score")
METRICS = (
    "execution_success",
    "turn_count",
    "repair_count",
    "query_retry_count",
    "empty_result_retry_count",
    "productive_retry_count",
    "single_agent_repair_count",
    "manager_repair_count",
    "specialist_repair_count",
    "tool_calls",
    "tool_failures",
    "tool_failure_rate",
    "handoff_count",
    "handoff_failures",
    "agent_tool_count",
    "agent_tool_failures",
    "abstention_count",
    "llm_reentries",
    "wall_ms",
    "adjusted_wall_ms",
    "observability_overhead_ms",
    "llm_ms",
    "tool_ms",
    "middleware_residual_ms",
    "database_observed_ms",
    "database_observed_cpu_ms",
    "middleware_process_cpu_s",
    "database_process_cpu_s",
    "shared_process_cpu_s",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "tool_call_tokens",
    "message_tokens",
    "manager_requests",
    "manager_input_tokens",
    "manager_output_tokens",
    "manager_reasoning_tokens",
    "specialist_requests",
    "specialist_input_tokens",
    "specialist_output_tokens",
    "specialist_reasoning_tokens",
    "cached_tokens",
    "cache_hit_ratio",
    "db_server_available_ms",
    "db_server_consumed_ms",
    "tool_argument_decode_ms",
    "query_validation_ms",
    "db_client_acquire_ms",
    "db_submit_ms",
    "db_first_result_ms",
    "db_iterate_hydrate_ms",
    "evidence_serialize_ms",
    "db_graph_load_ms",
    "result_rows",
    "result_bytes",
    "retrieved_triple_count",
    "retrieval_precision",
    "retrieval_recall",
    "retrieval_f1",
    "retrieval_triggered",
    "time_to_first_graph_tool_ms",
    "turns_before_first_graph_tool",
    "schema_discovery_calls",
    "graph_query_calls",
    "query_validated",
    "query_executed",
    "nonempty_result",
    "gold_evidence_retrieved",
    "effective_retrieval",
    "vllm_prefix_cache_queries",
    "vllm_prefix_cache_hits",
    "vllm_prefix_cache_hit_ratio",
    *JUDGE_METRICS,
)
PAIR_METRICS = (
    "execution_success",
    "turn_count",
    "repair_count",
    "query_retry_count",
    "empty_result_retry_count",
    "productive_retry_count",
    "tool_calls",
    "tool_failure_rate",
    "handoff_failures",
    "agent_tool_failures",
    "wall_ms",
    "adjusted_wall_ms",
    "observability_overhead_ms",
    "llm_ms",
    "tool_ms",
    "middleware_residual_ms",
    "database_observed_ms",
    "database_observed_cpu_ms",
    "db_server_available_ms",
    "db_server_consumed_ms",
    "tool_argument_decode_ms",
    "query_validation_ms",
    "db_client_acquire_ms",
    "db_submit_ms",
    "db_first_result_ms",
    "db_iterate_hydrate_ms",
    "evidence_serialize_ms",
    "db_graph_load_ms",
    "middleware_process_cpu_s",
    "database_process_cpu_s",
    "shared_process_cpu_s",
    "input_tokens",
    "output_tokens",
    "cache_hit_ratio",
    "retrieval_precision",
    "retrieval_recall",
    "retrieval_f1",
    "retrieval_triggered",
    "time_to_first_graph_tool_ms",
    "turns_before_first_graph_tool",
    "schema_discovery_calls",
    "graph_query_calls",
    "query_validated",
    "query_executed",
    "nonempty_result",
    "gold_evidence_retrieved",
    "effective_retrieval",
    *JUDGE_METRICS,
)
LOWER_IS_BETTER = frozenset(
    {
        "turn_count",
        "repair_count",
        "query_retry_count",
        "empty_result_retry_count",
        "tool_calls",
        "tool_failures",
        "tool_failure_rate",
        "handoff_failures",
        "wall_ms",
        "adjusted_wall_ms",
        "observability_overhead_ms",
        "llm_ms",
        "tool_ms",
        "middleware_residual_ms",
        "database_observed_ms",
        "database_observed_cpu_ms",
        "db_server_available_ms",
        "db_server_consumed_ms",
        "tool_argument_decode_ms",
        "query_validation_ms",
        "db_client_acquire_ms",
        "db_submit_ms",
        "db_first_result_ms",
        "db_iterate_hydrate_ms",
        "evidence_serialize_ms",
        "db_graph_load_ms",
        "middleware_process_cpu_s",
        "database_process_cpu_s",
        "shared_process_cpu_s",
        "input_tokens",
        "output_tokens",
        "time_to_first_graph_tool_ms",
        "turns_before_first_graph_tool",
    }
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _sum_numbers(values: Iterable[Any]) -> float | None:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return sum(numbers) if numbers else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _event_value(event: Mapping[str, Any], *names: str) -> Any:
    """Read a tool-stage value from flat or recorder-nested receipts."""
    details = event.get("details")
    nested = details if isinstance(details, Mapping) else {}
    for name in names:
        if name in event:
            return event.get(name)
        if name in nested:
            return nested.get(name)
    return None


def _sum_event_numbers(events: Iterable[Mapping[str, Any]], *names: str) -> float | None:
    return _sum_numbers(_event_value(event, *names) for event in events)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Iterable[Any], *, p99_min_n: int) -> dict[str, Any]:
    observed = [number for value in values if (number := _number(value)) is not None]
    result: dict[str, Any] = {"n": len(observed), "mean": None, "p50": None, "p95": None, "p99": None}
    if observed:
        result.update(
            {
                "mean": round(fmean(observed), 6),
                "p50": round(_quantile(observed, 0.50), 6),
                "p95": round(_quantile(observed, 0.95), 6),
            }
        )
        if len(observed) >= p99_min_n:
            result["p99"] = round(_quantile(observed, 0.99), 6)
    return result


def _canonical_lane(lane: str) -> str:
    for suffix in ("-AgentsSDK-Handoff", "-AgentsSDK-AgentTool"):
        if lane.endswith(suffix):
            return lane[: -len(suffix)]
    return lane


def _topology(lane: str, receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    configured = _nested(manifest, "config", "topology") or manifest.get("topology")
    if configured:
        return str(configured)
    role = str(receipt.get("role", ""))
    if (
        "AgentsSDK-Handoff" in lane
        or "AgentsSDK-AgentTool" in lane
        or "orchestrator" in role
        or "manager_specialist" in role
        or "handoff_count" in receipt
        or "agent_tool_count" in receipt
    ):
        return "multi_agent_shared_model"
    return "single_agent"


def _reasoning_mode(manifest: Mapping[str, Any]) -> str:
    value = _nested(manifest, "config", "enable_thinking")
    if value is None:
        value = _nested(manifest, "config", "reasoning_mode")
    if isinstance(value, bool):
        return "reasoning" if value else "non_reasoning"
    text = str(value or "unknown").casefold()
    if text in {"true", "on", "reasoning", "thinking"}:
        return "reasoning"
    if text in {"false", "off", "non_reasoning", "instant"}:
        return "non_reasoning"
    return str(value or "unknown")


def _vllm_metric_totals(receipt: Mapping[str, Any]) -> tuple[float | None, float | None]:
    windows: list[Mapping[str, Any]] = []
    direct = receipt.get("vllm_metrics_window")
    if isinstance(direct, Mapping):
        windows.append(direct)
    for turn in receipt.get("turns", ()) if isinstance(receipt.get("turns"), list) else ():
        if isinstance(turn, Mapping) and isinstance(turn.get("vllm_metrics_window"), Mapping):
            windows.append(turn["vllm_metrics_window"])
    queries: list[Any] = []
    hits: list[Any] = []
    for window in windows:
        delta = window.get("metrics_delta")
        if not isinstance(delta, Mapping):
            continue
        queries.append(delta.get("vllm:prefix_cache_queries"))
        hits.append(delta.get("vllm:prefix_cache_hits"))
    return _sum_numbers(queries), _sum_numbers(hits)


def _resource_metrics(receipt: Mapping[str, Any]) -> dict[str, float | None]:
    attribution = receipt.get("resource_attribution")
    if not isinstance(attribution, Mapping):
        return {
            "middleware_residual_ms": None,
            "database_observed_ms": None,
            "database_observed_cpu_ms": None,
            "middleware_process_cpu_s": None,
            "database_process_cpu_s": None,
            "shared_process_cpu_s": None,
        }
    observation = _nested(attribution, "layers", "database", "observation")
    observation = observation if isinstance(observation, Mapping) else {}
    process_window = attribution.get("process_window")
    processes = _nested(process_window, "processes") if isinstance(process_window, Mapping) else {}
    layers = attribution.get("process_role_layers")
    role_layers = layers if isinstance(layers, Mapping) else {}
    cpu_by_layer: dict[str, list[Any]] = defaultdict(list)
    if isinstance(processes, Mapping):
        for role, process in processes.items():
            if isinstance(process, Mapping):
                cpu_by_layer[str(role_layers.get(role, "unknown"))].append(process.get("cpu_total_s"))
    return {
        "middleware_residual_ms": _number(
            _nested(attribution, "critical_path_exclusive_ms", "middleware_orchestration_residual")
        ),
        "database_observed_ms": _first_number(
            observation.get("server_reported_ms"), observation.get("observed_wall_ms")
        ),
        "database_observed_cpu_ms": _number(observation.get("observed_thread_cpu_ms")),
        "middleware_process_cpu_s": _sum_numbers(cpu_by_layer.get("middleware", ())),
        "database_process_cpu_s": _sum_numbers(cpu_by_layer.get("database", ())),
        "shared_process_cpu_s": _sum_numbers(cpu_by_layer.get("shared_middleware_database_process", ())),
    }


def _normalize_metrics(receipt: Mapping[str, Any]) -> dict[str, float | None]:
    terminal = str(receipt.get("terminal") or receipt.get("terminal_status") or "")
    success_value = receipt.get("success")
    success = bool(success_value) if isinstance(success_value, bool) else terminal in {"final", "ok"}
    turns = receipt.get("turns") if isinstance(receipt.get("turns"), list) else []
    tools = receipt.get("tools")
    tool_events = tools if isinstance(tools, list) else (_nested(receipt, "tools", "events") or [])
    if not tool_events and isinstance(receipt.get("events"), list):
        tool_events = [event for event in receipt["events"] if isinstance(event, Mapping) and event.get("event") == "tool"]
    tool_calls = _number(_nested(receipt, "tools", "calls"))
    if tool_calls is None and isinstance(tool_events, list):
        tool_calls = float(len(tool_events))
    tool_failures = _number(_nested(receipt, "tools", "failures"))
    if tool_failures is None and isinstance(tool_events, list):
        tool_failures = float(sum(str(event.get("outcome", "ok")) not in {"ok", "empty"} for event in tool_events if isinstance(event, Mapping)))
    repair_count = _number(receipt.get("repair_count"))
    if repair_count is None:
        repair_count = _number(receipt.get("repair_failures"))
    query_events = [
        event
        for event in tool_events
        if isinstance(event, Mapping)
        and str(event.get("name", ""))
        in {"execute_cypher", "execute_sparql", "execute_graph_query", "text2cypher", "text2sparql"}
    ]
    query_retry_count = _number(receipt.get("query_retry_count"))
    if query_retry_count is None:
        query_retry_count = float(max(0, len(query_events) - 1))
    empty_result_retry_count = _number(receipt.get("empty_result_retry_count"))
    if empty_result_retry_count is None:
        empty_indices = [
            index
            for index, event in enumerate(query_events)
            if str(event.get("outcome", "ok")) in {"ok", "empty"}
            and (_number(_event_value(event, "rows", "evidence_count")) or 0.0) == 0.0
        ]
        empty_result_retry_count = float(
            sum(
                1
                for index in range(1, len(query_events))
                if any(empty_index < index for empty_index in empty_indices)
            )
        )
    else:
        empty_indices = [
            index
            for index, event in enumerate(query_events)
            if str(event.get("outcome", "ok")) in {"ok", "empty"}
            and (_number(_event_value(event, "rows", "evidence_count")) or 0.0) == 0.0
        ]
    failed_indices = [
        index
        for index, event in enumerate(query_events)
        if str(event.get("outcome", "ok")) not in {"ok", "empty"}
    ]
    productive_retry_count = _number(receipt.get("productive_retry_count"))
    if productive_retry_count is None:
        productive_retry_count = float(
            sum(
                1
                for index, event in enumerate(query_events)
                if index > 0
                and str(event.get("outcome", "")) == "ok"
                and (_number(_event_value(event, "rows", "evidence_count")) or 0.0) > 0.0
                and any(prior < index for prior in (*failed_indices, *empty_indices))
            )
        )
    time_block = receipt.get("time") if isinstance(receipt.get("time"), Mapping) else {}
    input_tokens = _number(receipt.get("accumulated_context_tokens"))
    if input_tokens is None:
        input_tokens = _sum_numbers(turn.get("input_tokens") for turn in turns if isinstance(turn, Mapping))
    output_tokens = _number(_nested(receipt, "generated_tokens", "total"))
    if output_tokens is None:
        output_tokens = _sum_numbers(turn.get("output_tokens") for turn in turns if isinstance(turn, Mapping))
    reasoning_tokens = _number(_nested(receipt, "generated_tokens", "thinking"))
    if reasoning_tokens is None:
        reasoning_tokens = _sum_numbers(
            turn.get("thinking_tokens") for turn in turns if isinstance(turn, Mapping)
        )
    tool_call_tokens = _number(_nested(receipt, "generated_tokens", "tool_call"))
    if tool_call_tokens is None:
        tool_call_tokens = _sum_numbers(
            turn.get("tool_call_tokens") for turn in turns if isinstance(turn, Mapping)
        )
    cached_tokens = _number(_nested(receipt, "cache", "provider_reported_cached_tokens"))
    if cached_tokens is None:
        cached_tokens = _sum_numbers(turn.get("cached_tokens") for turn in turns if isinstance(turn, Mapping))
    retrieval = receipt.get("retrieval") if isinstance(receipt.get("retrieval"), Mapping) else {}
    funnel = receipt.get("retrieval_funnel") if isinstance(receipt.get("retrieval_funnel"), Mapping) else {}
    queries, hits = _vllm_metric_totals(receipt)
    resource_metrics = _resource_metrics(receipt)
    typed_tool_events = [event for event in tool_events if isinstance(event, Mapping)]
    query_execution_receipts = receipt.get("query_executions")
    execution_events = (
        [event for event in query_execution_receipts if isinstance(event, Mapping)]
        if isinstance(query_execution_receipts, list) and query_execution_receipts
        else typed_tool_events
    )
    server_available = _sum_event_numbers(execution_events, "server_available_ms")
    server_consumed = _sum_event_numbers(execution_events, "server_consumed_ms")
    result_rows = _sum_event_numbers(execution_events, "rows")
    result_bytes = _sum_event_numbers(execution_events, "result_bytes")
    tool_argument_decode_ms = _sum_event_numbers(typed_tool_events, "tool_argument_decode_ms")
    query_validation_ms = _sum_event_numbers(execution_events, "validator_ms", "validation_ms")
    db_client_acquire_ms = _sum_event_numbers(execution_events, "pool_acquire_ms")
    db_submit_ms = _sum_event_numbers(execution_events, "bolt_submit_ms", "store_submit_ms")
    db_first_result_ms = _sum_event_numbers(execution_events, "first_record_ms", "first_result_ms")
    db_iterate_hydrate_ms = _sum_event_numbers(
        execution_events,
        "bolt_iterate_hydrate_ms",
        "hydrate_ms",
        "iterate_ms",
    )
    evidence_serialize_ms = _sum_event_numbers(
        execution_events,
        "evidence_json_encode_ms",
        "serialize_ms",
    )
    db_graph_load_ms = _first_number(
        *(_event_value(event, "load_ms") for event in execution_events)
    )
    delegation = receipt.get("delegation_usage") if isinstance(receipt.get("delegation_usage"), Mapping) else {}
    finalization = receipt.get("finalization_usage") if isinstance(receipt.get("finalization_usage"), Mapping) else {}
    manager = receipt.get("manager_usage") if isinstance(receipt.get("manager_usage"), Mapping) else {}
    specialist = receipt.get("specialist_usage") if isinstance(receipt.get("specialist_usage"), Mapping) else {}
    role_characteristics = (
        receipt.get("role_characteristics")
        if isinstance(receipt.get("role_characteristics"), Mapping)
        else {}
    )
    single_role = role_characteristics.get("single_agent")
    single_role = single_role if isinstance(single_role, Mapping) else {}
    manager_role = role_characteristics.get("manager")
    manager_role = manager_role if isinstance(manager_role, Mapping) else {}
    specialist_role = role_characteristics.get("query_specialist")
    specialist_role = specialist_role if isinstance(specialist_role, Mapping) else {}
    if input_tokens is None:
        input_tokens = _sum_numbers(
            (
                delegation.get("input_tokens"),
                finalization.get("input_tokens"),
                manager.get("input_tokens"),
                specialist.get("input_tokens"),
            )
        )
    if output_tokens is None:
        output_tokens = _sum_numbers(
            (
                delegation.get("output_tokens"),
                finalization.get("output_tokens"),
                manager.get("output_tokens"),
                specialist.get("output_tokens"),
            )
        )
    if reasoning_tokens is None:
        reasoning_tokens = _sum_numbers(
            (
                delegation.get("reasoning_tokens"),
                finalization.get("reasoning_tokens"),
                manager.get("reasoning_tokens"),
                specialist.get("reasoning_tokens"),
            )
        )
    if cached_tokens is None:
        cached_tokens = _sum_numbers(
            (
                delegation.get("cached_tokens"),
                finalization.get("cached_tokens"),
                manager.get("cached_tokens"),
                specialist.get("cached_tokens"),
            )
        )
    message_tokens = (
        max(0.0, output_tokens - (reasoning_tokens or 0.0) - (tool_call_tokens or 0.0))
        if output_tokens is not None
        else None
    )
    turn_count = _number(receipt.get("turn_count"))
    if turn_count is None and turns:
        turn_count = float(len(turns))
    if turn_count is None:
        turn_count = _sum_numbers(
            (
                delegation.get("requests"),
                finalization.get("requests"),
                manager.get("requests"),
                specialist.get("requests"),
            )
        )
    values: dict[str, float | None] = {
        "execution_success": float(success),
        "turn_count": turn_count,
        "repair_count": repair_count,
        "query_retry_count": query_retry_count,
        "empty_result_retry_count": empty_result_retry_count,
        "productive_retry_count": productive_retry_count,
        "single_agent_repair_count": _number(single_role.get("repair_attempts")),
        "manager_repair_count": _number(manager_role.get("repair_attempts")),
        "specialist_repair_count": _number(specialist_role.get("repair_attempts")),
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "tool_failure_rate": (tool_failures / tool_calls) if tool_calls and tool_failures is not None else (0.0 if tool_calls == 0 else None),
        "handoff_count": _number(receipt.get("handoff_count")),
        "handoff_failures": _number(receipt.get("handoff_failures")),
        "agent_tool_count": _number(receipt.get("agent_tool_count")),
        "agent_tool_failures": _number(receipt.get("agent_tool_failures")),
        "abstention_count": _first_number(
            receipt.get("abstention_count"),
            1.0
            if terminal == "abstained" or receipt.get("answer_status") == "abstained"
            else None,
        ),
        "llm_reentries": turn_count,
        "wall_ms": _first_number(time_block.get("wall_ms"), receipt.get("wall_ms")),
        "adjusted_wall_ms": _first_number(
            time_block.get("adjusted_wall_ms"),
            _nested(receipt, "resource_attribution", "critical_path_exclusive_ms", "workload_wall"),
        ),
        "observability_overhead_ms": _first_number(
            time_block.get("observability_overhead_ms"),
            _nested(receipt, "resource_attribution", "critical_path_exclusive_ms", "observability_overhead"),
        ),
        "llm_ms": _number(time_block.get("llm_ms")),
        "tool_ms": _number(time_block.get("tool_ms")),
        **resource_metrics,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "tool_call_tokens": tool_call_tokens,
        "message_tokens": message_tokens,
        "manager_requests": _number(manager.get("requests")),
        "manager_input_tokens": _number(manager.get("input_tokens")),
        "manager_output_tokens": _number(manager.get("output_tokens")),
        "manager_reasoning_tokens": _number(manager.get("reasoning_tokens")),
        "specialist_requests": _number(specialist.get("requests")),
        "specialist_input_tokens": _number(specialist.get("input_tokens")),
        "specialist_output_tokens": _number(specialist.get("output_tokens")),
        "specialist_reasoning_tokens": _number(specialist.get("reasoning_tokens")),
        "cached_tokens": cached_tokens,
        "cache_hit_ratio": _number(_nested(receipt, "cache", "provider_cache_hit_ratio")),
        "db_server_available_ms": server_available,
        "db_server_consumed_ms": server_consumed,
        "tool_argument_decode_ms": tool_argument_decode_ms,
        "query_validation_ms": query_validation_ms,
        "db_client_acquire_ms": db_client_acquire_ms,
        "db_submit_ms": db_submit_ms,
        "db_first_result_ms": db_first_result_ms,
        "db_iterate_hydrate_ms": db_iterate_hydrate_ms,
        "evidence_serialize_ms": evidence_serialize_ms,
        "db_graph_load_ms": db_graph_load_ms,
        "result_rows": _first_number(result_rows, funnel.get("result_rows"), receipt.get("row_count")),
        "result_bytes": result_bytes,
        "retrieved_triple_count": _number(receipt.get("retrieved_triple_count")),
        "retrieval_precision": _number(retrieval.get("precision")),
        "retrieval_recall": _number(retrieval.get("recall")),
        "retrieval_f1": _number(retrieval.get("f1")),
        "retrieval_triggered": _number(funnel.get("graph_tool_selected")),
        "time_to_first_graph_tool_ms": _number(funnel.get("time_to_first_graph_tool_ms")),
        "turns_before_first_graph_tool": _number(funnel.get("llm_turns_before_first_graph_tool")),
        "schema_discovery_calls": _number(funnel.get("schema_discovery_calls")),
        "graph_query_calls": _number(funnel.get("graph_query_calls")),
        "query_validated": _number(funnel.get("query_validated")),
        "query_executed": _number(funnel.get("query_executed")),
        "nonempty_result": _number(funnel.get("nonempty_result")),
        "gold_evidence_retrieved": _number(funnel.get("gold_evidence_retrieved")),
        "effective_retrieval": _number(funnel.get("effective_retrieval")),
        "vllm_prefix_cache_queries": queries,
        "vllm_prefix_cache_hits": hits,
        "vllm_prefix_cache_hit_ratio": (hits / queries) if queries and hits is not None else None,
        **{metric: None for metric in JUDGE_METRICS},
    }
    return values


def _failure_kinds(receipt: Mapping[str, Any], metrics: Mapping[str, float | None]) -> tuple[str, ...]:
    kinds: list[str] = []
    adjusted_wall = metrics.get("adjusted_wall_ms")
    llm_ms = metrics.get("llm_ms")
    tool_ms = metrics.get("tool_ms")
    if (
        adjusted_wall is not None
        and llm_ms is not None
        and tool_ms is not None
        and adjusted_wall + 2.0 < llm_ms + tool_ms
    ):
        kinds.append("invalid_latency_attribution")
    if metrics["execution_success"] != 1.0:
        kinds.append(str(receipt.get("terminal") or receipt.get("terminal_status") or "execution_failure"))
    if (metrics.get("tool_failures") or 0) > 0:
        kinds.append("tool_failure")
    if (metrics.get("handoff_failures") or 0) > 0:
        kinds.append("handoff_failure")
    if (metrics.get("agent_tool_failures") or 0) > 0:
        kinds.append("agent_tool_failure")
    if any(
        (metrics.get(metric) or 0) > 0
        for metric in ("repair_count", "query_retry_count", "empty_result_retry_count")
    ):
        kinds.append("repair_or_retry")
    if metrics.get("retrieval_triggered") == 0.0:
        kinds.append("retrieval_not_triggered")
    if metrics.get("query_executed") == 1.0 and metrics.get("nonempty_result") == 0.0:
        kinds.append("empty_result")
    if metrics.get("effective_retrieval") == 0.0:
        kinds.append("ineffective_retrieval")
    return tuple(dict.fromkeys(kinds))


@dataclass(frozen=True)
class RunSources:
    manifests: tuple[Path, ...]
    receipts: tuple[Path, ...]
    telemetry: tuple[Path, ...]


def discover_run_sources(roots: Sequence[str | Path]) -> RunSources:
    manifests: set[Path] = set()
    receipts: set[Path] = set()
    telemetry: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root)
        candidates = [root] if root.is_file() else list(root.rglob("*"))
        for path in candidates:
            if not path.is_file() or ".private." in path.name:
                continue
            if path.suffix == ".json":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                schema = str(payload.get("schema_version", "")) if isinstance(payload, Mapping) else ""
                if schema == "agentic_os.run_manifest.v1":
                    manifests.add(path)
                    telemetry.add(path)
                elif "receipt" in schema and isinstance(payload, Mapping) and isinstance(payload.get("cases"), list):
                    receipts.add(path)
                    telemetry.add(path)
            elif path.suffix == ".jsonl":
                try:
                    first = next(
                        (line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
                        "",
                    )
                    sample = json.loads(first) if first else None
                except (OSError, ValueError):
                    sample = None
                if isinstance(sample, Mapping) and sample.get("run_id") and (
                    sample.get("case_id") or sample.get("qid")
                ):
                    receipts.add(path)
                    telemetry.add(path)
            elif path.suffix == ".csv" and "metric" in str(path.parent).casefold():
                telemetry.add(path)
    return RunSources(tuple(sorted(manifests)), tuple(sorted(receipts)), tuple(sorted(telemetry)))


def _load_manifests(paths: Sequence[Path]) -> dict[str, Mapping[str, Any]]:
    manifests: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(payload.get("run_id", ""))
        if not run_id:
            raise ValueError(f"run manifest lacks run_id: {path}")
        if run_id in manifests and canonical_digest(manifests[run_id]) != canonical_digest(payload):
            raise ValueError(f"conflicting manifests for run_id {run_id}")
        manifests[run_id] = payload
    return manifests


def _iter_receipts(path: Path) -> Iterable[Mapping[str, Any]]:
    if path.suffix == ".jsonl":
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"invalid receipt JSON at {path}:{line_number}") from exc
            if not isinstance(payload, Mapping):
                raise ValueError(f"receipt is not an object at {path}:{line_number}")
            yield payload
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    for case in payload.get("cases", ()):
        if not isinstance(case, Mapping):
            raise ValueError(f"invalid case receipt in {path}")
        yield case


def normalize_attempts(sources: RunSources) -> list[dict[str, Any]]:
    manifests = _load_manifests(sources.manifests)
    attempts: list[dict[str, Any]] = []
    for path in sources.receipts:
        for receipt in _iter_receipts(path):
            run_id = str(receipt.get("run_id", ""))
            manifest = manifests.get(run_id, {})
            if not run_id and len(manifests) == 1:
                run_id, manifest = next(iter(manifests.items()))
            config = manifest.get("config") if isinstance(manifest.get("config"), Mapping) else {}
            lane_raw = str(manifest.get("lane") or receipt.get("benchmark") or "unknown")
            arm = str(receipt.get("arm") or manifest.get("arm") or config.get("arm") or "unknown")
            case_id = str(receipt.get("case_id") or receipt.get("qid") or "")
            if not run_id or not case_id:
                raise ValueError(f"receipt lacks run/case correlation: {path}")
            metrics = _normalize_metrics(receipt)
            canonical_lane = _canonical_lane(lane_raw)
            # GTSQA retrieval is an all-attempt primary endpoint. A terminal
            # execution failure retrieved no valid gold evidence and is scored
            # as zero, rather than disappearing through metric-wise deletion.
            if canonical_lane == "GTSQA-Text2SPARQL" and metrics["execution_success"] != 1.0:
                for metric in ("retrieval_precision", "retrieval_recall", "retrieval_f1"):
                    if metrics[metric] is None:
                        metrics[metric] = 0.0
            attempt = {
                "_schedule_lifecycle": str(receipt.get("schema_version", ""))
                == "agentic_os.schedule_attempt_lifecycle.v1",
                "run_id": run_id,
                "case_id": case_id,
                "lane": canonical_lane,
                "arm": arm,
                "topology": _topology(lane_raw, receipt, manifest),
                "cache_phase": str(receipt.get("cache_phase") or config.get("cache_phase") or "none"),
                "role": str(receipt.get("role") or "unknown"),
                "seed": int(receipt.get("seed") or config.get("seed") or 0),
                "repetition": int(receipt.get("repetition") or 0),
                "model": str(config.get("model") or manifest.get("model") or "unknown"),
                "reasoning_mode": _reasoning_mode(manifest),
                "condition": str(receipt.get("condition") or config.get("condition") or "tool_auto"),
                "evidence_class": str(
                    receipt.get("evidence_class")
                    or _nested(receipt, "retrieval_funnel", "evidence_class")
                    or config.get("evidence_class")
                    or "unspecified"
                ),
                "answer_status": str(receipt.get("answer_status") or "unknown"),
                "terminal_status": str(receipt.get("terminal") or receipt.get("terminal_status") or ("final" if metrics["execution_success"] else "execution_failure")),
                "error_code": str(receipt.get("error_code") or ""),
                "error_stage": str(receipt.get("error_stage") or ""),
                "error_fingerprint": str(receipt.get("error_fingerprint") or ""),
                "failure_kinds": list(_failure_kinds(receipt, metrics)),
                **metrics,
            }
            attempts.append(attempt)
    # The coordinator emits one lifecycle receipt for every scheduled attempt.
    # Preserve it when the child runner produced no case receipt (so failures
    # remain in the denominator), but do not count it as a second attempt when
    # the content-free child receipt exists.
    identity_fields = ("run_id", "case_id", "arm", "seed", "repetition", "cache_phase")
    identities_with_case_receipts = {
        tuple(attempt[field] for field in identity_fields)
        for attempt in attempts
        if not attempt["_schedule_lifecycle"]
    }
    attempts = [
        attempt
        for attempt in attempts
        if not attempt["_schedule_lifecycle"]
        or tuple(attempt[field] for field in identity_fields) not in identities_with_case_receipts
    ]
    for attempt in attempts:
        attempt.pop("_schedule_lifecycle", None)
    attempts.sort(key=lambda item: tuple(str(item[field]) for field in ATTEMPT_DIMENSIONS))
    return attempts


def load_judge_scores(path: str | Path) -> list[dict[str, Any]]:
    """Load already-produced blinded scores; no provider call occurs here."""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping) or not payload.get("run_id") or not (payload.get("case_id") or payload.get("qid")):
            raise ValueError(f"invalid judge score at line {line_number}")
        row = dict(payload)
        row["case_id"] = str(payload.get("case_id") or payload.get("qid"))
        for metric in JUDGE_METRICS:
            value = row.get(metric)
            if value is not None and (_number(value) is None or not 0 <= float(value) <= 1):
                raise ValueError(f"{metric} must be within [0,1] at line {line_number}")
        rows.append(row)
    return rows


def join_judge_scores(attempts: list[dict[str, Any]], scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Join blinded scores by correlation fields, rejecting ambiguous matches."""
    fields = ("run_id", "case_id", "arm", "seed", "repetition", "cache_phase", "role")
    index = {tuple(str(attempt[field]) for field in fields): attempt for attempt in attempts}
    matched = 0
    for score in scores:
        key = tuple(
            str(score.get(field, 0 if field == "seed" else ""))
            for field in fields
        )
        if key not in index:
            raise ValueError(f"judge score has no exact attempt match: {key}")
        attempt = index[key]
        if any(attempt[metric] is not None for metric in JUDGE_METRICS):
            raise ValueError(f"duplicate judge score for attempt: {key}")
        for metric in JUDGE_METRICS:
            attempt[metric] = _number(score.get(metric))
        matched += 1
    return {"schema_version": "agentic_os.blinded_judge_join.v1", "rows": len(scores), "matched": matched, "coverage": round(matched / len(attempts), 6) if attempts else None}


def aggregate_groups(attempts: Sequence[Mapping[str, Any]], *, p99_min_n: int = 100) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        buckets[tuple(str(attempt[field]) for field in GROUP_DIMENSIONS)].append(attempt)
    groups: list[dict[str, Any]] = []
    for key, rows in sorted(buckets.items()):
        dimensions = dict(zip(GROUP_DIMENSIONS, key))
        successes = sum(row.get("execution_success") == 1.0 for row in rows)
        groups.append(
            {
                **dimensions,
                "attempts_total": len(rows),
                "execution_successes": successes,
                "execution_failures": len(rows) - successes,
                "metrics": {metric: _distribution((row.get(metric) for row in rows), p99_min_n=p99_min_n) for metric in METRICS},
            }
        )
    return groups


def _bootstrap_paired(deltas: Sequence[float], *, samples: int, seed: int) -> tuple[float, float]:
    generator = random.Random(seed)
    estimates = []
    for _ in range(samples):
        draw = [deltas[generator.randrange(len(deltas))] for _ in deltas]
        estimates.append(fmean(draw))
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def paired_arm_comparisons(
    attempts: Sequence[Mapping[str, Any]],
    *,
    baseline_arm: str = "B0",
    bootstrap_samples: int = 2_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    strata: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        strata[tuple(str(attempt[field]) for field in PAIR_DIMENSIONS)].append(attempt)
    comparisons: list[dict[str, Any]] = []
    for stratum, rows in sorted(strata.items()):
        arms = sorted({str(row["arm"]) for row in rows})
        if baseline_arm not in arms:
            continue
        for treatment in (arm for arm in arms if arm != baseline_arm):
            baseline_qids = {str(row["case_id"]) for row in rows if row["arm"] == baseline_arm}
            treatment_qids = {str(row["case_id"]) for row in rows if row["arm"] == treatment}
            shared = sorted(baseline_qids & treatment_qids)
            metric_results: dict[str, Any] = {}
            for metric in PAIR_METRICS:
                deltas: list[float] = []
                for qid in shared:
                    baseline_values = [_number(row.get(metric)) for row in rows if row["arm"] == baseline_arm and str(row["case_id"]) == qid]
                    treatment_values = [_number(row.get(metric)) for row in rows if row["arm"] == treatment and str(row["case_id"]) == qid]
                    left = [value for value in baseline_values if value is not None]
                    right = [value for value in treatment_values if value is not None]
                    if left and right:
                        deltas.append(fmean(right) - fmean(left))
                if not deltas:
                    continue
                metric_seed = int(hashlib.sha256(f"{seed}|{stratum}|{treatment}|{metric}".encode()).hexdigest()[:16], 16)
                low, high = _bootstrap_paired(deltas, samples=bootstrap_samples, seed=metric_seed)
                metric_results[metric] = {
                    "paired_qids": len(deltas),
                    "mean_delta_treatment_minus_baseline": round(fmean(deltas), 6),
                    "ci95_low": round(low, 6),
                    "ci95_high": round(high, 6),
                    "preferred_direction": "lower" if metric in LOWER_IS_BETTER else "higher",
                }
            comparisons.append(
                {
                    **dict(zip(PAIR_DIMENSIONS, stratum)),
                    "baseline_arm": baseline_arm,
                    "treatment_arm": treatment,
                    "baseline_qids": len(baseline_qids),
                    "treatment_qids": len(treatment_qids),
                    "shared_qids": len(shared),
                    "unpaired_baseline_qids": len(baseline_qids - treatment_qids),
                    "unpaired_treatment_qids": len(treatment_qids - baseline_qids),
                    "bootstrap_unit": "case_id",
                    "bootstrap_samples": bootstrap_samples,
                    "metrics": metric_results,
                }
            )
    return comparisons


def paired_condition_comparisons(
    attempts: Sequence[Mapping[str, Any]],
    *,
    baseline_condition: str = "closed_book",
    treatment_condition: str = "tool_auto",
    bootstrap_samples: int = 2_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Compare endogenous tool access with closed book on the same cases."""
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    strata: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        if str(attempt.get("condition")) not in {baseline_condition, treatment_condition}:
            continue
        if str(attempt.get("evidence_class")) == "plumbing_smoke":
            continue
        strata[tuple(str(attempt[field]) for field in CONDITION_PAIR_DIMENSIONS)].append(attempt)
    comparisons: list[dict[str, Any]] = []
    for stratum, rows in sorted(strata.items()):
        baseline_qids = {str(row["case_id"]) for row in rows if row["condition"] == baseline_condition}
        treatment_qids = {str(row["case_id"]) for row in rows if row["condition"] == treatment_condition}
        shared = sorted(baseline_qids & treatment_qids)
        if not shared:
            continue
        metric_results: dict[str, Any] = {}
        for metric in PAIR_METRICS:
            deltas: list[float] = []
            for qid in shared:
                baseline_values = [
                    _number(row.get(metric))
                    for row in rows
                    if row["condition"] == baseline_condition and str(row["case_id"]) == qid
                ]
                treatment_values = [
                    _number(row.get(metric))
                    for row in rows
                    if row["condition"] == treatment_condition and str(row["case_id"]) == qid
                ]
                left = [value for value in baseline_values if value is not None]
                right = [value for value in treatment_values if value is not None]
                if left and right:
                    deltas.append(fmean(right) - fmean(left))
            if not deltas:
                continue
            metric_seed = int(
                hashlib.sha256(
                    f"{seed}|{stratum}|{baseline_condition}|{treatment_condition}|{metric}".encode()
                ).hexdigest()[:16],
                16,
            )
            low, high = _bootstrap_paired(deltas, samples=bootstrap_samples, seed=metric_seed)
            metric_results[metric] = {
                "paired_qids": len(deltas),
                "mean_delta_treatment_minus_baseline": round(fmean(deltas), 6),
                "ci95_low": round(low, 6),
                "ci95_high": round(high, 6),
                "preferred_direction": "lower" if metric in LOWER_IS_BETTER else "higher",
            }
        comparisons.append(
            {
                **dict(zip(CONDITION_PAIR_DIMENSIONS, stratum)),
                "baseline_condition": baseline_condition,
                "treatment_condition": treatment_condition,
                "baseline_qids": len(baseline_qids),
                "treatment_qids": len(treatment_qids),
                "shared_qids": len(shared),
                "unpaired_baseline_qids": len(baseline_qids - treatment_qids),
                "unpaired_treatment_qids": len(treatment_qids - baseline_qids),
                "bootstrap_unit": "case_id",
                "bootstrap_samples": bootstrap_samples,
                "metrics": metric_results,
            }
        )
    return comparisons


def failure_ledger(attempts: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for attempt in attempts:
        kinds = tuple(str(item) for item in attempt.get("failure_kinds", ()))
        if not kinds:
            continue
        counts.update(kinds)
        rows.append(
            {field: attempt[field] for field in ATTEMPT_DIMENSIONS}
            | {
                "answer_status": attempt["answer_status"],
                "terminal_status": attempt["terminal_status"],
                "error_code": attempt["error_code"],
                "error_stage": attempt["error_stage"],
                "error_fingerprint": attempt["error_fingerprint"],
                "failure_kinds": "|".join(kinds),
            }
        )
    return rows, dict(sorted(counts.items()))


def build_catalog(
    roots: Sequence[str | Path],
    *,
    baseline_arm: str = "B0",
    bootstrap_samples: int = 2_000,
    bootstrap_seed: int = 0,
    p99_min_n: int = 100,
    judge_scores_path: str | Path | None = None,
    privacy_sentinels: Sequence[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    sources = discover_run_sources(roots)
    root_paths = tuple(Path(root) for root in roots)

    def source_label(path: Path) -> str:
        for index, root in enumerate(root_paths):
            base = root.parent if root.is_file() else root
            try:
                return f"root{index}/{path.relative_to(base).as_posix()}"
            except ValueError:
                continue
        return f"external/{path.name}"

    privacy: dict[str, Any]
    if privacy_sentinels:
        privacy = assert_content_free(sources.telemetry, privacy_sentinels)
        privacy["status"] = "passed"
        privacy["checked_files"] = [source_label(Path(path)) for path in privacy["checked_files"]]
        privacy["findings"] = {source_label(Path(path)): values for path, values in privacy["findings"].items()}
    else:
        privacy = {"schema_version": "agentic_os.artifact_privacy_validation.v1", "status": "not_run", "passed": None, "checked_files": [], "sentinel_count": 0, "findings": {}}
    attempts = normalize_attempts(sources)
    judge_join = {"schema_version": "agentic_os.blinded_judge_join.v1", "rows": 0, "matched": 0, "coverage": 0.0 if attempts else None}
    if judge_scores_path:
        judge_join = join_judge_scores(attempts, load_judge_scores(judge_scores_path))
    groups = aggregate_groups(attempts, p99_min_n=p99_min_n)
    comparisons = paired_arm_comparisons(
        attempts,
        baseline_arm=baseline_arm,
        bootstrap_samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    condition_comparisons = paired_condition_comparisons(
        attempts,
        bootstrap_samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    failures, failure_counts = failure_ledger(attempts)
    source_files = [
        {"path": source_label(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(set(sources.manifests + sources.receipts))
    ]
    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "catalog_digest": "",
        "configuration": {
            "baseline_arm": baseline_arm,
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": bootstrap_seed,
            "p99_min_n": p99_min_n,
            "answer_metrics_source": "external_blinded_judge_extension" if judge_scores_path else "not_supplied",
        },
        "attempts_total": len(attempts),
        "groups": groups,
        "paired_arm_comparisons": comparisons,
        "paired_condition_comparisons": condition_comparisons,
        "failure_summary": {"attempts_with_failures": len(failures), "by_kind": failure_counts},
        "privacy_validation": privacy,
        "judge_join": judge_join,
        "sources": source_files,
    }
    catalog["catalog_digest"] = canonical_digest({key: value for key, value in catalog.items() if key != "catalog_digest"})
    return catalog, attempts, failures


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_catalog_outputs(
    output_dir: str | Path,
    catalog: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    diagnostic_fields = (
        "answer_status",
        "terminal_status",
        "error_code",
        "error_stage",
        "error_fingerprint",
        "failure_kinds",
    )
    _write_csv(root / "attempts.csv", attempts, (*ATTEMPT_DIMENSIONS, *diagnostic_fields, *METRICS))
    group_rows: list[dict[str, Any]] = []
    for group in catalog.get("groups", ()):  # type: ignore[union-attr]
        row = {field: group[field] for field in GROUP_DIMENSIONS}
        row.update({key: group[key] for key in ("attempts_total", "execution_successes", "execution_failures")})
        for metric, stats in group["metrics"].items():
            for statistic, value in stats.items():
                row[f"{metric}_{statistic}"] = value
        group_rows.append(row)
    group_fields = list(GROUP_DIMENSIONS) + ["attempts_total", "execution_successes", "execution_failures"]
    group_fields += [f"{metric}_{stat}" for metric in METRICS for stat in ("n", "mean", "p50", "p95", "p99")]
    _write_csv(root / "groups.csv", group_rows, group_fields)
    comparison_rows: list[dict[str, Any]] = []
    for comparison in catalog.get("paired_arm_comparisons", ()):  # type: ignore[union-attr]
        base = {key: value for key, value in comparison.items() if key != "metrics"}
        for metric, result in comparison["metrics"].items():
            comparison_rows.append({**base, "metric": metric, **result})
    comparison_fields = [*PAIR_DIMENSIONS, "baseline_arm", "treatment_arm", "baseline_qids", "treatment_qids", "shared_qids", "unpaired_baseline_qids", "unpaired_treatment_qids", "bootstrap_unit", "bootstrap_samples", "metric", "paired_qids", "mean_delta_treatment_minus_baseline", "ci95_low", "ci95_high", "preferred_direction"]
    _write_csv(root / "paired_comparisons.csv", comparison_rows, comparison_fields)
    condition_rows: list[dict[str, Any]] = []
    for comparison in catalog.get("paired_condition_comparisons", ()):  # type: ignore[union-attr]
        base = {key: value for key, value in comparison.items() if key != "metrics"}
        for metric, result in comparison["metrics"].items():
            condition_rows.append({**base, "metric": metric, **result})
    condition_fields = [
        *CONDITION_PAIR_DIMENSIONS,
        "baseline_condition",
        "treatment_condition",
        "baseline_qids",
        "treatment_qids",
        "shared_qids",
        "unpaired_baseline_qids",
        "unpaired_treatment_qids",
        "bootstrap_unit",
        "bootstrap_samples",
        "metric",
        "paired_qids",
        "mean_delta_treatment_minus_baseline",
        "ci95_low",
        "ci95_high",
        "preferred_direction",
    ]
    _write_csv(root / "paired_condition_comparisons.csv", condition_rows, condition_fields)
    _write_csv(root / "failures.csv", failures, (*ATTEMPT_DIMENSIONS, *diagnostic_fields))
    privacy = catalog.get("privacy_validation", {})
    analysis = [
        "# AgenticOS Graph-Agent Analysis Catalog",
        "",
        f"- Attempts (all outcomes): {catalog.get('attempts_total', 0)}",
        f"- Groups: {len(catalog.get('groups', []))}",
        f"- Paired arm comparisons: {len(catalog.get('paired_arm_comparisons', []))}",
        f"- Paired raw-condition comparisons: {len(catalog.get('paired_condition_comparisons', []))}",
        f"- Attempts with recorded failures/retries: {catalog.get('failure_summary', {}).get('attempts_with_failures', 0)}",
        f"- Privacy sentinel gate: {privacy.get('status', 'unknown')}",
        f"- Answer-quality source: {catalog.get('configuration', {}).get('answer_metrics_source')}",
        "",
        "All attempt counts retain failures in the denominator. Percentiles are p50/p95; p99 is emitted only when the configured minimum N is met. Paired confidence intervals resample case IDs, not individual repeated attempts. Answer and rationale scores are absent unless supplied by the separate blinded-judge extension.",
        "",
        "## Group Snapshot",
        "",
        "| Lane | Arm | Condition | Topology | Cache | Role | Attempts | Success | Retrieval F1 | Wall p50 ms | Turns p50 |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
        "",
    ]
    for group in catalog.get("groups", ()):  # type: ignore[union-attr]
        metrics = group["metrics"]
        analysis.append(
            "| {lane} | {arm} | {condition} | {topology} | {cache_phase} | {role} | {attempts_total} | {success} | {f1} | {wall} | {turns} |".format(
                **group,
                success=metrics["execution_success"]["mean"],
                f1=metrics["retrieval_f1"]["mean"],
                wall=metrics["wall_ms"]["p50"],
                turns=metrics["turn_count"]["p50"],
            )
        )
    analysis.extend(
        [
            "",
            "## Paired Arm Effects",
            "",
            "Treatment-minus-B0 effects and paired-QID bootstrap intervals are in `paired_comparisons.csv`. Missing or unpaired QIDs are disclosed in that file rather than silently discarded from cohort accounting.",
            "",
            "## Paired Raw-Condition Effects",
            "",
            "Tool-auto-minus-closed-book effects are in `paired_condition_comparisons.csv`. Forced-plumbing attempts are excluded by construction.",
            "",
            "Machine-readable details: `catalog.json`, `attempts.csv`, `groups.csv`, `paired_comparisons.csv`, `paired_condition_comparisons.csv`, and `failures.csv`.",
            "",
        ]
    )
    (root / "ANALYSIS.md").write_text("\n".join(analysis), encoding="utf-8")


__all__ = [
    "ATTEMPT_DIMENSIONS",
    "CATALOG_SCHEMA_VERSION",
    "GROUP_DIMENSIONS",
    "JUDGE_METRICS",
    "METRICS",
    "RunSources",
    "aggregate_groups",
    "build_catalog",
    "discover_run_sources",
    "failure_ledger",
    "join_judge_scores",
    "load_judge_scores",
    "normalize_attempts",
    "paired_arm_comparisons",
    "paired_condition_comparisons",
    "write_catalog_outputs",
]
