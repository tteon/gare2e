#!/usr/bin/env python3
"""Run a bounded, SEOCHO-free ReAct Text2Cypher baseline on GRBench.

The agent talks directly to an OpenAI-compatible vLLM endpoint and uses the
stock Neo4j driver against DozerDB.  It does not import SEOCHO code.  Raw
answers are kept in a caller-designated local prediction file for later blind
evaluation; the receipt file never contains prompts, answers, queries, tool
arguments, observations, or chain-of-thought.
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

from seocho.eval.agentic_os_artifacts import (
    Correlation,
    canonical_digest,
    require_fresh_outputs,
    write_manifest,
)
from seocho.eval.agentic_os_replay import append_private_event
from seocho.eval.agentic_os_trace import case_trace, inject_traceparent
from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError
from seocho.eval.agentic_graph_agents import (
    classify_answer_status,
    contains_unparsed_tool_markup,
    grbench_react_tools,
)
from seocho.eval.agentic_os_observability import (
    HostProcessSampler,
    NvidiaSmiSampler,
    ProcessMetricsWindow,
    VllmMetricsClient,
    VllmMetricsWindow,
    build_three_layer_attribution,
    validate_gpu_samples,
)
from seocho.eval.agent_execution import build_retrieval_funnel
from seocho.eval.prompt_profiles import PROMPT_PROFILES, prompt_profile_receipt, system_prompt_addendum
from seocho.eval.raw_agentic_baseline import RAW_CONDITIONS, openai_tool_fields, raw_condition_spec




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
    span_id = inject_traceparent(headers)  # W3C trace context for the bound case (no-op otherwise)
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


def _schema_slice() -> str:
    """Return the neutral LPG discovery result only after an explicit tool call."""
    return json.dumps(
        {
            "nodes": [{"label": "GRBenchNode", "properties": ["id", "node_type", "source_node_id", "feature_*"]}],
            "relationships": [{"type": "GRBENCH_REL", "properties": ["relation_name", "source_node_type"]}],
            "query_scope": "Every node/relationship pattern must filter _workspace_id: $workspace_id. The tool binds this parameter.",
        },
        separators=(",", ":"),
    )


def _load_discovery_cache(
    path: Path | None,
    arm: str,
    *,
    expected_tool_schema_digest: str | None = None,
) -> tuple[dict[str, str], str | None]:
    if path is None:
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "seocho.agentic_os.discovery_cache.v1":
        raise ValueError("unsupported discovery cache")
    if expected_tool_schema_digest is not None:
        observed = str((payload.get("contract") or {}).get("tool_schema_digest", ""))
        if observed != expected_tool_schema_digest:
            raise ValueError("discovery cache tool-schema digest does not match the runtime schema")
    responses: dict[str, str] = {}
    for case in payload.get("cases", []):
        qid = str(case.get("qid", ""))
        by_arm = case.get("responses", {})
        if not qid or arm not in by_arm:
            raise ValueError(f"discovery cache lacks {arm} response for {qid or '<empty>'}")
        responses[qid] = json.dumps(by_arm[arm], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return responses, str(payload.get("cache_digest") or canonical_digest(payload))


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--predictions-out", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--arm", choices=("B0", "C-", "S1", "O1"), default="B0", help="discovery context arm; O1 = ontology-enriched vocabulary (lab 016)")
    parser.add_argument("--run-id", default="direct-react-smoke")
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--metrics-url")
    parser.add_argument(
        "--profile-query-plans",
        action="store_true",
        help="Replay successful read-only Cypher queries under PROFILE outside E2E timing.",
    )
    parser.add_argument("--host-samples-out", type=Path)
    parser.add_argument("--gpu-samples-out", type=Path)
    parser.add_argument("--gpu-sample-interval-s", type=float, default=1.0)
    parser.add_argument("--db-pid", type=int)
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--qid", action="append", default=[])
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument(
        "--token-budget",
        type=int,
        default=8_192,
        help="Fixed per-case prompt+completion token ceiling shared by every comparison arm.",
    )
    parser.add_argument("--row-cap", type=int, default=20)
    parser.add_argument("--byte-cap", type=int, default=12_000)
    parser.add_argument("--enable-thinking", choices=("true", "false"), default="true")
    parser.add_argument("--condition", choices=RAW_CONDITIONS, default="tool_auto")
    parser.add_argument(
        "--prompt-profile",
        choices=PROMPT_PROFILES,
        default="baseline",
        help="Prompt-engineering arm: baseline system prompt, or strengthened (explicit scope pattern, repair table, finalisation rule).",
    )
    parser.add_argument("--stage", choices=("smoke", "main"), default="smoke")
    parser.add_argument(
        "--zero-row-hint",
        action="store_true",
        help="lab 021: on an empty result the executor appends a content-free diagnosis (anchor resolved?, outgoing degree per relation) to the tool result.",
    )
    parser.add_argument("--replay-out", type=Path, help="Opt-in private fixed-request capture for serving diagnostics")
    args = parser.parse_args()
    if args.replay_out is not None and ".private." not in args.replay_out.name:
        raise ValueError("replay capture must have a .private. filename")
    if args.token_budget <= 0:
        raise ValueError("--token-budget must be positive")
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be positive")
    spec = raw_condition_spec(args.condition, stage=args.stage)
    if args.arm != "B0" and args.condition != "tool_auto":
        raise ValueError("closed_book and forced_plumbing are SEOCHO-free B0 conditions")
    if args.stage == "main" and spec.tools_enabled and args.discovery_cache is None:
        raise ValueError("main tool_auto requires a frozen discovery cache")
    require_fresh_outputs(
        (args.receipt_out, args.predictions_out, args.manifest_out, args.host_samples_out, args.gpu_samples_out, args.replay_out)
    )
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if spec.tools_enabled and not password:
        raise RuntimeError("NEO4J_PASSWORD must be set for a tool-enabled condition")
    questions = _load_questions(args.questions, args.limit, set(args.qid) or None)
    if args.qid and len(questions) != len(set(args.qid)):
        raise KeyError("one or more requested GRBench QIDs were not found")
    system = (
        "Classify the request before answering. For factual questions about named entities, "
        "attributes, relations, paths, counts, citations, authorship, or provenance in the active "
        "benchmark graph, do not answer from memory. You start with no schema: call "
        "inspect_graph_schema exactly once, then form bounded read-only Cypher using only returned "
        "labels, properties, relationship types, and scope rules, and call execute_cypher. Anchor "
        "every pattern on an indexed property (id, source_node_id, feature_name or feature_title "
        "via =, IN, CONTAINS or STARTS WITH); unanchored scans are rejected as unanchored_scan. On a "
        "rejection, change the query using its error code; never repeat identical Cypher. Answer "
        "only from non-empty evidence, or explicitly abstain when evidence is empty or failed."
        " Do not call graph tools for greetings, formatting requests, or facts already supplied in "
        "the conversation."
        if spec.tools_enabled
        else "Answer the user question concisely. Do not expose chain-of-thought."
    )
    if spec.tools_enabled:
        system += system_prompt_addendum(args.prompt_profile, "cypher")
    schema_tool, query_tool = grbench_react_tools()
    runtime_tool_schema_digest = canonical_digest({"tools": [schema_tool, query_tool]})
    discovery_by_qid, discovery_cache_digest = _load_discovery_cache(
        args.discovery_cache,
        args.arm,
        expected_tool_schema_digest=(
            canonical_digest(schema_tool["function"])
            if args.stage == "main" and spec.tools_enabled
            else None
        ),
    )
    driver = (
        GraphDatabase.driver("bolt://127.0.0.1:7687", auth=("neo4j", password))
        if spec.tools_enabled
        else None
    )
    # The only Bolt execution boundary (seocho-5qmw): validator, transaction
    # timeout, caps and receipt fields live in GRBenchDozerExecutor.
    executor = (
        GRBenchDozerExecutor(
            driver, database=args.database, workspace_id=args.workspace_id, row_cap=args.row_cap, byte_cap=args.byte_cap,
            require_evidence_columns=False,  # the ReAct runner feeds raw rows back to the model
            require_anchor=os.getenv("SEOCHO_GRBENCH_REQUIRE_ANCHOR", "1") == "1",
            zero_row_hint=args.zero_row_hint,
        )
        if driver is not None
        else None
    )
    metrics_client = VllmMetricsClient(args.metrics_url) if args.metrics_url else None
    process_roles = {"agent": os.getpid()}
    if args.db_pid and spec.tools_enabled:
        process_roles["dozerdb"] = args.db_pid
    sampler: HostProcessSampler | None = None
    if args.host_samples_out:
        sampler = HostProcessSampler(args.host_samples_out, roles=process_roles)
        sampler.start()
    gpu_sampler = (
        NvidiaSmiSampler(args.gpu_samples_out, interval_s=args.gpu_sample_interval_s)
        if args.gpu_samples_out
        else None
    )
    if gpu_sampler:
        gpu_sampler.start()
    cases: list[dict[str, Any]] = []
    predictions: dict[str, dict[str, str]] = {}
    try:
        for item in questions:
          with case_trace(args.run_id, item["qid"]) as case_trace_ctx:
              case_clock_started = time.perf_counter()  # absolute offsets for turns/tools (OTel spans)
              process_window = ProcessMetricsWindow(process_roles)
              process_window.__enter__()
              wall_started = time.perf_counter()
              case_metrics_window = VllmMetricsWindow(metrics_client) if metrics_client else None
              if case_metrics_window:
                  case_metrics_window.__enter__()
              messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": item["question"]}]
              turns: list[dict[str, Any]] = []
              tools: list[dict[str, Any]] = []
              profile_queries: list[str] = []
              terminal = "turn_budget_exhausted"
              final_answer = ""
              schema_observed = False
              query_executed = False
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
                          spec,
                          tools=(schema_tool, query_tool),
                          schema_observed=schema_observed,
                          query_executed=query_executed,
                          schema_tool_name="inspect_graph_schema",
                          query_tool_name="execute_cypher",
                      ),
                  }
                  try:
                      response, llm_ms = _post(f"{args.base_url.rstrip('/')}/v1/chat/completions", payload)
                  except Exception as exc:  # retained only as class, never provider/body text
                      turns.append({"latency_ms": None, "outcome": type(exc).__name__, "output_kind": "error"})
                      terminal = "llm_error"
                      break
                  append_private_event(args.replay_out, {"kind": "llm", "payload": payload,
                      "input_tokens": (response.get("usage") or {}).get("prompt_tokens"),
                      "output_tokens": (response.get("usage") or {}).get("completion_tokens")})
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
                  for call in calls[:1]:
                      tool_started = time.perf_counter()
                      arguments: dict[str, Any] = {}
                      tool_argument_decode_ms: float | None = None
                      try:
                          function = call.get("function") or {}
                          tool_name = str(function.get("name", ""))
                          decode_started = time.perf_counter()
                          arguments = json.loads(function.get("arguments") or "{}")
                          tool_argument_decode_ms = round(
                              (time.perf_counter() - decode_started) * 1000,
                              3,
                          )
                          if tool_name == "inspect_graph_schema":
                              observation = discovery_by_qid.get(item["qid"])
                              if observation is None:
                                  if args.discovery_cache:
                                      raise ValueError("qid missing from frozen discovery cache")
                                  observation = _schema_slice()
                              detail = {"schema_slice_digest": _digest(observation), "result_bytes": len(observation), "rows": None}
                              schema_observed = True
                          elif tool_name == "execute_cypher":
                              if driver is None:
                                  raise RuntimeError("graph driver unavailable")
                              observation, detail = executor.execute_rows(str(arguments.get("query", "")))
                              query_executed = True
                              profile_queries.append(str(arguments.get("query", "")))
                          else:
                              raise ValueError("unrecognized tool")
                          outcome = "ok"
                      except Exception as exc:
                          error_code = (
                              exc.code
                              if isinstance(exc, QueryPolicyError)
                              else "database_or_runtime_error"
                          )
                          observation = json.dumps({"error_code": error_code})
                          attempted_query = (
                              str(arguments.get("query", ""))
                              if isinstance(arguments, dict)
                              else ""
                          )
                          detail = {"validator_ms": None, "db_execute_ms": None, "rows": 0, "result_bytes": len(observation), "query_digest": _digest(attempted_query) if attempted_query else None, "error_code": error_code}
                          outcome = "rejected"
                      detail.update(
                          {
                              "name": str((call.get("function") or {}).get("name", "unknown")),
                              "outcome": outcome,
                              "latency_ms": round((time.perf_counter() - tool_started) * 1000, 3),
                              "tool_argument_decode_ms": tool_argument_decode_ms,
                          }
                      )
                      detail.setdefault("started_elapsed_ms", round((tool_started - case_clock_started) * 1000, 3))
                      append_private_event(args.replay_out, {"kind": "tool", "name": detail["name"],
                          "query": str(arguments.get("query", "")) if isinstance(arguments, dict) else "",
                          "observation": observation, "outcome": outcome, "error_code": detail.get("error_code"),
                          "workspace_id": args.workspace_id, "row_cap": args.row_cap, "byte_cap": args.byte_cap})
                      tools.append(detail)
                      messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": observation})
              if case_metrics_window:
                  case_metrics_window.__exit__(None, None, None)
              case_vllm_metrics = case_metrics_window.receipt() if case_metrics_window else None
              case_execution_wall_ms = round((time.perf_counter() - wall_started) * 1000, 3)
              process_window.__exit__(None, None, None)
              process_receipt = process_window.receipt()
              plan_replays: list[dict[str, Any]] = []
              if args.profile_query_plans and driver is not None:
                  for query in profile_queries:
                      try:
                          plan_replays.append(
                              executor.profile_select(query)
                          )
                      except Exception as exc:
                          plan_replays.append(
                              {
                                  "schema_version": "agentic_os.dozer_profile_replay.v1",
                                  "scope": "separate_read_only_diagnostic_replay",
                                  "error_class": type(exc).__name__,
                              }
                          )
              predictions[item["qid"]] = {"prediction": final_answer, "answer": item["answer"]}
              correlation = Correlation(
                  run_id=args.run_id,
                  case_id=item["qid"],
                  arm=args.arm,
                  repetition=args.repetition,
                  cache_phase=args.cache_phase,
                  role="single_agent",
                  seed=args.seed,
              ).receipt_fields()
              successful_query_events = [
                  event
                  for event in tools
                  if event.get("name") == "execute_cypher" and event.get("outcome") == "ok"
              ]
              query_events = [event for event in tools if event.get("name") == "execute_cypher"]
              failed_query_indices = [
                  index for index, event in enumerate(query_events) if event.get("outcome") != "ok"
              ]
              empty_query_indices = [
                  index
                  for index, event in enumerate(query_events)
                  if event.get("outcome") == "ok" and int(event.get("rows") or 0) == 0
              ]
              repair_attempts = sum(
                  1
                  for index in range(1, len(query_events))
                  if any(failed < index for failed in failed_query_indices)
              )
              repair_successes = sum(
                  1
                  for index, event in enumerate(query_events)
                  if event.get("outcome") == "ok"
                  and any(failed < index for failed in failed_query_indices)
              )
              empty_result_retries = sum(
                  1
                  for index in range(1, len(query_events))
                  if any(empty < index for empty in empty_query_indices)
              )
              result_rows = sum(int(event.get("rows") or 0) for event in successful_query_events)
              evidence_status = "ok" if result_rows else ("empty" if query_events else "not_called")
              answer_status = classify_answer_status(final_answer, evidence_status=evidence_status)
              if terminal == "final":
                  terminal = {
                      "abstained": "abstained",
                      "empty": "empty_answer",
                      "unsupported_answer": "unsupported_answer",
                  }.get(answer_status, terminal)
              completed = terminal == "final" and answer_status in {"answered", "direct_answer"}
              wall_ms = case_execution_wall_ms
              llm_ms = round(sum(t.get("latency_ms") or 0 for t in turns), 3)
              tool_ms = round(sum(t.get("latency_ms") or 0 for t in tools), 3)
              observability_overhead_ms = round(
                  float((case_vllm_metrics or {}).get("observer_overhead_ms") or 0),
                  3,
              )
              adjusted_wall_ms = round(max(0.0, wall_ms - observability_overhead_ms), 3)
              server_reported_ms = round(
                  sum(
                      float(event.get("server_available_ms") or 0)
                      + float(event.get("server_consumed_ms") or 0)
                      for event in successful_query_events
                  ),
                  3,
              )
              case_receipt = {
                  **correlation,
                  "trace": case_trace_ctx.receipt(),
                  "qid": item["qid"],
                  "terminal": terminal,
                  "answer_status": answer_status,
                  "success": completed,
                  "turn_count": len(turns),
                  "repair_count": repair_attempts,
                  "query_retry_count": max(0, len(query_events) - 1),
                  "empty_result_retry_count": empty_result_retries,
                  "role_characteristics": {
                      "single_agent": {
                          "schema_calls": sum(event.get("name") == "inspect_graph_schema" for event in tools),
                          "query_calls": len(query_events),
                          "query_failures": len(failed_query_indices),
                          "repair_attempts": repair_attempts,
                          "repair_successes": repair_successes,
                          "query_retries": max(0, len(query_events) - 1),
                          "empty_result_retries": empty_result_retries,
                      }
                  },
                  "time": {
                      "wall_ms": wall_ms,
                      "adjusted_wall_ms": adjusted_wall_ms,
                      "observability_overhead_ms": observability_overhead_ms,
                      "llm_ms": llm_ms,
                      "tool_ms": tool_ms,
                  },
                  "turns": turns,
                  "tools": tools,
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
                  "query_plan_replays": plan_replays,
              }
              if case_vllm_metrics:
                  case_receipt["vllm_metrics_window"] = case_vllm_metrics
              case_receipt["resource_attribution"] = build_three_layer_attribution(
                  wall_ms=wall_ms,
                  llm_ms=llm_ms,
                  tool_ms=tool_ms,
                  process_window=process_receipt,
                  process_role_layers={
                      "agent": "middleware",
                      **({"dozerdb": "database"} if args.db_pid and spec.tools_enabled else {}),
                  },
                  database_observation={
                      "source": "bolt_server_summary",
                      "server_reported_ms": server_reported_ms,
                      "query_calls": len(successful_query_events),
                  },
                  vllm_window_scope="serialized_case" if metrics_client else "absent",
                  observability_overhead_ms=observability_overhead_ms,
              )
              case_receipt["retrieval_funnel"] = build_retrieval_funnel(
                  turns=turns,
                  tools=tools,
                  success=completed,
                  evidence_class=spec.evidence_class,
                  forced_tool=spec.forced,
                  query_validated=bool(successful_query_events),
                  query_executed=bool(successful_query_events),
                  result_rows=result_rows if successful_query_events else None,
              )
              cases.append(case_receipt)
    finally:
        if driver is not None:
            driver.close()
        if sampler:
            sampler.stop()
        if gpu_sampler:
            gpu_sampler.stop()
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.predictions_out.parent.mkdir(parents=True, exist_ok=True)
    args.predictions_out.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = {"model": args.model, "workspace_id": args.workspace_id, "database": args.database, "initial_context": "question_only", "max_turns": args.max_turns, "token_budget": args.token_budget, "row_cap": args.row_cap, "byte_cap": args.byte_cap, "enable_thinking": args.enable_thinking, "arm": args.arm, "condition": args.condition, "stage": args.stage, "run_id": args.run_id, "seed": args.seed, "repetition": args.repetition, "cache_phase": args.cache_phase, "evidence_class": spec.evidence_class, "profile_query_plans": args.profile_query_plans, "zero_row_hint": args.zero_row_hint, "private_replay_capture": args.replay_out is not None, "discovery_cache_digest": discovery_cache_digest, "tool_schema_digest": runtime_tool_schema_digest if spec.tools_enabled else None, "system_prompt_digest": _digest(system), **prompt_profile_receipt(args.prompt_profile, "cypher")}
    receipt = {
        "schema_version": "agentic_os.direct_react_text2cypher_receipt.v3",
        "execution_surface": "direct_openai_compatible_vllm_and_stock_neo4j_driver",
        "config": config,
        "cases": cases,
        "gpu_sample_validation": (
            validate_gpu_samples(args.gpu_samples_out, max_gap_s=max(2.5, args.gpu_sample_interval_s * 2.5))
            if args.gpu_samples_out
            else None
        ),
    }
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.manifest_out:
        write_manifest(
            args.manifest_out,
            {
                "schema_version": "agentic_os.run_manifest.v1",
                "run_id": args.run_id,
                "lane": "GRBench-Text2Cypher",
                "arm": args.arm,
                "config": config,
                "question_file_sha256": hashlib.sha256(args.questions.read_bytes()).hexdigest(),
            },
        )
    print(json.dumps({"cases": len(cases), "final": sum(case["success"] for case in cases)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
