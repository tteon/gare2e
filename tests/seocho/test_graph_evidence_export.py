"""Receipt adapter preserves recorded identities without duplicating PROFILE evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmarks.export_graph_query_evidence import export
from seocho.eval.experiment_platform import Registry, import_characterization


def test_receipt_export_import_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    cell = root / "run-raw"
    cell.mkdir(parents=True)
    trace_id = "1" * 32
    plan = {
        "operators": [
            {
                "operator": "NodeIndexSeek",
                "estimated_rows": 2,
                "actual_rows": 1,
                "db_hits": 2,
                "page_cache_hits": None,
                "page_cache_misses": None,
            }
        ]
    }
    case = {
        "qid": "q1",
        "run_id": "run-raw",
        "trace": {"trace_id": trace_id},
        "started_unix_s": 1700000000,
        "time": {"wall_ms": 100},
        "terminal_status": "final",
        "turns": [
            {
                "started_elapsed_ms": 0,
                "latency_ms": 80,
                "input_tokens": 20,
                "output_tokens": 10,
            }
        ],
        "query_plan_replays": [{"plan": plan, "wall_ms": 5}],
    }
    (cell / "run-receipt.json").write_text(json.dumps({"cases": [case]}))
    captured = {
        "schema_version": "gare2e.query_attempt.v1",
        "trace_id": trace_id,
        "query_id": "actual-replay",
        "scope": "diagnostic_replay",
        "mode": "raw",
        "outcome": "ok",
        "db_queries_executed": 1,
        "plan": plan,
        "query": "private-cypher",
    }
    (cell / "worker.queries.jsonl").write_text(json.dumps(captured) + "\n")
    output = tmp_path / "export"
    manifest = export(root, output)
    assert manifest["queries"] == 1
    assert len(manifest["source_sha256"]) == 2
    assert "private-cypher" not in (output / "queries.jsonl").read_text()
    registry = tmp_path / "registry"
    run = import_characterization(
        output, registry, run_id="run", workspace_id="w", owner="test", metadata={}
    )
    task = Registry(registry, "w").tasks("run", run["conditions"][0]["id"])[0]
    assert task["queries"][0]["query_id"] == "actual-replay"
    assert task["queries"][0]["plan"]["total_page_cache_misses"] is None
    assert task["queries"][0]["plan"]["operators"][0]["cardinality_error"] > 0


def test_historical_profile_submission_count_stays_unknown(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "run-receipt.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "qid": "q",
                        "run_id": "old",
                        "wall_ms": 10,
                        "query_plan_replays": [
                            {"wall_ms": 2, "plan": {"operators": []}}
                        ],
                    }
                ]
            }
        )
    )
    output = tmp_path / "export"
    export(root, output)
    record = json.loads((output / "queries.jsonl").read_text())
    assert record["db_queries_executed"] is None
    assert record["started_unix_ns"] is None
    task = json.loads((output / "characterization.json").read_text())["passes"][0][
        "examples"
    ][0]["task"]
    assert task["clock_source"] == "unknown_relative_only"


@pytest.mark.parametrize("duplicates", [1, 2])
def test_server_join_requires_one_exact_parent(tmp_path: Path, duplicates: int) -> None:
    root = tmp_path / "source"
    root.mkdir()
    case = {
        "qid": "q",
        "run_id": "r",
        "trace": {"trace_id": "a" * 32},
        "started_unix_s": 1700000000,
        "wall_ms": 10,
        "turns": [{"span_id": "b" * 16, "latency_ms": 8, "started_elapsed_ms": 1}],
    }
    (root / "run-receipt.json").write_text(json.dumps({"cases": [case]}))
    server = {
        "trace_id": "a" * 32,
        "parent_span_id": "b" * 16,
        "span_id": "c" * 16,
        "name": "llm_request",
        "service_name": "vllm",
        "start_unix_ns": 1700000000002000000,
        "end_unix_ns": 1700000000007000000,
        "attributes": {},
    }
    server_path = tmp_path / "collector.jsonl"
    server_path.write_text((json.dumps(server) + "\n") * duplicates)
    out = tmp_path / "export"
    manifest = export(root, out, server_path)
    assert "server_spans" in manifest["source_sha256"]
    cell = json.loads((out / "characterization.json").read_text())["passes"][0]
    request = cell["examples"][0]["requests"][0]
    assert cell["matched_requests"] == (1 if duplicates == 1 else 0)
    assert ("server_started_unix_s" in request) == (duplicates == 1)
