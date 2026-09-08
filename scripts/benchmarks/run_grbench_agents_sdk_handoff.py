#!/usr/bin/env python3
"""Run GRBench manager→Text2Cypher-specialist-as-tool with Agents SDK.

The filename is retained as a compatibility entry point; the measured topology
keeps control with the manager and is not an SDK handoff.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

from neo4j import GraphDatabase

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from seocho.eval.agent_execution import build_retrieval_funnel
from seocho.eval.prompt_profiles import PROMPT_PROFILES, instructions_prefix, system_prompt_addendum
from seocho.eval.agentic_graph_agents import (
    EvidenceHandoff,
    grbench_react_tools,
    LLMRequestTimer,
    make_vllm_chat_model,
    redacted_episode_error,
    run_agents_sdk_agent_tool_episode,
)
from seocho.eval.agentic_os_artifacts import (
    Correlation,
    append_jsonl,
    append_private_jsonl,
    canonical_digest,
    require_fresh_outputs,
    write_manifest,
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
from seocho.eval.discovery_cache import VLLMTokenizer, validate_discovery_cache
from seocho.eval.grbench import load_grbench_questions
from seocho.eval.grbench_runtime import GRBenchDozerExecutor


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _neutral_schema() -> dict:
    return {
        "nodes": [
            {
                "label": "GRBenchNode",
                "properties": ["id", "node_type", "source_node_id", "feature_*"],
            }
        ],
        "relationships": [
            {
                "type": "GRBENCH_REL",
                "properties": ["relation_name", "source_node_type"],
            }
        ],
        "query_contract": {
            "scope": "Every GRBenchNode pattern carries {_workspace_id: $workspace_id}.",
            "evidence_columns": ["s", "p", "o"],
        },
    }


def _load_discovery(path: Path | None, *, arm: str) -> tuple[dict[str, dict], dict | None]:
    if path is None:
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "seocho.agentic_os.discovery_cache.v1":
        raise ValueError("unsupported discovery cache")
    responses: dict[str, dict] = {}
    for item in payload.get("cases", ()):
        qid = str(item.get("qid", ""))
        response = (item.get("responses") or {}).get(arm)
        if not qid or not isinstance(response, dict):
            raise ValueError(f"discovery cache lacks {arm} response for {qid or '<empty>'}")
        responses[qid] = dict(response)
    return responses, payload


async def _run(args: argparse.Namespace) -> int:
    root: Path = args.run_root
    require_fresh_outputs(
        (
            root / "receipts.jsonl",
            root / "predictions.private.jsonl",
            root / "evidence.private.jsonl",
            root / "manifest.json",
            root / "metrics" / "agent-dozer-process.csv",
            args.gpu_samples_out,
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    evidence_class = "main_comparable" if args.stage == "main" else "behavioral_baseline"
    discovery, discovery_payload = _load_discovery(args.discovery_cache, arm=args.arm)
    if args.stage == "main":
        if discovery_payload is None:
            raise ValueError("main stage requires --discovery-cache")
        validate_discovery_cache(
            discovery_payload,
            token_counter=VLLMTokenizer(args.base_url, args.model),
            qids=args.qid or None,
        )
        if str(discovery_payload["contract"].get("tool_schema_digest", "")) != canonical_digest(
            grbench_react_tools()[0]["function"]
        ):
            raise ValueError("discovery cache tool-schema digest does not match the runtime schema")
    if args.arm in {"C-", "S1"} and discovery_payload is None:
        raise ValueError(f"{args.arm} requires --discovery-cache")

    questions = load_grbench_questions(args.questions, limit=None if args.qid else args.limit)
    if args.qid:
        selected = set(args.qid)
        questions = [question for question in questions if question.qid in selected]
        if {question.qid for question in questions} != selected:
            raise KeyError("one or more requested GRBench QIDs were not found within --limit")
    llm_timer = LLMRequestTimer()
    model = make_vllm_chat_model(
        base_url=f"{args.base_url.rstrip('/')}/v1",
        api_key="EMPTY",
        model=args.model,
        request_timer=llm_timer,
    )
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set")
    driver = GraphDatabase.driver(args.bolt_uri, auth=(args.bolt_user, password))
    metrics = VllmMetricsClient(args.metrics_url) if args.metrics_url else None
    roles = {"agent": os.getpid()}
    if args.db_pid:
        roles["dozerdb"] = args.db_pid
    sampler = HostProcessSampler(root / "metrics" / "agent-dozer-process.csv", roles=roles)
    sampler.start()
    gpu_sampler = (
        NvidiaSmiSampler(args.gpu_samples_out, interval_s=args.gpu_sample_interval_s)
        if args.gpu_samples_out
        else None
    )
    if gpu_sampler:
        gpu_sampler.start()
    try:
        for question in questions:
            executor = GRBenchDozerExecutor(
                driver,
                database=args.database,
                workspace_id=args.workspace_id,
                row_cap=args.row_cap,
                byte_cap=args.byte_cap,
            )
            query_receipts: list[dict] = []
            profile_queries: list[str] = []

            def execute(query: str) -> EvidenceHandoff:
                result = executor.execute_select(query)
                query_receipts.append(dict(result.receipt))
                profile_queries.append(query)
                return EvidenceHandoff.from_rows(rows=result.rows, query=query)

            correlation = Correlation(
                args.run_id,
                question.qid,
                args.arm,
                args.repetition,
                args.cache_phase,
                "manager_specialist_as_tool",
                args.seed,
            ).receipt_fields()
            window = VllmMetricsWindow(metrics) if metrics else None
            process_window = ProcessMetricsWindow(roles)
            case_started = time.perf_counter()
            llm_timer.reset()
            process_window.__enter__()
            if window:
                window.__enter__()
            try:
                episode = await run_agents_sdk_agent_tool_episode(
                    question=question.question,
                    model=model,
                    execute_query=execute,
                    schema_discovery=lambda qid=question.qid: discovery.get(qid, _neutral_schema()),
                    max_turns=args.max_turns,
                    enable_thinking=args.enable_thinking == "true",
                    max_output_tokens=args.max_output_tokens,
                    evidence_class=evidence_class,
                    query_language="Cypher",
                    specialist_instructions_addendum=system_prompt_addendum(args.prompt_profile, "cypher"),
                    shared_prefix=instructions_prefix(args.prompt_profile, "cypher"),
                )
                usage_total = sum(
                    int(episode.receipt.get(stage, {}).get(field, 0))
                    for stage in ("manager_usage", "specialist_usage")
                    for field in ("input_tokens", "output_tokens")
                )
                budget_exceeded = usage_total > args.token_budget
                answer_status = str(episode.receipt.get("answer_status") or "empty")
                terminal_status = (
                    "budget_exceeded"
                    if budget_exceeded
                    else {
                        "abstained": "abstained",
                        "empty": "empty_answer",
                        "unsupported_answer": "unsupported_answer",
                    }.get(answer_status, "final")
                )
                receipt = {
                    **correlation,
                    **episode.receipt,
                    "success": bool(episode.receipt.get("success")) and not budget_exceeded,
                    "terminal_status": terminal_status,
                    "evidence_class": evidence_class,
                    "retrieved_triple_count": len(episode.evidence.evidence),
                    "query_executions": query_receipts,
                    "budget": {
                        "limit": args.token_budget,
                        "observed_total": usage_total,
                        "enforcement": "post_response_fail_closed",
                    },
                }
                append_private_jsonl(
                    root / "predictions.private.jsonl",
                    {
                        **correlation,
                        "answer": episode.answer,
                        "gold_answer": question.answer,
                    },
                )
                append_private_jsonl(
                    root / "evidence.private.jsonl",
                    {
                        **correlation,
                        "triples": [item.model_dump() for item in episode.evidence.evidence],
                    },
                )
            except Exception as exc:
                append_private_jsonl(
                    root / "errors.private.jsonl",
                    {
                        **correlation,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
                query_tools = [
                    {"name": "execute_graph_query", "outcome": "ok"}
                    for _receipt in query_receipts
                ]
                receipt = {
                    **correlation,
                    "schema_version": "agentic_os.agents_sdk_agent_tool_episode.v1",
                    "success": False,
                    "terminal_status": "episode_error",
                    **redacted_episode_error(exc, stage="manager_specialist_as_tool"),
                    "evidence_class": evidence_class,
                    "retrieved_triple_count": 0,
                    "query_executions": query_receipts,
                    "retrieval_funnel": build_retrieval_funnel(
                        turns=[],
                        tools=query_tools,
                        success=False,
                        evidence_class=evidence_class,
                        query_validated=bool(query_receipts),
                        query_executed=bool(query_receipts),
                        result_rows=0 if query_receipts else None,
                    ),
                }
            finally:
                if window:
                    window.__exit__(None, None, None)
                    receipt["vllm_metrics_window"] = window.receipt()
                process_window.__exit__(None, None, None)
                window_delta = (
                    receipt.get("vllm_metrics_window", {}).get("metrics_delta") or {}
                )
                observability_overhead_ms = float(
                    receipt.get("vllm_metrics_window", {}).get("observer_overhead_ms") or 0
                )
                vllm_e2e_ms = float(
                    window_delta.get("vllm:e2e_request_latency_seconds_sum") or 0
                ) * 1000
                # Client-observed HTTP latency is the exclusive critical-path term (same
                # boundary as the single-agent runner); the vLLM window, when present,
                # is kept as a server-side reference.  Without the timer the appliance
                # mode reported llm_ms=0 and pushed the whole wall into the residual.
                llm_http = llm_timer.receipt()
                llm_observed_ms = llm_http["llm_ms"] if llm_http["request_count"] else vllm_e2e_ms
                audited_tool_ms = sum(
                    float(event.get("latency_ms") or 0)
                    for event in receipt.get("events", [])
                    if event.get("event") == "tool"
                )
                query_tool_ms = audited_tool_ms or sum(
                    float(item.get("wall_ms") or 0) for item in query_receipts
                )
                server_reported_ms = sum(
                    float(item.get("server_available_ms") or 0)
                    + float(item.get("server_consumed_ms") or 0)
                    for item in query_receipts
                )
                receipt["time"] = {
                    "wall_ms": round((time.perf_counter() - case_started) * 1000, 3),
                    "llm_ms": round(llm_observed_ms, 3),
                    "tool_ms": round(query_tool_ms, 3),
                }
                receipt["time"]["llm_time_source"] = (
                    "client_http_request_timer" if llm_http["request_count"] else "vllm_metrics_window"
                )
                receipt["time"]["llm_requests"] = llm_http["request_count"]
                receipt["time"]["vllm_e2e_ms"] = round(vllm_e2e_ms, 3)
                receipt["llm_requests"] = llm_http["requests"]
                receipt["time"]["observability_overhead_ms"] = round(
                    observability_overhead_ms, 3
                )
                receipt["time"]["adjusted_wall_ms"] = round(
                    max(0.0, receipt["time"]["wall_ms"] - observability_overhead_ms),
                    3,
                )
                receipt["resource_attribution"] = build_three_layer_attribution(
                    wall_ms=receipt["time"]["wall_ms"],
                    llm_ms=receipt["time"]["llm_ms"],
                    tool_ms=receipt["time"]["tool_ms"],
                    process_window=process_window.receipt(),
                    process_role_layers={"agent": "middleware", "dozerdb": "database"},
                    database_observation={
                        "source": "bolt_server_summary",
                        "server_reported_ms": round(server_reported_ms, 3),
                        "query_calls": len(query_receipts),
                    },
                    vllm_window_scope="serialized_case" if metrics else "absent",
                    observability_overhead_ms=observability_overhead_ms,
                )
            if args.profile_query_plans:
                plan_replays: list[dict] = []
                for query in profile_queries:
                    try:
                        plan_replays.append(executor.profile_select(query))
                    except Exception as exc:
                        plan_replays.append(
                            {
                                "schema_version": "agentic_os.dozer_profile_replay.v1",
                                "scope": "separate_read_only_diagnostic_replay",
                                "error_class": type(exc).__name__,
                            }
                        )
                receipt["query_plan_replays"] = plan_replays
            append_jsonl(root / "receipts.jsonl", receipt)
    finally:
        sampler.stop()
        if gpu_sampler:
            gpu_sampler.stop()
        driver.close()

    write_manifest(
        root / "manifest.json",
        {
            "schema_version": "agentic_os.run_manifest.v1",
            "run_id": args.run_id,
            "lane": "GRBench-Text2Cypher-AgentsSDK-AgentTool",
            "question_file_sha256": _sha256(args.questions),
            "config": {
                "model": args.model,
                "arm": args.arm,
                "topology": "manager_specialist_as_tool",
                "stage": args.stage,
                "evidence_class": evidence_class,
                "initial_context": "question_only",
                "workspace_id": args.workspace_id,
                "database": args.database,
                "repetition": args.repetition,
                "seed": args.seed,
                "cache_phase": args.cache_phase,
                "max_turns": args.max_turns,
                "case_count": len(questions),
                "enable_thinking": args.enable_thinking,
                "token_budget": args.token_budget,
                "profile_query_plans": args.profile_query_plans,
                "discovery_cache_digest": discovery_payload.get("cache_digest") if discovery_payload else None,
            },
            "telemetry": {
                "gpu_samples": str(args.gpu_samples_out) if args.gpu_samples_out else None,
                "gpu_sample_validation": (
                    validate_gpu_samples(
                        args.gpu_samples_out,
                        max_gap_s=max(2.5, args.gpu_sample_interval_s * 2.5),
                    )
                    if args.gpu_samples_out
                    else None
                ),
            },
        },
    )
    print(json.dumps({"run_id": args.run_id, "cases": len(questions)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", choices=("B0", "C-", "S1"), default="B0")
    parser.add_argument("--stage", choices=("smoke", "main"), default="main")
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--bolt-user", default="neo4j")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--qid", action="append", default=[])
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="baseline")
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--row-cap", type=int, default=20)
    parser.add_argument("--byte-cap", type=int, default=12_000)
    parser.add_argument("--token-budget", type=int, default=8_192)
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--enable-thinking", choices=("true", "false"), required=True)
    parser.add_argument("--metrics-url")
    parser.add_argument("--gpu-samples-out", type=Path)
    parser.add_argument("--gpu-sample-interval-s", type=float, default=1.0)
    parser.add_argument(
        "--profile-query-plans",
        action="store_true",
        help="Replay successful read-only Cypher queries under PROFILE outside E2E timing.",
    )
    parser.add_argument("--db-pid", type=int)
    args = parser.parse_args()
    if args.token_budget <= 0:
        raise ValueError("--token-budget must be positive")
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be positive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
