#!/usr/bin/env python3
"""Plain-response Text2Cypher on GRBench with optional grammar-constrained decoding (lab 020, guided decoding).

Same execution surface as ``run_direct_react_text2cypher.py`` (one agent, an
OpenAI-compatible vLLM endpoint, the stock Neo4j driver through the single
:class:`GRBenchDozerExecutor` boundary, content-free receipts) but the protocol
is *query-first*: the schema context of the arm (B0 / S1 / O1, from the frozen
discovery cache) is in the system prompt, and each query turn asks the model
for exactly one read-only Cypher statement as its whole response.  With
``--constrained xgrammar`` that response is decoded under
:data:`~seocho.eval.cypher_grammar.CYPHER_READ_GRAMMAR` (``structured_outputs.grammar``),
so label / scope / write-clause rejections cannot occur; with
``--constrained none`` the same protocol runs unconstrained (the control that
isolates the grammar's effect from the protocol change).  After the first
non-empty result, or when the query turns are used up, one unconstrained turn
produces the final answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from neo4j import GraphDatabase

from seocho.eval.agent_execution import build_retrieval_funnel
from seocho.eval.agentic_graph_agents import classify_answer_status
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
from seocho.eval.cypher_grammar import GRAMMARS, GRAMMAR_DIGESTS
from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError
from seocho.eval.prompt_profiles import PROMPT_PROFILES, prompt_profile_receipt
from seocho.eval.raw_agentic_baseline import RAW_CONDITIONS, raw_condition_spec

LANE = "GRBench-GrammarCypher"
CONSTRAINED_MODES = ("xgrammar", "xgrammar-ontology", "none")
_FENCE = re.compile(r"^```(?:cypher|sql)?\s*|\s*```$", re.I | re.M)


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


def _load_discovery_context(path: Path | None, arm: str) -> tuple[dict[str, str], str | None]:
    """Per-question schema context of ``arm`` from the frozen discovery cache (the text the schema tool would return)."""
    if path is None:
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "seocho.agentic_os.discovery_cache.v1":
        raise ValueError("unsupported discovery cache")
    responses: dict[str, str] = {}
    for case in payload.get("cases", []):
        qid = str(case.get("qid", ""))
        by_arm = case.get("responses", {})
        if not qid or arm not in by_arm:
            raise ValueError(f"discovery cache lacks {arm} response for {qid or '<empty>'}")
        responses[qid] = json.dumps(by_arm[arm], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return responses, str(payload.get("cache_digest") or canonical_digest(payload))


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


def _usage_receipt(response: dict[str, Any], latency_ms: float, *, output_kind: str, constrained: bool) -> dict[str, Any]:
    usage = response.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "latency_ms": round(latency_ms, 3),
        "outcome": "ok",
        "output_kind": output_kind,
        "constrained": constrained,
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "thinking_tokens": completion_details.get("reasoning_tokens", usage.get("reasoning_tokens")),
        "tool_call_tokens": None,
        "cached_tokens": details.get("cached_tokens"),
    }


def clean_cypher(text: str) -> str:
    """Strip code fences and surrounding prose-free whitespace from a plain Cypher response."""
    return _FENCE.sub("", text.strip()).strip()


def system_prompt(context: str) -> str:
    return (
        "You answer factual questions about papers, authors, venues, citations and collaborations in the "
        "active benchmark graph by writing read-only Cypher, never from memory. Graph context (labels, "
        "properties, relationship names, scope rules): " + context + " Every node pattern is "
        "(x:GRBenchNode {_workspace_id: $workspace_id, ...}); relationships are [:GRBENCH_REL {relation_name: '...'}]. "
        "Anchor every pattern on an indexed property (id, source_node_id, feature_name or feature_title via =, IN, "
        "CONTAINS or STARTS WITH). When asked for a query, reply with exactly one Cypher statement and nothing else. "
        "When asked for the answer, answer only from the returned rows or say the evidence is insufficient."
    )


QUERY_INSTRUCTION = "Write one read-only Cypher query that retrieves the evidence for the question. Reply with the Cypher statement only."
REPAIR_INSTRUCTION = "The previous query returned the result above. If it does not answer the question, write one different read-only Cypher query; reply with the Cypher statement only."
ANSWER_INSTRUCTION = "Using only the rows returned above, answer the question concisely. If the rows do not contain the answer, say that the evidence is insufficient."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--predictions-out", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--discovery-cache", type=Path, help="frozen discovery cache; the arm's response is placed in the system prompt")
    parser.add_argument("--arm", choices=("B0", "S1", "O1"), default="S1")
    parser.add_argument("--constrained", choices=CONSTRAINED_MODES, default="xgrammar", help="xgrammar = read-only Cypher grammar; xgrammar-ontology = the same grammar with property / relation / node_type names closed over the GRBench ontology; none = same protocol unconstrained")
    parser.add_argument("--run-id", default="grammar-smoke")
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
    parser.add_argument("--bolt-url", default="bolt://127.0.0.1:7687")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--qid", action="append", default=[])
    parser.add_argument("--max-turns", type=int, default=6, help="total LLM turns (query turns + the answer turn)")
    parser.add_argument("--max-query-turns", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--token-budget", type=int, default=16_384)
    parser.add_argument("--row-cap", type=int, default=20)
    parser.add_argument("--byte-cap", type=int, default=12_000)
    parser.add_argument("--zero-row-hint", action="store_true")
    parser.add_argument("--enable-thinking", choices=("true", "false"), default="false")
    parser.add_argument("--condition", choices=RAW_CONDITIONS, default="tool_auto", help="accepted for parity; the lane is query-first")
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="baseline", help="accepted for parity")
    parser.add_argument("--profile-query-plans", action="store_true", help="accepted for parity; not implemented in this lane")
    parser.add_argument("--stage", choices=("smoke", "main"), default="smoke")
    args = parser.parse_args()
    if args.token_budget <= 0 or args.max_output_tokens < 1 or args.max_turns < 2 or args.max_query_turns < 1:
        raise ValueError("budgets must be positive and max-turns must leave room for the answer turn")
    spec = raw_condition_spec(args.condition, stage=args.stage)
    if args.stage == "main" and args.discovery_cache is None:
        raise ValueError("main requires a frozen discovery cache")
    require_fresh_outputs((args.receipt_out, args.predictions_out, args.manifest_out, args.host_samples_out, args.gpu_samples_out))
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set")
    questions = _load_questions(args.questions, args.limit, set(args.qid) or None)
    if args.qid and len(questions) != len(set(args.qid)):
        raise KeyError("one or more requested GRBench QIDs were not found")
    context_by_qid, discovery_cache_digest = _load_discovery_context(args.discovery_cache, args.arm)
    constrained = args.constrained != "none"
    grammar = GRAMMARS.get(args.constrained)
    driver = GraphDatabase.driver(args.bolt_url, auth=("neo4j", password))
    executor = GRBenchDozerExecutor(
        driver, database=args.database, workspace_id=args.workspace_id, row_cap=args.row_cap, byte_cap=args.byte_cap,
        require_evidence_columns=False, require_anchor=os.getenv("SEOCHO_GRBENCH_REQUIRE_ANCHOR", "1") == "1", zero_row_hint=args.zero_row_hint,
    )
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
    system_digests: set[str] = set()
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
                context = context_by_qid.get(item["qid"])
                if context is None:
                    if args.discovery_cache:
                        raise ValueError("qid missing from frozen discovery cache")
                    context = "{}"
                system = system_prompt(context)
                system_digests.add(_digest(system))
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": item["question"] + "\n\n" + QUERY_INSTRUCTION},
                ]
                turns: list[dict[str, Any]] = []
                tool_events: list[dict[str, Any]] = []
                terminal = "turn_budget_exhausted"
                final_answer = ""
                budget_prompt_tokens = 0
                budget_completion_tokens = 0
                query_turns = 0
                have_rows = False
                for turn_index in range(args.max_turns):
                    answer_turn = have_rows or query_turns >= args.max_query_turns or turn_index == args.max_turns - 1
                    if answer_turn:
                        messages.append({"role": "user", "content": ANSWER_INSTRUCTION})
                    payload: dict[str, Any] = {
                        "model": args.model,
                        "messages": messages,
                        "temperature": 0,
                        "max_tokens": args.max_output_tokens,
                        "chat_template_kwargs": {"enable_thinking": args.enable_thinking == "true"},
                    }
                    if constrained and not answer_turn:
                        payload["structured_outputs"] = {"grammar": grammar}
                    try:
                        response, llm_ms = _post(f"{args.base_url.rstrip('/')}/v1/chat/completions", payload)
                    except Exception as exc:  # class only, never provider/body text
                        turns.append({"latency_ms": None, "outcome": type(exc).__name__, "output_kind": "error", "constrained": constrained and not answer_turn})
                        terminal = "llm_error"
                        break
                    turn_receipt = _usage_receipt(response, llm_ms, output_kind="message" if answer_turn else "cypher", constrained=constrained and not answer_turn)
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
                    content = str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    messages.append({"role": "assistant", "content": content})
                    if answer_turn:
                        final_answer = content
                        terminal = "final"
                        break
                    query_turns += 1
                    query = clean_cypher(content)
                    tool_started = time.perf_counter()
                    try:
                        observation, detail = executor.execute_rows(query)
                        outcome = "ok"
                        have_rows = int(detail.get("rows") or 0) > 0
                    except Exception as exc:
                        error_code = exc.code if isinstance(exc, QueryPolicyError) else "database_or_runtime_error"
                        observation = json.dumps({"error_code": error_code})
                        detail = {"validator_ms": None, "db_execute_ms": None, "rows": 0, "result_bytes": len(observation), "query_digest": _digest(query) if query else None, "error_code": error_code, "error_class": type(exc).__name__}
                        outcome = "rejected"
                    detail.update({"name": "execute_cypher", "outcome": outcome, "latency_ms": round((time.perf_counter() - tool_started) * 1000, 3), "tool_argument_decode_ms": 0.0})
                    detail.setdefault("started_elapsed_ms", round((tool_started - case_clock_started) * 1000, 3))
                    tool_events.append(detail)
                    messages.append({"role": "user", "content": "Result: " + observation + ("" if have_rows else "\n\n" + REPAIR_INSTRUCTION)})
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
                case_receipt = {
                    **correlation,
                    "trace": case_trace_ctx.receipt(),
                    "qid": item["qid"],
                    "terminal": terminal,
                    "answer_status": answer_status,
                    "success": completed,
                    "turn_count": len(turns),
                    "query_turns": query_turns,
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
                            "constrained_query_turns": sum(1 for t in turns if t.get("constrained")),
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
                    forced_tool=True, query_validated=bool(ok_events), query_executed=bool(ok_events),
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
        "model": args.model, "workspace_id": args.workspace_id, "database": args.database, "initial_context": "schema_context_in_system_prompt",
        "protocol": "query_first_plain_response", "constrained": args.constrained, "grammar_digest": GRAMMAR_DIGESTS.get(args.constrained),
        "max_turns": args.max_turns, "max_query_turns": args.max_query_turns, "token_budget": args.token_budget, "row_cap": args.row_cap, "byte_cap": args.byte_cap,
        "zero_row_hint": args.zero_row_hint, "enable_thinking": args.enable_thinking, "arm": args.arm, "condition": args.condition, "stage": args.stage,
        "run_id": args.run_id, "seed": args.seed, "repetition": args.repetition, "cache_phase": args.cache_phase, "evidence_class": spec.evidence_class,
        "discovery_cache_digest": discovery_cache_digest, "system_prompt_digests": sorted(system_digests), **prompt_profile_receipt(args.prompt_profile, "cypher"),
    }
    receipt = {
        "schema_version": "agentic_os.grbench_grammar_cypher_receipt.v1",
        "execution_surface": "direct_openai_compatible_vllm_structured_outputs_and_stock_neo4j_driver",
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
