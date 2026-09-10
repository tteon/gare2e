#!/usr/bin/env python3
"""Run GTSQA manager→SPARQL-specialist-as-tool with Agents SDK.

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

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.policy import require_runtime_permission

from seocho.eval.agent_execution import build_retrieval_funnel
from seocho.eval.agentic_os_trace import case_trace
from seocho.eval.prompt_profiles import PROMPT_PROFILES, instructions_prefix, system_prompt_addendum
from seocho.eval.agentic_graph_agents import (
    EvidenceHandoff,
    gtsqa_react_tools,
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
from seocho.eval.gtsqa import GTSQACase, load_gtsqa_cases, score_gtsqa_retrieval
from seocho.eval.oxigraph_runtime import GTSQAOxigraphExecutor, SeochoOxigraphExecutor


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _schema(case: GTSQACase) -> dict:
    relations = {edge.relation_id: edge.relation_label for edge in case.graph_edges}
    return {
        "graph_iri": f"urn:seocho:gtsqa:case:{case.case_id}",
        "triple_variables_required": ["s", "p", "o"],
        "relations": [{"id": key, "label": relations[key]} for key in sorted(relations)[:48]],
        "truncated": len(relations) > 48,
    }


def _identifier(value: str) -> str:
    token = value.strip().strip("<>")
    return token.rsplit(":", 1)[-1]


def _triples(rows) -> list[dict[str, str]]:
    triples: list[dict[str, str]] = []
    for raw in rows:
        row = raw.model_dump() if hasattr(raw, "model_dump") else raw
        source, relation, target = row.get("s"), row.get("p"), row.get("o")
        if source and relation and target:
            triples.append({"source": _identifier(source), "relation": _identifier(relation), "target": _identifier(target)})
    return triples


async def _run(args: argparse.Namespace) -> int:
    root: Path = args.run_root
    require_fresh_outputs(
        (
            root / "receipts.jsonl",
            root / "predictions.private.jsonl",
            root / "evidence.private.jsonl",
            root / "manifest.json",
            root / "metrics" / "agent-oxigraph-process.csv",
            args.gpu_samples_out,
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    llm_timer = LLMRequestTimer()
    model = make_vllm_chat_model(
        base_url=f"{args.base_url.rstrip('/')}/v1", api_key="EMPTY", model=args.model, request_timer=llm_timer
    )
    cases = load_gtsqa_cases(
        args.parquet,
        limit=args.limit,
        case_ids=args.case_id or None,
    )
    evidence_class = "main_comparable" if args.stage == "main" else "behavioral_baseline"
    discovery_payload = None
    discovery: dict[str, dict] = {}
    if args.discovery_cache:
        discovery_payload = json.loads(args.discovery_cache.read_text(encoding="utf-8"))
        if args.stage == "main":
            validate_discovery_cache(
                discovery_payload,
                token_counter=VLLMTokenizer(args.base_url, args.model),
                qids={str(case.case_id) for case in cases},
            )
            if str(discovery_payload["contract"].get("tool_schema_digest", "")) != canonical_digest(
                gtsqa_react_tools()[0]["function"]
            ):
                raise ValueError("discovery cache tool-schema digest does not match the runtime schema")
        source_arm = "S1" if args.arm == "S4" else "B0"
        discovery = {
            str(item["qid"]): dict(item["responses"][source_arm])
            for item in discovery_payload.get("cases", ())
        }
    metrics = VllmMetricsClient(args.metrics_url) if args.metrics_url else None
    sampler = HostProcessSampler(
        root / "metrics" / "agent-oxigraph-process.csv",
        roles={"agent_oxigraph": os.getpid()},
    )
    sampler.start()
    gpu_sampler = (
        NvidiaSmiSampler(args.gpu_samples_out, interval_s=args.gpu_sample_interval_s)
        if args.gpu_samples_out
        else None
    )
    if gpu_sampler:
        gpu_sampler.start()
    try:
      for case in cases:
        with case_trace(args.run_id, str(case.case_id)) as case_trace_ctx:
          if args.discovery_cache and str(case.case_id) not in discovery:
              raise RuntimeError(f"frozen discovery cache lacks case {case.case_id}")
          executor = (
              SeochoOxigraphExecutor(case, policy_check=require_runtime_permission)
              if args.arm == "S4"
              else GTSQAOxigraphExecutor(case)
          )
          query_receipts: list[dict] = []

          def execute(query: str) -> EvidenceHandoff:
              result = executor.execute_select(query)
              query_receipts.append(dict(result.receipt))
              return EvidenceHandoff.from_rows(rows=result.rows, query=query)
          correlation = Correlation(
              args.run_id,
              str(case.case_id),
              args.arm,
              args.repetition,
              args.cache_phase,
              "manager_specialist_as_tool",
              args.seed,
          ).receipt_fields()
          window = VllmMetricsWindow(metrics) if metrics else None
          process_window = ProcessMetricsWindow({"agent_oxigraph": os.getpid()})
          case_started = time.perf_counter()
          llm_timer.reset()
          process_window.__enter__()
          if window:
              window.__enter__()
          try:
              episode = await run_agents_sdk_agent_tool_episode(
                  question=case.question,
                  model=model,
                  execute_query=execute,
                  schema_discovery=lambda case=case: discovery.get(str(case.case_id), _schema(case)),
                  max_turns=args.max_turns,
                  enable_thinking=args.enable_thinking == "true",
                  max_output_tokens=args.max_output_tokens,
                  evidence_class=evidence_class,
                  query_language="SPARQL",
                  specialist_instructions_addendum=system_prompt_addendum(args.prompt_profile, "sparql"),
                  shared_prefix=instructions_prefix(args.prompt_profile, "sparql"),
              )
              triples = _triples(episode.evidence.evidence)
              retrieval = score_gtsqa_retrieval(triples, case)
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
                  "trace": case_trace_ctx.receipt(),
                  "success": bool(episode.receipt.get("success")) and not budget_exceeded,
                  "terminal_status": terminal_status,
                  "retrieval": retrieval.to_dict(),
                  "retrieved_triple_count": len(triples),
                  "evidence_class": evidence_class,
                  "query_executions": query_receipts,
                  "budget": {
                      "limit": args.token_budget,
                      "observed_total": usage_total,
                      "enforcement": "post_response_fail_closed",
                  },
              }
              receipt["retrieval_funnel"].update(
                  {
                      "gold_evidence_retrieved": retrieval.recall > 0,
                      "effective_retrieval": retrieval.recall > 0,
                  }
              )
              append_private_jsonl(
                  root / "predictions.private.jsonl",
                  {**correlation, "answer": episode.answer},
              )
              append_private_jsonl(
                  root / "evidence.private.jsonl",
                  {**correlation, "triples": triples},
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
                  "retrieval": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
                  "retrieved_triple_count": 0,
                  "evidence_class": evidence_class,
                  "query_executions": query_receipts,
                  "retrieval_funnel": build_retrieval_funnel(
                      turns=[],
                      tools=query_tools,
                      success=False,
                      evidence_class=evidence_class,
                      query_validated=bool(query_receipts),
                      query_executed=bool(query_receipts),
                      result_rows=0 if query_receipts else None,
                      gold_evidence_retrieved=False if query_receipts else None,
                  ),
              }
          finally:
              if window:
                  window.__exit__(None, None, None)
                  receipt["vllm_metrics_window"] = window.receipt()
              process_window.__exit__(None, None, None)
              window_delta = receipt.get("vllm_metrics_window", {}).get("metrics_delta") or {}
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
              database_wall_ms = sum(
                  float(item.get("store_submit_ms") or 0)
                  + float(item.get("iterate_ms") or 0)
                  for item in query_receipts
              )
              database_cpu_ms = sum(
                  float(item.get("store_submit_cpu_ms") or 0)
                  + float(item.get("iterate_cpu_ms") or 0)
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
                  process_role_layers={
                      "agent_oxigraph": "shared_middleware_database_process"
                  },
                  database_observation={
                      "source": "embedded_oxigraph_stage_timers",
                      "observed_wall_ms": round(database_wall_ms, 3),
                      "observed_thread_cpu_ms": round(database_cpu_ms, 3),
                      "query_calls": len(query_receipts),
                  },
                  vllm_window_scope="serialized_case" if metrics else "absent",
                  observability_overhead_ms=observability_overhead_ms,
              )
          append_jsonl(root / "receipts.jsonl", receipt)
    finally:
        sampler.stop()
        if gpu_sampler:
            gpu_sampler.stop()
    write_manifest(
        root / "manifest.json",
        {
            "schema_version": "agentic_os.run_manifest.v1",
            "run_id": args.run_id,
            "lane": "GTSQA-Text2SPARQL-AgentsSDK-AgentTool",
            "dataset_sha256": _sha256(args.parquet),
            "config": {
                "model": args.model,
                "arm": args.arm,
                "topology": "manager_specialist_as_tool",
                "repetition": args.repetition,
                "seed": args.seed,
                "cache_phase": args.cache_phase,
                "max_turns": args.max_turns,
                "case_count": len(cases),
                "enable_thinking": args.enable_thinking,
                "token_budget": args.token_budget,
                "stage": args.stage,
                "evidence_class": evidence_class,
                "discovery_cache_digest": discovery_payload["cache_digest"] if discovery_payload else None,
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
    print(json.dumps({"run_id": args.run_id, "cases": len(cases)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", choices=("B0", "S4"), required=True)
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--stage", choices=("smoke", "main"), default="main")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--case-id", action="append", type=int, default=[])
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="baseline")
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--enable-thinking", choices=("true", "false"), required=True)
    parser.add_argument("--token-budget", type=int, default=8_192)
    parser.add_argument("--metrics-url")
    parser.add_argument("--gpu-samples-out", type=Path)
    parser.add_argument("--gpu-sample-interval-s", type=float, default=1.0)
    args = parser.parse_args()
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be positive")
    if args.stage == "main" and args.discovery_cache is None:
        raise ValueError("main stage requires --discovery-cache")
    if args.arm == "S4" and args.discovery_cache is None:
        raise ValueError("S4 requires --discovery-cache")
    if args.arm == "S4" and args.token_budget <= 0:
        raise ValueError("S4 requires a positive --token-budget")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
