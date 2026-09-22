from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.experiment_platform import create_app
from seocho.eval.experiment_platform import (
    CONTRACT_FIELDS,
    Registry,
    compare,
    file_sha256,
    import_characterization,
    metric_definitions,
    perfetto_trace,
)


def fixture_export(path: Path) -> Path:
    path.mkdir()
    task = {"task_id": "case-1", "case_id": "1", "started_unix_s": 1000,
            "wall_ms": 100, "terminal": "final", "fully_contained": True, "answer": "SECRET"}
    request = {"request_started_unix_s": 1000.01, "client_latency_s": .05,
               "server_span_present": True, "server_started_unix_s": 1000.012,
               "server_finished_unix_s": 1000.055, "prefill_s": .003, "decode_s": .03,
               "prompt": "SECRET", "client_output_tokens": 20}
    tool = {"started_elapsed_ms": 65, "latency_ms": 15, "rows": 3,
            "query_shape": "neighborhood", "query": "SECRET"}
    (path / "characterization.json").write_text(json.dumps({"passes": [{"pass": "base-k8",
        "output_tok_s_gpu": 10, "application_k": 8, "requests": 1, "matched_requests": 1,
        "throughput": {"task_completions_per_s": 2}, "tools": [],
        "examples": [{"task": task, "requests": [request], "tools": [tool]}]}]}))
    return path


def register(tmp_path: Path) -> tuple[Registry, dict]:
    source = fixture_export(tmp_path / "source")
    run = import_characterization(source, tmp_path / "registry", run_id="run-1", workspace_id="team",
        owner="alice", metadata={"contract": {k: "fixed" for k in CONTRACT_FIELDS}, "admission": {"base-k8": "passed"}})
    return Registry(tmp_path / "registry", "team"), run


def test_immutable_import_and_content_free_drilldown(tmp_path: Path) -> None:
    registry, run = register(tmp_path)
    cid = run["conditions"][0]["id"]
    task = registry.tasks("run-1", cid)[0]
    assert len(task["events"]) == 4
    assert "SECRET" not in json.dumps(task)
    assert not any("prefill" in e["name"] for e in task["events"])
    same = import_characterization(tmp_path / "source", tmp_path / "registry", run_id="run-1", workspace_id="team",
        owner="alice", metadata={"contract": {k: "fixed" for k in CONTRACT_FIELDS}, "admission": {"base-k8": "passed"}})
    assert same["content_sha256"] == run["content_sha256"]
    with pytest.raises(ValueError, match="different evidence"):
        import_characterization(tmp_path / "source", tmp_path / "registry", run_id="run-1", workspace_id="team", owner="bob", metadata={})
    assert not list((tmp_path / "registry").glob(".import-*"))


def test_comparison_requires_contract_and_admission(tmp_path: Path) -> None:
    _, run = register(tmp_path)
    condition = run["conditions"][0]
    candidate = {**condition, "metrics": {**condition["metrics"], "output_tok_s_gpu": 20}}
    good = compare(run, condition, run, candidate)
    assert good["comparable"]
    assert good["metrics"][0]["percent_change"] == 100
    altered = {**run, "contract": {**run["contract"], "hardware": "other"}}
    candidate = {**candidate, "contract": {**candidate["contract"], "hardware": "other"}}
    bad = compare(run, condition, altered, candidate)
    assert not bad["comparable"] and bad["contract_differences"] == ["hardware"]
    assert all(m["percent_change"] is None for m in bad["metrics"])
    assert not compare(run, condition, run, {**candidate, "admission": "failed"})["comparable"]


def test_trace_units_and_parallel_intervals_have_separate_tracks(tmp_path: Path) -> None:
    registry, run = register(tmp_path)
    task = registry.tasks("run-1", run["conditions"][0]["id"])[0]
    task["events"].append({**task["events"][1], "start_us": 20000})
    task["events"].sort(key=lambda e: e["start_us"])
    trace = perfetto_trace(task)
    clients = [e for e in trace["traceEvents"] if e.get("cat") == "LLM client"]
    assert clients[0]["dur"] == 50000
    assert len({e["tid"] for e in clients}) == 2
    assert "SECRET" not in json.dumps(trace)


def test_api_auth_workspace_paths_and_missing_results(tmp_path: Path) -> None:
    _, run = register(tmp_path)
    app = create_app(tmp_path / "registry", "team", token="test-token")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/runs").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        assert len(client.get("/api/runs", headers=headers).json()["runs"]) == 1
        cid = run["conditions"][0]["id"]
        url = f"/api/runs/run-1/conditions/{cid}"
        tasks = client.get(url + "/tasks", headers=headers).json()["tasks"]
        assert "events" not in tasks[0]
        assert client.get(url + "/tasks/missing", headers=headers).status_code == 404
        trace = client.get(url + f"/tasks/{tasks[0]['id']}/perfetto", headers=headers)
        assert trace.status_code == 200 and trace.json()["traceEvents"]
        assert client.get("/api/runs/run-1/conditions/nope", headers=headers).status_code == 404
    assert Registry(tmp_path / "registry", "other-team").runs() == []
    with pytest.raises(ValueError):
        Registry(tmp_path / "registry", "team").run("../source")


def test_metric_extensions_are_numeric_and_cannot_replace_primary_metric() -> None:
    extension = {"id": "db_p95", "label": "DB p95", "unit": "ms", "view": "Tool",
                 "direction": "lower", "path": "custom.db_p95"}
    assert metric_definitions([extension])[-1] == extension
    with pytest.raises(ValueError):
        metric_definitions([{**extension, "id": "output_tok_s_gpu"}])


def test_reviewed_nsight_sqlite_download_and_checksum_gate(tmp_path: Path) -> None:
    source = fixture_export(tmp_path / "source")
    # Schema-only fixture validates attachment handling, not Nsight compatibility/performance.
    database = source / "profile.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (start INTEGER, end INTEGER)")
    artifact = {"id": "gpu-profile", "condition": "base-k8", "type": "nsight_sqlite", "path": "profile.sqlite",
                "sha256": file_sha256(database), "content_reviewed": True, "profiler_version": "test-fixture",
                "scope": "separate_diagnostic", "capture_window": {"start_s": 0, "end_s": 1, "clock_domain": "profiler_relative"}}
    registry = tmp_path / "registry"
    run = import_characterization(source, registry, run_id="sql-run", workspace_id="team", owner="alice", metadata={}, diagnostics=[artifact])
    cid = run["conditions"][0]["id"]
    with TestClient(create_app(registry, "team", token="test")) as client:
        path = f"/api/runs/sql-run/conditions/{cid}/diagnostics/gpu-profile"
        assert client.get(path).status_code == 401
        response = client.get(path, headers={"Authorization": "Bearer test"})
        assert response.content == database.read_bytes()
        assert response.headers["x-artifact-sha256"] == artifact["sha256"]
        assert '.sqlite' in response.headers['content-disposition']
    for changed in ({"sha256": "wrong"}, {"path": "../outside.sqlite"}, {"content_reviewed": False}):
        with pytest.raises(ValueError):
            import_characterization(source, registry, run_id="invalid", workspace_id="team", owner="alice", metadata={}, diagnostics=[{**artifact, **changed}])
    assert not (registry / "invalid").exists()
