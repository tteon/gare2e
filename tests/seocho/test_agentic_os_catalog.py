from __future__ import annotations

import csv
import json

import pytest

from seocho.eval.agentic_os_catalog import build_catalog, write_catalog_outputs


def _write_run(
    root,
    *,
    run_id: str,
    arm: str,
    cases: list[dict],
    lane: str = "GTSQA-Text2SPARQL",
    condition: str = "tool_auto",
) -> None:
    root.mkdir(parents=True)
    manifest = {
        "schema_version": "agentic_os.run_manifest.v1",
        "run_id": run_id,
        "lane": lane,
        "config": {"model": "Qwen/Qwen3.6-27B", "arm": arm, "condition": condition, "enable_thinking": "true", "cache_phase": "warm"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with (root / "receipts.jsonl").open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps({"run_id": run_id, "arm": arm, "condition": condition, "cache_phase": "warm", "role": "single_agent", "repetition": 0, **case}) + "\n")


def _case(case_id: str, *, success: bool, recall: float, wall_ms: float, failures: int = 0) -> dict:
    return {
        "case_id": case_id,
        "success": success,
        "terminal_status": "final" if success else "turn_budget_exhausted",
        "turn_count": 2 if success else 4,
        "repair_failures": failures,
        "time": {"wall_ms": wall_ms, "llm_ms": wall_ms - 2, "tool_ms": 2},
        "tools": {"calls": 2, "failures": failures, "events": []},
        "accumulated_context_tokens": 100,
        "generated_tokens": {"total": 20},
        "cache": {"provider_reported_cached_tokens": 50, "provider_cache_hit_ratio": 0.5},
        "retrieved_triple_count": 2,
        "retrieval": {"precision": recall, "recall": recall, "f1": recall},
        "evidence_class": "main_comparable",
        "retrieval_funnel": {
            "graph_tool_selected": True,
            "time_to_first_graph_tool_ms": 5,
            "llm_turns_before_first_graph_tool": 1,
            "schema_discovery_calls": 1,
            "graph_query_calls": 1,
            "query_validated": True,
            "query_executed": True,
            "result_rows": 2 if recall else 0,
            "nonempty_result": bool(recall),
            "gold_evidence_retrieved": bool(recall),
            "effective_retrieval": bool(recall),
        },
    }


def test_catalog_preserves_failures_and_computes_paired_qid_bootstrap(tmp_path) -> None:
    _write_run(
        tmp_path / "b0",
        run_id="b0-run",
        arm="B0",
        cases=[_case("q1", success=True, recall=0.5, wall_ms=20), _case("q2", success=False, recall=0.0, wall_ms=40, failures=1)],
    )
    _write_run(
        tmp_path / "s1",
        run_id="s1-run",
        arm="S1",
        cases=[_case("q1", success=True, recall=1.0, wall_ms=15), _case("q2", success=True, recall=0.5, wall_ms=30)],
    )
    catalog, attempts, failures = build_catalog([tmp_path], bootstrap_samples=200, bootstrap_seed=7, p99_min_n=100)
    assert catalog["attempts_total"] == 4
    assert len(attempts) == 4
    b0 = next(group for group in catalog["groups"] if group["arm"] == "B0")
    assert b0["attempts_total"] == 2
    assert b0["execution_failures"] == 1
    assert b0["metrics"]["execution_success"]["mean"] == 0.5
    assert b0["metrics"]["wall_ms"]["p50"] == 30.0
    assert b0["metrics"]["wall_ms"]["p99"] is None
    assert b0["evidence_class"] == "main_comparable"
    assert b0["metrics"]["retrieval_triggered"]["mean"] == 1.0
    assert b0["metrics"]["effective_retrieval"]["mean"] == 0.5
    comparison = catalog["paired_arm_comparisons"][0]
    assert comparison["shared_qids"] == 2
    assert comparison["metrics"]["retrieval_recall"]["mean_delta_treatment_minus_baseline"] == 0.5
    assert comparison["metrics"]["wall_ms"]["mean_delta_treatment_minus_baseline"] == -7.5
    assert len(failures) == 1
    assert "turn_budget_exhausted" in failures[0]["failure_kinds"]
    assert "tool_failure" in failures[0]["failure_kinds"]
    assert "repair_or_retry" in failures[0]["failure_kinds"]


def test_catalog_accepts_exact_blinded_judge_extension_and_writes_outputs(tmp_path) -> None:
    run = tmp_path / "run"
    _write_run(run, run_id="r1", arm="B0", cases=[_case("q1", success=True, recall=1.0, wall_ms=10)])
    scores = tmp_path / "judge.jsonl"
    scores.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "case_id": "q1",
                "arm": "B0",
                "repetition": 0,
                "cache_phase": "warm",
                "role": "single_agent",
                "answer_score": 1,
                "rationale_score": 0.5,
                "ar_score": 0.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    catalog, attempts, failures = build_catalog([run], judge_scores_path=scores, bootstrap_samples=10)
    assert catalog["judge_join"] == {
        "schema_version": "agentic_os.blinded_judge_join.v1",
        "rows": 1,
        "matched": 1,
        "coverage": 1.0,
    }
    assert attempts[0]["answer_score"] == 1.0
    output = tmp_path / "analysis"
    write_catalog_outputs(output, catalog, attempts, failures)
    assert {path.name for path in output.iterdir()} == {
        "ANALYSIS.md",
        "attempts.csv",
        "catalog.json",
        "failures.csv",
        "groups.csv",
        "paired_comparisons.csv",
        "paired_condition_comparisons.csv",
    }
    with (output / "attempts.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["answer_score"] == "1.0"
    assert "separate blinded-judge extension" in (output / "ANALYSIS.md").read_text(encoding="utf-8")


def test_catalog_flags_impossible_adjusted_wall_attribution(tmp_path) -> None:
    case = _case("q1", success=True, recall=1.0, wall_ms=10)
    case["time"] = {
        "wall_ms": 10,
        "adjusted_wall_ms": 4,
        "observability_overhead_ms": 6,
        "llm_ms": 8,
        "tool_ms": 1,
    }
    _write_run(tmp_path / "run", run_id="r1", arm="B0", cases=[case])

    _, attempts, failures = build_catalog([tmp_path], bootstrap_samples=10)

    assert "invalid_latency_attribution" in attempts[0]["failure_kinds"]
    assert "invalid_latency_attribution" in failures[0]["failure_kinds"]


def test_catalog_privacy_gate_checks_only_telemetry(tmp_path) -> None:
    run = tmp_path / "run"
    _write_run(run, run_id="r1", arm="B0", cases=[_case("q1", success=True, recall=1.0, wall_ms=10)])
    (run / "predictions.private.jsonl").write_text("PRIVATE_CANARY\n", encoding="utf-8")
    catalog, _, _ = build_catalog([run], privacy_sentinels=["PRIVATE_CANARY"], bootstrap_samples=10)
    assert catalog["privacy_validation"]["passed"] is True
    with (run / "receipts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('"PRIVATE_CANARY"\n')
    with pytest.raises(Exception, match="content sentinel"):
        build_catalog([run], privacy_sentinels=["PRIVATE_CANARY"], bootstrap_samples=10)


def test_catalog_normalizes_direct_case_receipt_and_infers_multi_agent_lane(tmp_path) -> None:
    direct = tmp_path / "direct"
    direct.mkdir()
    (direct / "manifest.json").write_text(
        json.dumps({"schema_version": "agentic_os.run_manifest.v1", "run_id": "direct", "lane": "GRBench-Text2Cypher", "arm": "B0", "config": {"model": "qwen", "enable_thinking": False}}),
        encoding="utf-8",
    )
    (direct / "run-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "agentic_os.direct_react_text2cypher_receipt.v3",
                "cases": [
                    {
                        "run_id": "direct",
                        "case_id": "7",
                        "arm": "B0",
                        "repetition": 0,
                        "cache_phase": "cold",
                        "role": "single_agent",
                        "success": True,
                        "terminal": "final",
                        "turn_count": 2,
                        "time": {"wall_ms": 12, "llm_ms": 10, "tool_ms": 2},
                        "tools": [
                            {
                                "name": "execute_cypher",
                                "outcome": "ok",
                                "rows": 0,
                                "result_bytes": 2,
                                "server_available_ms": 1,
                                "server_consumed_ms": 2,
                                "tool_argument_decode_ms": 0.1,
                                "validator_ms": 0.2,
                                "pool_acquire_ms": 0.3,
                                "bolt_submit_ms": 0.4,
                                "hydrate_ms": 0.5,
                                "serialize_ms": 0.6,
                            },
                            {
                                "name": "execute_cypher",
                                "outcome": "ok",
                                "rows": 3,
                                "result_bytes": 30,
                                "server_available_ms": 1,
                                "server_consumed_ms": 2,
                                "tool_argument_decode_ms": 0.1,
                                "validator_ms": 0.2,
                                "pool_acquire_ms": 0.3,
                                "bolt_submit_ms": 0.4,
                                "first_record_ms": 0.7,
                                "hydrate_ms": 0.5,
                                "serialize_ms": 0.6,
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog, attempts, _ = build_catalog([direct], bootstrap_samples=10)
    assert catalog["attempts_total"] == 1
    assert attempts[0]["topology"] == "single_agent"
    assert attempts[0]["reasoning_mode"] == "non_reasoning"
    assert attempts[0]["result_rows"] == 3.0
    assert attempts[0]["db_server_consumed_ms"] == 4.0
    assert attempts[0]["query_retry_count"] == 1.0
    assert attempts[0]["empty_result_retry_count"] == 1.0
    assert attempts[0]["tool_argument_decode_ms"] == pytest.approx(0.2)
    assert attempts[0]["query_validation_ms"] == pytest.approx(0.4)
    assert attempts[0]["db_client_acquire_ms"] == pytest.approx(0.6)
    assert attempts[0]["db_submit_ms"] == pytest.approx(0.8)
    assert attempts[0]["db_first_result_ms"] == pytest.approx(0.7)
    assert attempts[0]["db_iterate_hydrate_ms"] == pytest.approx(1.0)
    assert attempts[0]["evidence_serialize_ms"] == pytest.approx(1.2)


def test_catalog_reads_nested_oxigraph_tool_stage_receipts(tmp_path) -> None:
    run = tmp_path / "gts"
    _write_run(
        run,
        run_id="gts-run",
        arm="B0",
        cases=[
            {
                **_case("q1", success=True, recall=1.0, wall_ms=10),
                "tools": {
                    "calls": 1,
                    "failures": 0,
                    "events": [
                        {
                            "name": "execute_sparql",
                            "outcome": "ok",
                            "details": {
                                "rows": 2,
                                "result_bytes": 42,
                                "load_ms": 1.5,
                                "validation_ms": 0.2,
                                "store_submit_ms": 0.4,
                                "first_result_ms": 0.6,
                                "iterate_ms": 0.8,
                                "serialize_ms": 1.0,
                            },
                        }
                    ],
                },
            }
        ],
    )

    _, attempts, _ = build_catalog([run], bootstrap_samples=10)

    assert attempts[0]["query_validation_ms"] == pytest.approx(0.2)
    assert attempts[0]["db_submit_ms"] == pytest.approx(0.4)
    assert attempts[0]["db_first_result_ms"] == pytest.approx(0.6)
    assert attempts[0]["db_iterate_hydrate_ms"] == pytest.approx(0.8)
    assert attempts[0]["evidence_serialize_ms"] == pytest.approx(1.0)
    assert attempts[0]["db_graph_load_ms"] == pytest.approx(1.5)


def test_catalog_prefers_case_receipt_but_preserves_orphaned_schedule_failure(tmp_path) -> None:
    run = tmp_path / "scheduled"
    _write_run(
        run,
        run_id="scheduled-run",
        arm="B0",
        cases=[_case("q1", success=True, recall=1.0, wall_ms=10)],
    )
    lifecycle = [
        {
            "schema_version": "agentic_os.schedule_attempt_lifecycle.v1",
            "run_id": "scheduled-run",
            "case_id": "q1",
            "arm": "B0",
            "seed": 0,
            "repetition": 0,
            "cache_phase": "warm",
            "role": "schedule_coordinator",
            "terminal_status": "ok",
        },
        {
            "schema_version": "agentic_os.schedule_attempt_lifecycle.v1",
            "run_id": "missing-child-run",
            "case_id": "q2",
            "arm": "B0",
            "seed": 0,
            "repetition": 0,
            "cache_phase": "warm",
            "role": "schedule_coordinator",
            "terminal_status": "runner_failed",
            "runner_exit_code": 1,
        },
    ]
    (run / "coordinator-receipts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in lifecycle),
        encoding="utf-8",
    )

    catalog, attempts, _ = build_catalog([run], bootstrap_samples=10)

    assert catalog["attempts_total"] == 2
    q1 = next(attempt for attempt in attempts if attempt["case_id"] == "q1")
    q2 = next(attempt for attempt in attempts if attempt["case_id"] == "q2")
    assert q1["role"] == "single_agent"
    assert q2["role"] == "schedule_coordinator"
    assert q2["execution_success"] == 0.0


def test_catalog_normalizes_agents_sdk_specialist_as_tool(tmp_path) -> None:
    run = tmp_path / "agent-tool"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "agentic_os.run_manifest.v1",
                "run_id": "agent-tool",
                "lane": "GTSQA-Text2SPARQL-AgentsSDK-AgentTool",
                "config": {
                    "model": "qwen",
                    "enable_thinking": True,
                    "topology": "manager_specialist_as_tool",
                },
            }
        ),
        encoding="utf-8",
    )
    (run / "receipts.jsonl").write_text(
        json.dumps(
            {
                "run_id": "agent-tool",
                "case_id": "7",
                "arm": "B0",
                "role": "manager_specialist_as_tool",
                "repetition": 0,
                "cache_phase": "warm",
                "success": True,
                "terminal_status": "final",
                "turn_count": 4,
                "agent_tool_count": 1,
                "agent_tool_failures": 0,
                "role_characteristics": {
                    "manager": {"repair_attempts": 0},
                    "query_specialist": {"repair_attempts": 2},
                },
                "manager_usage": {
                    "input_tokens": 30,
                    "output_tokens": 5,
                    "cached_tokens": 10,
                    "reasoning_tokens": 2,
                    "requests": 2,
                },
                "specialist_usage": {
                    "input_tokens": 40,
                    "output_tokens": 8,
                    "cached_tokens": 20,
                    "reasoning_tokens": 3,
                    "requests": 2,
                },
                "retrieval_funnel": {
                    "graph_tool_selected": True,
                    "schema_discovery_calls": 1,
                    "graph_query_calls": 1,
                    "query_validated": True,
                    "query_executed": True,
                    "result_rows": 1,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, attempts, _ = build_catalog([run], bootstrap_samples=10)

    assert attempts[0]["lane"] == "GTSQA-Text2SPARQL"
    assert attempts[0]["topology"] == "manager_specialist_as_tool"
    assert attempts[0]["agent_tool_count"] == 1.0
    assert attempts[0]["input_tokens"] == 70.0
    assert attempts[0]["output_tokens"] == 13.0
    assert attempts[0]["cached_tokens"] == 30.0
    assert attempts[0]["reasoning_tokens"] == 5.0
    assert attempts[0]["message_tokens"] == 8.0
    assert attempts[0]["manager_requests"] == 2.0
    assert attempts[0]["manager_input_tokens"] == 30.0
    assert attempts[0]["manager_output_tokens"] == 5.0
    assert attempts[0]["manager_reasoning_tokens"] == 2.0
    assert attempts[0]["specialist_requests"] == 2.0
    assert attempts[0]["specialist_input_tokens"] == 40.0
    assert attempts[0]["specialist_output_tokens"] == 8.0
    assert attempts[0]["specialist_reasoning_tokens"] == 3.0
    assert attempts[0]["manager_repair_count"] == 0.0
    assert attempts[0]["specialist_repair_count"] == 2.0


def test_catalog_pairs_tool_auto_against_closed_book_without_plumbing(tmp_path) -> None:
    _write_run(
        tmp_path / "closed",
        run_id="closed-run",
        arm="B0",
        condition="closed_book",
        cases=[_case("q1", success=True, recall=0.0, wall_ms=10)],
    )
    _write_run(
        tmp_path / "tool",
        run_id="tool-run",
        arm="B0",
        condition="tool_auto",
        cases=[_case("q1", success=True, recall=1.0, wall_ms=20)],
    )
    catalog, attempts, _ = build_catalog([tmp_path], bootstrap_samples=10)
    assert {attempt["condition"] for attempt in attempts} == {"closed_book", "tool_auto"}
    comparison = catalog["paired_condition_comparisons"][0]
    assert comparison["baseline_condition"] == "closed_book"
    assert comparison["treatment_condition"] == "tool_auto"
    assert comparison["metrics"]["wall_ms"]["mean_delta_treatment_minus_baseline"] == 10.0
