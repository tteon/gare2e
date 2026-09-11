#!/usr/bin/env python3
"""Run a bounded Graph-CoT (hop-action) ReAct baseline on GRBench (lab 017).

Same shape as ``run_direct_react_text2cypher.py`` — one agent, an
OpenAI-compatible vLLM endpoint, the stock Neo4j driver through the single
:class:`GRBenchDozerExecutor` boundary, content-free receipts — but the model
never writes Cypher.  It walks the graph with the four Graph-CoT actions
(``RetrieveNode``, ``NodeFeature``, ``NeighbourCheck``, ``NodeDegree``), each a
fixed parameterised template.  Because the actions take relation names as
arguments, the graph vocabulary is part of the system prompt in every arm:

* ``B0`` — tool signatures + node types / relation names (information-
  equivalent to the Text2Cypher lane's S1 slice);
* ``O1`` — plus relation meanings and directions (lab 016's O1 semantics).

Raw answers go to a private prediction file; receipts hold timings, token
counts and outcome codes only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from neo4j import GraphDatabase

from seocho.eval.agent_execution import build_retrieval_funnel
from seocho.eval.agentic_graph_agents import classify_answer_status, contains_unparsed_tool_markup
from seocho.eval.agentic_os_artifacts import Correlation, canonical_digest, require_fresh_outputs, write_manifest
from seocho.eval.agentic_os_observability import (
    HostProcessSampler,
    NvidiaSmiSampler,
    ProcessMetricsWindow,
    VllmMetricsClient,
    VllmMetricsWindow,
    build_three_layer_attribution,
    validate_gpu_samples,
)
from seocho.eval.agentic_os_trace import case_trace, inject_traceparent
from seocho.eval.grbench_graphcot import HOP_TOOL_NAMES, GraphCoTTools, graph_description, graphcot_tools
from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError
from seocho.eval.prompt_profiles import PROMPT_PROFILES, prompt_profile_receipt
from seocho.eval.raw_agentic_baseline import RAW_CONDITIONS, openai_tool_fields, raw_condition_spec

LANE = "GRBench-GraphCoT"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_questions(path: Path, limit: int, qids: set[str] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                if qids and str(item["qid"]) not in qids:
                    continue
                rows.append({"qid": str(item["qid"]), "question": str(item["question"]), "answer": str(item["answer"])})
            if len(rows) >= limit:
                break
    return rows


def _post(url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    span_id = inject_traceparent(headers)
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urlopen(request, timeout=180) as response:  # noqa: S310 - endpoint is an explicit experiment arg
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"LLM HTTP {error.code}: {detail}") from error
    if span_id is not None:
        result["_seocho_span_id"] = span_id
    return result, (time.perf_counter() - started) * 1000


def _usage_receipt(response: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    usage = response.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    message = choice.get("message") or {}
    return {
        "latency_ms": round(latency_ms, 3),
        "outcome": "ok",
        "output_kind": "tool_calls" if message.get("tool_calls") else "message",
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "thinking_tokens": completion_details.get("reasoning_tokens", usage.get("reasoning_tokens")),
        "tool_call_tokens": usage.get("tool_call_tokens"),
        "cached_tokens": details.get("cached_tokens"),
    }


def system_prompt(arm: str) -> str:
    return (
        "Classify the request before acting. For factual questions about papers, authors, venues, "
        "citations, authorship, counts or collaborations in the active benchmark graph, do not answer "
        "from memory: walk the graph with the tools, one hop per call. "
        + graph_description(semantics=(arm == "O1"))
        + " Start with RetrieveNode on the exact names or titles the question gives to obtain node ids; "
        "then use NeighbourCheck to move along a relation, NodeFeature to read a property, and NodeDegree "
        "when the question asks how many. Never invent an id; only use ids returned by a tool. Keep every "
        "call bounded: one node and one relation per call. When the returned evidence answers the question, "
        "reply with the answer and stop; when tools return nothing useful after reasonable attempts, say that "
        "the evidence is insufficient instead of guessing. Do not call tools for greetings or formatting."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--predictions-out", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--discovery-cache", type=Path, help="accepted for command-line parity with the Text2Cypher lane; not used (no schema tool)")
    parser.add_argument("--arm", choices=("B0", "O1"), default="B0")
    parser.add_argument("--run-id", default="graphcot-smoke")
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--metrics-url")
    parser.add_argument("--host-samples-out", type=Path)
    parser.add_argument("--gpu-samples-out", type=Path)
    parser.add_argument("--gpu-sample-interval-s", type=float, default=1.0)
    parser.add_argument("--db-pid", type=int)
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--bolt-url", default="bolt://127.0.0.1:7687", help="DozerDB Bolt endpoint (appliance default; override for local rehearsals)")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--qid", action="append", default=[])
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--token-budget", type=int, default=8_192)
    parser.add_argument("--row-cap", type=int, default=20)
    parser.add_argument("--byte-cap", type=int, default=12_000)
    parser.add_argument("--retrieve-cap", type=int, default=10)
    parser.add_argument("--enable-thinking", choices=("true", "false"), default="true")
    parser.add_argument("--condition", choices=RAW_CONDITIONS, default="tool_auto")
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="baseline", help="accepted for parity; the hop lane has one prompt")
    parser.add_argument("--profile-query-plans", action="store_true", help="accepted for parity; templates are not profiled")
    parser.add_argument("--stage", choices=("smoke", "main"), default="smoke")
    args = parser.parse_args()
    if args.token_budget <= 0 or args.max_output_tokens < 1 or args.max_turns < 1:
        raise ValueError("budgets must be positive")
    spec = raw_condition_spec(args.condition, stage=args.stage)
    if not spec.tools_enabled:
        raise ValueError("the Graph-CoT lane is tool-only; use the Text2Cypher lane for closed_book")
    require_fresh_outputs((args.receipt_out, args.predictions_out, args.manifest_out, args.host_samples_out, args.gpu_samples_out))
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set")
    questions = _load_questions(args.questions, args.limit, set(args.qid) or None)
    if args.qid and len(questions) != len(set(args.qid)):
        raise KeyError("one or more requested GRBench QIDs were not found")
    system = system_prompt(args.arm)
    tools = graphcot_tools()
    runtime_tool_schema_digest = canonical_digest({"tools": list(tools)})
    driver = GraphDatabase.driver(args.bolt_url, auth=("neo4j", password))
    executor = GRBenchDozerExecutor(
        driver, database=args.database, workspace_id=args.workspace_id, row_cap=args.row_cap, byte_cap=args.byte_cap,
        require_evidence_columns=False, require_anchor=True,
    )
    hop = GraphCoTTools(executor, retrieve_cap=args.retrieve_cap)
    metrics_client = VllmMetricsClient(args.metrics_url) if args.metrics_url else None
    process_roles = {"agent": os.getpid()}
    if args.db_pid:
        process_roles["dozerdb"] = args.db_pid
    sampler: HostProcessSampler | None = None
    if args.host_samples_out:
        sampler = HostProcessSampler(args.host_samples_out, roles=process_roles)
        sampler.start()
    gpu_sampler = NvidiaSmiSampler(args.gpu_samples_out, interval_s=args.gpu_sample_interval_s) if args.gpu_samples_out else None
    if gpu_sampler:
        gpu_sampler.start()
    cases: list[dict[str, Any]] = []
    predictions: dict[str, dict[str, str]] = {}
    try:
        for item in questions:
            with case_trace(args.run_id, item["qid"]) as case_trace_ctx:
                case_clock_started = time.perf_counter()
                process_window = ProcessMetricsWindow(process_roles)
                process_window.__enter__()
                wall_started = time.perf_counter()
                case_metrics_window = VllmMetricsWindow(metrics_client) if metrics_client else None
                if case_metrics_window:
                    case_metrics_window.__enter__()
                messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": item["question"]}]
                turns: list[dict[str, Any]] = []
                tool_events: list[dict[str, Any]] = []
                terminal = "turn_budget_exhausted"
                final_answer = ""
                any_tool = False
                budget_prompt_tokens = 0
                budget_completion_tokens = 0
                for _ in range(args.max_turns):
                    payload = {
                        "model": args.model,
                        "messages": messages,
                        "temperature": 0,
                        "max_tokens": args.max_output_tokens,
                        "chat_template_kwargs": {"enable_thinking": args.enable_thinking == "true"},
                        **openai_tool_fields(
                            spec, tools=tools, schema_observed=True, query_executed=any_tool,
                            schema_tool_name="RetrieveNode", query_tool_name="NeighbourCheck",
                        ),
                    }
                    try:
                        response, llm_ms = _post(f"{args.base_url.rstrip('/')}/v1/chat/completions", payload)
                    except Exception as exc:  # class only, never provider/body text
                        turns.append({"latency_ms": None, "outcome": type(exc).__name__, "output_kind": "error"})
                        terminal = "llm_error"
                        break
                    turn_receipt = _usage_receipt(response, llm_ms)
                    turn_receipt["started_elapsed_ms"] = round((time.perf_counter() - case_clock_started) * 1000 - llm_ms, 3)
                    if response.get("_seocho_span_id"):
                        turn_receipt["span_id"] = response["_seocho_span_id"]
                    budget_prompt_tokens += int(turn_receipt.get("input_tokens") or 0)
                    budget_completion_tokens += int(turn_receipt.get("output_tokens") or 0)
                    turns.append(turn_receipt)
                    if budget_prompt_tokens + budget_completion_tokens > args.token_budget:
                        terminal = "budget_exceeded"
                        final_answer = ""
                        break
                    choice = (response.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": message.get("tool_calls")})
                    calls = message.get("tool_calls") or []
                    if not calls:
                        candidate = str(message.get("content") or "")
                        if contains_unparsed_tool_markup(candidate):
                            turns[-1]["outcome"] = "tool_parse_error"
                            turns[-1]["output_kind"] = "protocol_error"
                            terminal = "tool_parse_error"
                            break
                        final_answer = candidate
                        terminal = "final"
                        break
                    # Graph-CoT allows several hops per turn; execute each call in order (bounded by the row cap each).
                    for call in calls[:4]:
                        tool_started = time.perf_counter()
                        function = call.get("function") or {}
                        tool_name = str(function.get("name", "unknown"))
                        decode_started = time.perf_counter()
                        try:
                            arguments = json.loads(function.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            arguments = {}
                        tool_argument_decode_ms = round((time.perf_counter() - decode_started) * 1000, 3)
                        try:
                            if tool_name not in HOP_TOOL_NAMES:
                                raise ValueError("unrecognized tool")
                            observation, detail = hop.call(tool_name, arguments if isinstance(arguments, dict) else {})
                            any_tool = True
                            outcome = "ok"
                        except Exception as exc:
                            error_code = exc.code if isinstance(exc, QueryPolicyError) else "database_or_runtime_error"
                            observation = json.dumps({"error_code": error_code})
                            detail = {"validator_ms": None, "db_execute_ms": None, "rows": 0, "result_bytes": len(observation), "error_class": type(exc).__name__, "policy_code": error_code}
                            outcome = "rejected"
                        detail.update({
                            "name": tool_name,
                            "outcome": outcome,
                            "latency_ms": round((time.perf_counter() - tool_started) * 1000, 3),
                            "tool_argument_decode_ms": tool_argument_decode_ms,
                        })
                        detail.setdefault("started_elapsed_ms", round((tool_started - case_clock_started) * 1000, 3))
                        tool_events.append(detail)
                        messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": observation})
                if case_metrics_window:
                    case_metrics_window.__exit__(None, None, None)
                case_vllm_metrics = case_metrics_window.receipt() if case_metrics_window else None
                wall_ms = round((time.perf_counter() - wall_started) * 1000, 3)
                process_window.__exit__(None, None, None)
                process_receipt = process_window.receipt()
                predictions[item["qid"]] = {"prediction": final_answer, "answer": item["answer"]}
                correlation = Correlation(
                    run_id=args.run_id, case_id=item["qid"], arm=args.arm, repetition=args.repetition,
                    cache_phase=args.cache_phase, role="single_agent", seed=args.seed,
                ).receipt_fields()
                ok_events = [e for e in tool_events if e.get("outcome") == "ok"]
                failed_indices = [i for i, e in enumerate(tool_events) if e.get("outcome") != "ok"]
                empty_indices = [i for i, e in enumerate(tool_events) if e.get("outcome") == "ok" and int(e.get("rows") or 0) == 0]
                repair_attempts = sum(1 for i in range(1, len(tool_events)) if any(f < i for f in failed_indices))
                repair_successes = sum(1 for i, e in enumerate(tool_events) if e.get("outcome") == "ok" and any(f < i for f in failed_indices))
                empty_result_retries = sum(1 for i in range(1, len(tool_events)) if any(x < i for x in empty_indices))
                result_rows = sum(int(e.get("rows") or 0) for e in ok_events)
                evidence_status = "ok" if result_rows else ("empty" if tool_events else "not_called")
                answer_status = classify_answer_status(final_answer, evidence_status=evidence_status)
                if terminal == "final":
                    terminal = {"abstained": "abstained", "empty": "empty_answer", "unsupported_answer": "unsupported_answer"}.get(answer_status, terminal)
                completed = terminal == "final" and answer_status in {"answered", "direct_answer"}
                llm_ms = round(sum(t.get("latency_ms") or 0 for t in turns), 3)
                tool_ms = round(sum(t.get("latency_ms") or 0 for t in tool_events), 3)
                observability_overhead_ms = round(float((case_vllm_metrics or {}).get("observer_overhead_ms") or 0), 3)
                server_reported_ms = round(sum(float(e.get("server_available_ms") or 0) + float(e.get("server_consumed_ms") or 0) for e in ok_events), 3)
                hops_by_tool = {name: sum(1 for e in tool_events if e.get("name") == name) for name in HOP_TOOL_NAMES}
                case_receipt = {
                    **correlation,
                    "trace": case_trace_ctx.receipt(),
                    "qid": item["qid"],
                    "terminal": terminal,
                    "answer_status": answer_status,
                    "success": completed,
                    "turn_count": len(turns),
                    "repair_count": repair_attempts,
                    "query_retry_count": max(0, len(tool_events) - 1),
                    "empty_result_retry_count": empty_result_retries,
                    "role_characteristics": {
                        "single_agent": {
                            "schema_calls": 0,
                            "query_calls": len(tool_events),
                            "query_failures": len(failed_indices),
                            "repair_attempts": repair_attempts,
                            "repair_successes": repair_successes,
                            "query_retries": max(0, len(tool_events) - 1),
                            "empty_result_retries": empty_result_retries,
                            "hops_by_tool": hops_by_tool,
                        }
                    },
                    "time": {
                        "wall_ms": wall_ms,
                        "adjusted_wall_ms": round(max(0.0, wall_ms - observability_overhead_ms), 3),
                        "observability_overhead_ms": observability_overhead_ms,
                        "llm_ms": llm_ms,
                        "tool_ms": tool_ms,
                    },
                    "turns": turns,
                    "tools": tool_events,
                    "budget": {
                        "limit": args.token_budget,
                        "prompt_tokens": budget_prompt_tokens,
                        "completion_tokens": budget_completion_tokens,
                        "observed_total": budget_prompt_tokens + budget_completion_tokens,
                        "exceeded": budget_prompt_tokens + budget_completion_tokens > args.token_budget,
                        "enforcement": "post_response_fail_closed",
                    },
                    "condition": args.condition,
                    "evidence_class": spec.evidence_class,
                    "query_plan_replays": [],
                }
                if case_vllm_metrics:
                    case_receipt["vllm_metrics_window"] = case_vllm_metrics
                case_receipt["resource_attribution"] = build_three_layer_attribution(
                    wall_ms=wall_ms, llm_ms=llm_ms, tool_ms=tool_ms, process_window=process_receipt,
                    process_role_layers={"agent": "middleware", **({"dozerdb": "database"} if args.db_pid else {})},
                    database_observation={"source": "bolt_server_summary", "server_reported_ms": server_reported_ms, "query_calls": len(ok_events)},
                    vllm_window_scope="serialized_case" if metrics_client else "absent",
                    observability_overhead_ms=observability_overhead_ms,
                )
                case_receipt["retrieval_funnel"] = build_retrieval_funnel(
                    turns=turns, tools=tool_events, success=completed, evidence_class=spec.evidence_class,
                    forced_tool=spec.forced, query_validated=bool(ok_events), query_executed=bool(ok_events),
                    result_rows=result_rows if ok_events else None,
                )
                cases.append(case_receipt)
    finally:
        driver.close()
        if sampler:
            sampler.stop()
        if gpu_sampler:
            gpu_sampler.stop()
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.predictions_out.parent.mkdir(parents=True, exist_ok=True)
    args.predictions_out.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = {
        "model": args.model, "workspace_id": args.workspace_id, "database": args.database, "initial_context": "question_only",
        "max_turns": args.max_turns, "token_budget": args.token_budget, "row_cap": args.row_cap, "byte_cap": args.byte_cap,
        "retrieve_cap": args.retrieve_cap, "enable_thinking": args.enable_thinking, "arm": args.arm, "condition": args.condition,
        "stage": args.stage, "run_id": args.run_id, "seed": args.seed, "repetition": args.repetition, "cache_phase": args.cache_phase,
        "evidence_class": spec.evidence_class, "tool_schema_digest": runtime_tool_schema_digest, "system_prompt_digest": _digest(system),
        "action_set": list(HOP_TOOL_NAMES), **prompt_profile_receipt(args.prompt_profile, "cypher"),
    }
    receipt = {
        "schema_version": "agentic_os.grbench_graphcot_receipt.v1",
        "execution_surface": "direct_openai_compatible_vllm_and_stock_neo4j_driver_via_hop_templates",
        "config": config,
        "cases": cases,
        "gpu_sample_validation": validate_gpu_samples(args.gpu_samples_out, max_gap_s=max(2.5, args.gpu_sample_interval_s * 2.5)) if args.gpu_samples_out else None,
    }
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.manifest_out:
        write_manifest(args.manifest_out, {
            "schema_version": "agentic_os.run_manifest.v1", "run_id": args.run_id, "lane": LANE, "arm": args.arm, "config": config,
            "question_file_sha256": hashlib.sha256(args.questions.read_bytes()).hexdigest(),
        })
    print(json.dumps({"cases": len(cases), "final": sum(case["success"] for case in cases)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
