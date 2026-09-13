#!/usr/bin/env python3
"""Run question-only GTSQA Text2SPARQL ReAct with correlated artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.policy import require_runtime_permission
from seocho.budget import BudgetExceededError, TokenBudgetTracker
from seocho.eval.agentic_graph_agents import (
    NativeModelReply,
    NativeToolModel,
    OpenAICompatibleNativeToolModel,
    PrivateTrace,
    gtsqa_react_tools,
    run_gtsqa_closed_book,
    run_gtsqa_text2sparql_react,
)
from seocho.eval.private_trace_store import store_from_env
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
from seocho.eval.gtsqa import load_gtsqa_cases
from seocho.eval.oxigraph_runtime import GTSQAOxigraphExecutor, SeochoOxigraphExecutor
from seocho.eval.prompt_profiles import PROMPT_PROFILES, prompt_profile_receipt, system_prompt_addendum
from seocho.eval.raw_agentic_baseline import RAW_CONDITIONS, raw_condition_spec


class _BudgetedModel:
    """Charge provider-reported usage without changing the model request."""

    def __init__(self, delegate: NativeToolModel, tracker: TokenBudgetTracker) -> None:
        self.delegate = delegate
        self.tracker = tracker

    def complete(
        self,
        *,
        messages: list[Mapping[str, Any]],
        tools: list[Mapping[str, Any]],
    ) -> NativeModelReply:
        self.tracker.check()
        reply = self.delegate.complete(messages=messages, tools=tools)
        try:
            self.tracker.charge(
                prompt=int(reply.usage.get("prompt_tokens") or reply.usage.get("input_tokens") or 0),
                completion=int(reply.usage.get("completion_tokens") or reply.usage.get("output_tokens") or 0),
            )
        except BudgetExceededError:
            # Account for the completed provider request, but do not expose its
            # text or tool calls to the episode once the shared ceiling is crossed.
            return NativeModelReply(
                text="",
                usage=reply.usage,
                latency_s=reply.latency_s,
                protocol_error="token_budget_exceeded",
            )
        return reply


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_discovery(
    path: Path | None,
    *,
    arm: str,
    base_url: str,
    model: str,
    validate_serving_tokens: bool = True,
    validate_tool_schema: bool = True,
    qids: set[str] | None = None,
) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    if path is None:
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if validate_tool_schema:
        expected = canonical_digest(gtsqa_react_tools()[0]["function"])
        observed = str((payload.get("contract") or {}).get("tool_schema_digest", ""))
        if observed != expected:
            raise ValueError("discovery cache tool-schema digest does not match the runtime schema")
    if validate_serving_tokens:
        validate_discovery_cache(
            payload,
            token_counter=VLLMTokenizer(base_url, model),
            qids=qids,
        )
    source_arm = "S1" if arm in {"S1", "S4"} else ("C-" if arm == "C-" else "B0")
    responses = {
        str(case["qid"]): dict(case["responses"][source_arm])
        for case in payload.get("cases", ())
    }
    return responses, str(payload["cache_digest"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", choices=("B0", "C-", "S1", "S2a", "S2b", "S3", "S4"), default="B0")
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--condition", choices=RAW_CONDITIONS, default="tool_auto")
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="baseline")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--case-id", action="append", type=int, default=[])
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--enable-thinking", choices=("true", "false"), default="true")
    parser.add_argument(
        "--capture-token-ids",
        action="store_true",
        help="Opt in to provider prompt/response token IDs for private trace capture.",
    )
    parser.add_argument(
        "--capture-wire-bytes",
        action="store_true",
        help="Opt in to exact private HTTP request/response JSON capture.",
    )
    parser.add_argument("--metrics-url")
    parser.add_argument("--gpu-samples-out", type=Path)
    parser.add_argument("--gpu-sample-interval-s", type=float, default=1.0)
    parser.add_argument("--token-budget", type=int, default=0)
    parser.add_argument(
        "--stage",
        choices=("smoke", "main"),
        default="smoke",
        help="smoke verifies E2E only; main additionally enforces serving-token cache equivalence",
    )
    parser.add_argument(
        "--require-retrieval",
        action="store_true",
        help="Plumbing smoke only: require a successful execute_sparql call before finalization",
    )
    args = parser.parse_args()
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be positive")
    condition = "forced_plumbing" if args.require_retrieval else args.condition
    spec = raw_condition_spec(condition, stage=args.stage)
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    if args.token_budget <= 0:
        raise ValueError("--token-budget must be positive")
    if args.stage == "smoke" and args.arm != "B0":
        raise ValueError("--stage smoke is restricted to the non-comparative B0 arm")
    if args.arm != "B0" and condition != "tool_auto":
        raise ValueError("closed_book and forced_plumbing are SEOCHO-free B0 conditions")
    if args.stage == "main" and spec.tools_enabled and args.discovery_cache is None:
        raise ValueError("main tool_auto requires a frozen discovery cache")
    evidence_class = spec.evidence_class

    receipts_path = args.run_root / "receipts.jsonl"
    predictions_path = args.run_root / "predictions.private.jsonl"
    evidence_path = args.run_root / "evidence.private.jsonl"
    private_events_path = args.run_root / "events.private.jsonl"
    samples_path = args.run_root / "metrics" / "oxigraph-process.csv"
    require_fresh_outputs(
        (
            receipts_path,
            predictions_path,
            evidence_path,
            private_events_path,
            samples_path,
            args.gpu_samples_out,
            args.run_root / "manifest.json",
        )
    )
    args.run_root.mkdir(parents=True, exist_ok=True)
    sampler = HostProcessSampler(samples_path, roles={"agent_oxigraph": os.getpid()})
    sampler.start()
    gpu_sampler = (
        NvidiaSmiSampler(args.gpu_samples_out, interval_s=args.gpu_sample_interval_s)
        if args.gpu_samples_out
        else None
    )
    if gpu_sampler:
        gpu_sampler.start()
    private_store = store_from_env()
    dataset_sha256 = _sha256(args.parquet)
    metrics = VllmMetricsClient(args.metrics_url) if args.metrics_url else None
    cases = load_gtsqa_cases(
        args.parquet,
        limit=args.limit,
        case_ids=args.case_id or None,
    )
    discovery, discovery_cache_digest = _load_discovery(
        args.discovery_cache,
        arm=args.arm,
        base_url=args.base_url,
        model=args.model,
        validate_serving_tokens=args.stage == "main",
        validate_tool_schema=args.stage == "main" and spec.tools_enabled,
        qids={str(case.case_id) for case in cases},
    )
    try:
        for case in cases:
            if args.discovery_cache is not None and spec.tools_enabled and str(case.case_id) not in discovery:
                raise RuntimeError(f"frozen discovery cache lacks case {case.case_id}")
            correlation = Correlation(
                args.run_id,
                str(case.case_id),
                args.arm,
                args.repetition,
                args.cache_phase,
                "single_agent",
                args.seed,
            ).receipt_fields()
            private_trace = private_store.trace(correlation, case=case, dataset_sha256=dataset_sha256, model=args.model, condition=condition) if private_store else PrivateTrace(correlation)
            direct_model: NativeToolModel = OpenAICompatibleNativeToolModel(
                base_url=args.base_url,
                model=args.model,
                enable_thinking=args.enable_thinking == "true",
                max_tokens=args.max_output_tokens,
                private_trace=private_trace,
                capture_token_ids=args.capture_token_ids or bool(private_store and private_store.config.get("capture_token_ids")),
                capture_wire_bytes=args.capture_wire_bytes or private_store is not None,
            )
            tracker = TokenBudgetTracker(
                budget=args.token_budget,
                scope=f"{args.run_id}:{case.case_id}",
            )
            model: NativeToolModel = _BudgetedModel(direct_model, tracker)

            schema_discovery = None
            if str(case.case_id) in discovery:
                def schema_discovery(_case, *, value=discovery[str(case.case_id)]):
                    return value

            def before_tool(name, active_case):
                # S4 query policy is enforced inside the SEOCHO Oxigraph
                # adapter; the discovery tool still crosses this gate here.
                if args.arm == "S2b" or (args.arm == "S4" and name == "inspect_graph_schema"):
                    require_runtime_permission(
                        action="run_agent",
                        workspace_id=active_case.workspace_id,
                    )

            if args.arm == "S3":
                executor_factory = lambda active_case: SeochoOxigraphExecutor(active_case)
            elif args.arm == "S4":
                executor_factory = lambda active_case: SeochoOxigraphExecutor(
                    active_case,
                    policy_check=require_runtime_permission,
                )
            else:
                executor_factory = GTSQAOxigraphExecutor
            window = VllmMetricsWindow(metrics) if metrics else None
            process_window = ProcessMetricsWindow({"agent_oxigraph": os.getpid()})
            observed_wall_started = time.perf_counter()
            process_window.__enter__()
            if window:
                window.__enter__()
            result = None
            try:
                if condition == "closed_book":
                    result = run_gtsqa_closed_book(
                        case,
                        model=model,
                        evidence_class=evidence_class,
                    )
                else:
                    result = run_gtsqa_text2sparql_react(
                        case,
                        model=model,
                        max_turns=args.max_turns,
                        executor_factory=executor_factory,
                        schema_discovery=schema_discovery,
                        before_tool=before_tool if args.arm in {"S2b", "S4"} else None,
                        require_retrieval=spec.forced,
                        evidence_class=evidence_class,
                        system_addendum=system_prompt_addendum(args.prompt_profile, "sparql"),
                        private_trace=private_trace,
                    )
            finally:
                if window:
                    window.__exit__(None, None, None)
                process_window.__exit__(None, None, None)
                if result is not None:
                    private_trace.record_final_answer(result.answer)
                for event in private_trace.records():
                    append_private_jsonl(private_events_path, event)
                if private_store:
                    private_store.finish(private_trace)
            observed_wall_ms = (time.perf_counter() - observed_wall_started) * 1000
            receipt = {**correlation, **result.receipt}
            receipt["condition"] = condition
            receipt["evidence_class"] = evidence_class
            receipt["budget"] = {
                **tracker.snapshot(),
                "limit": args.token_budget,
                "exceeded": tracker.total > args.token_budget,
                "enforcement": "per_response_fail_closed",
                "comparison_control": True,
            }
            if window:
                receipt["vllm_metrics_window"] = window.receipt()
            observability_overhead_ms = float(
                receipt.get("vllm_metrics_window", {}).get("observer_overhead_ms") or 0
            )
            receipt.setdefault("time", {})["wall_ms"] = round(observed_wall_ms, 3)
            receipt.setdefault("time", {})["observability_overhead_ms"] = round(
                observability_overhead_ms, 3
            )
            receipt["time"]["adjusted_wall_ms"] = round(
                max(0.0, float(receipt["time"].get("wall_ms") or 0) - observability_overhead_ms),
                3,
            )
            query_events = [
                event
                for event in receipt.get("tools", {}).get("events", [])
                if event.get("name") == "execute_sparql" and event.get("outcome") == "ok"
            ]
            database_wall_ms = round(
                sum(
                    float(event.get("details", {}).get("store_submit_ms") or 0)
                    + float(event.get("details", {}).get("iterate_ms") or 0)
                    for event in query_events
                ),
                3,
            )
            database_cpu_ms = round(
                sum(
                    float(event.get("details", {}).get("store_submit_cpu_ms") or 0)
                    + float(event.get("details", {}).get("iterate_cpu_ms") or 0)
                    for event in query_events
                ),
                3,
            )
            receipt["resource_attribution"] = build_three_layer_attribution(
                wall_ms=float(receipt.get("time", {}).get("wall_ms") or 0),
                llm_ms=float(receipt.get("time", {}).get("llm_ms") or 0),
                tool_ms=float(receipt.get("time", {}).get("tool_ms") or 0),
                process_window=process_window.receipt(),
                process_role_layers={"agent_oxigraph": "shared_middleware_database_process"},
                database_observation={
                    "source": "embedded_oxigraph_stage_timers",
                    "observed_wall_ms": database_wall_ms,
                    "observed_thread_cpu_ms": database_cpu_ms,
                    "query_calls": len(query_events),
                },
                vllm_window_scope="serialized_case" if metrics else "absent",
                observability_overhead_ms=observability_overhead_ms,
            )
            append_jsonl(receipts_path, receipt)
            append_private_jsonl(
                predictions_path,
                {**correlation, "answer": result.answer},
            )
            append_private_jsonl(
                evidence_path,
                {**correlation, "triples": result.retrieved_edges},
            )
    finally:
        sampler.stop()
        if gpu_sampler:
            gpu_sampler.stop()

    config = {
        "model": args.model,
        "arm": args.arm,
        "condition": condition,
        "max_turns": args.max_turns,
        "repetition": args.repetition,
        "seed": args.seed,
        "cache_phase": args.cache_phase,
        "enable_thinking": args.enable_thinking,
        "case_count": len(cases),
        "discovery_cache_digest": discovery_cache_digest,
        "token_budget": args.token_budget,
        **prompt_profile_receipt(args.prompt_profile, "sparql"),
        "tool_schema_digest": (
            canonical_digest({"tools": list(gtsqa_react_tools())})
            if spec.tools_enabled
            else None
        ),
        "stage": args.stage,
        "serving_token_cache_applicable": spec.tools_enabled,
        "serving_token_cache_validated": args.stage == "main" and spec.tools_enabled,
        "retrieval_required": spec.forced,
        "evidence_class": evidence_class,
    }
    write_manifest(
        args.run_root / "manifest.json",
        {
            "schema_version": "agentic_os.run_manifest.v1",
            "run_id": args.run_id,
            "lane": "GTSQA-Text2SPARQL",
            "config": config,
            "dataset_sha256": _sha256(args.parquet),
            "telemetry": {
                "process_samples": str(samples_path),
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


if __name__ == "__main__":
    raise SystemExit(main())
