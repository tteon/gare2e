from __future__ import annotations
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from runtime.experiment_platform import create_app
from seocho.eval.experiment_platform import Registry, import_characterization
from seocho.eval.experiment_evidence import query_record


def source(root: Path) -> Path:
    root.mkdir()
    task = {
        "task_id": "t1",
        "case_id": "q1",
        "trace_id": "1" * 32,
        "started_unix_s": 1,
        "wall_ms": 20,
    }
    (root / "characterization.json").write_text(
        json.dumps({"passes": [{"pass": "raw", "examples": [{"task": task}]}]})
    )
    return root


def query() -> dict:
    return {
        "schema_version": "gare2e.query_attempt.v1",
        "query_id": "q1",
        "trace_id": "1" * 32,
        "scope": "diagnostic_replay",
        "outcome": "ok",
        "mode": "raw",
        "db_queries_executed": 1,
        "started_unix_ns": 1000000001,
        "started_monotonic_ns": 100,
        "ended_monotonic_ns": 200,
        "latency_ms": 0.0001,
        "query": "SECRET CYPHER",
        "receipt": {"rows": 0, "result_bytes": 2, "password": "SECRET"},
        "plan": {
            "operators": [
                {
                    "operator": "Filter",
                    "operator_id": "0",
                    "actual_rows": 0,
                    "db_hits": None,
                    "details": "SECRET",
                }
            ],
            "total_db_hits": 0,
        },
    }


def test_import_query_evidence_and_api_logs(tmp_path: Path) -> None:
    src = source(tmp_path / "source")
    (src / "queries.jsonl").write_text(json.dumps(query()) + "\n")
    (src / "quality.jsonl").write_text(
        json.dumps(
            {
                "trace_id": "1" * 32,
                "evaluator_revision": "gold-v1",
                "answer_correctness": 1,
            }
        )
        + "\n"
    )
    root = tmp_path / "registry"
    run = import_characterization(
        src, root, run_id="run", workspace_id="team", owner="alice", metadata={}
    )
    cid = run["conditions"][0]["id"]
    task = Registry(root, "team").tasks("run", cid)[0]
    assert task["queries"][0]["plan"]["total_db_hits"] is None
    assert "SECRET" not in json.dumps(task)
    assert task["quality_evaluation"]["scores"]["answer_correctness"] == 1
    assert any(
        e["lane"] == "PROFILE replay" and e["evidence"]["query_id"] == "q1"
        for e in task["events"]
    )
    assert any(i["status"] == "missing" for i in task["evidence_insights"])
    with TestClient(create_app(root, "team", token="test")) as client:
        url = f"/api/runs/run/conditions/{cid}/tasks/{task['id']}"
        assert client.get(url + "/logs").status_code == 401
        headers = {"Authorization": "Bearer test"}
        logs = client.get(url + "/logs", headers=headers)
        assert json.loads(logs.text)["query_id"] == "q1"
        assert json.loads(logs.text)["workspace_id"] == "team"
        otlp = client.get(url + "/otlp-logs", headers=headers).json()
        attrs = otlp["resourceLogs"][0]["resource"]["attributes"]
        assert {"key": "workspace_id", "value": {"stringValue": "team"}} in attrs
        record = otlp["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert record["timeUnixNano"] == "1000000001" and record["traceId"] == "1" * 32
        assert "spanId" not in record
        metrics = client.get("/metrics", headers=headers).text
        assert "gare2e_registry_imported_query_attempts 1" in metrics
        assert "q1" not in metrics


@pytest.mark.parametrize(
    "change", [{"trace_id": "2" * 32}, {"scope": "unknown"}, {"query_id": None}]
)
def test_invalid_evidence_is_not_published(tmp_path: Path, change: dict) -> None:
    src = source(tmp_path / "source")
    (src / "queries.jsonl").write_text(json.dumps({**query(), **change}) + "\n")
    with pytest.raises(ValueError):
        import_characterization(
            src,
            tmp_path / "registry",
            run_id="r",
            workspace_id="w",
            owner="a",
            metadata={},
        )
    assert not (tmp_path / "registry/r").exists()


def test_duplicate_evidence_and_unversioned_quality_rejected(tmp_path: Path) -> None:
    src = source(tmp_path / "source")
    (src / "queries.jsonl").write_text((json.dumps(query()) + "\n") * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        import_characterization(
            src,
            tmp_path / "registry",
            run_id="r",
            workspace_id="w",
            owner="a",
            metadata={},
        )


def test_missing_counters_stay_unknown() -> None:
    record = query_record(query())
    assert record["receipt"]["rows"] == 0
    assert record["plan"]["total_db_hits"] is None
    assert record["plan"]["counter_coverage"]["db_hits"]["known"] == 0


@pytest.mark.parametrize("score", [-0.1, 1.1, True, "invalid"])
def test_invalid_quality_score_rejected(tmp_path: Path, score: object) -> None:
    src = source(tmp_path / "source")
    (src / "quality.jsonl").write_text(
        json.dumps(
            {
                "trace_id": "1" * 32,
                "evaluator_revision": "gold-v1",
                "answer_correctness": score,
            }
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="Quality scores"):
        import_characterization(
            src,
            tmp_path / "registry",
            run_id="r",
            workspace_id="w",
            owner="a",
            metadata={},
        )


def test_reuse_cannot_cross_snapshot(tmp_path: Path) -> None:
    src = source(tmp_path / "source")
    original = {
        **query(),
        "session_id": "s",
        "snapshot_id": "a",
        "query_digest": "a" * 64,
        "parameter_digest": "b" * 64,
    }
    reused = {
        **original,
        "query_id": "q2",
        "reused_from": "q1",
        "outcome": "reused",
        "mode": "exact_result_reuse",
        "db_queries_executed": 0,
        "snapshot_id": "b",
    }
    (src / "queries.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in [original, reused])
    )
    with pytest.raises(ValueError, match="Reuse needs"):
        import_characterization(
            src,
            tmp_path / "registry",
            run_id="r",
            workspace_id="w",
            owner="a",
            metadata={},
        )
