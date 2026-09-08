from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from seocho.eval.agentic_os_admission import (
    GateResult,
    build_admission_report,
    probe_dozerdb,
    validate_frozen_artifacts,
    validate_grbench_import_receipt,
    validate_observability_and_privacy,
    validate_required_runners,
)
from seocho.eval.agentic_os_artifacts import canonical_digest


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_import_receipt_requires_exact_source_database_equivalence(tmp_path) -> None:
    path = tmp_path / "import.json"
    receipt = {
        "schema_version": "seocho.grbench_lpg_projection_receipt.v1",
        "execution_surface": "direct_neo4j_driver",
        "workspace_id": "grbench-test",
        "schema": {"schema_digest": "graph-digest", "topology": {"node_count": 2, "edge_count": 1}},
        "verification": {"node_count": 2, "edge_count": 1, "source_counts_match": True},
        "write": {"nodes": {"errors": 0}, "relationships": {"errors": 0}},
    }
    _write_json(path, receipt)
    assert validate_grbench_import_receipt(path).status == "pass"
    receipt["verification"]["edge_count"] = 0
    _write_json(path, receipt)
    failed = validate_grbench_import_receipt(path)
    assert failed.status == "fail"
    assert failed.detail["checks"]["edge_counts_equal"] is False
    receipt["verification"]["edge_count"] = 1
    receipt["execution_surface"] = "neo4j_admin_bulk_import"
    _write_json(path, receipt)
    assert validate_grbench_import_receipt(path).status == "pass"
    receipt["execution_surface"] = "hand_edited"
    _write_json(path, receipt)
    assert validate_grbench_import_receipt(path).detail["checks"]["execution_surface_known"] is False


class _FakeResult:
    def __init__(self, *, health: bool = False) -> None:
        self.health = health

    def single(self):
        return {"healthy": 1} if self.health else None

    def consume(self):
        child = SimpleNamespace(operator_type="NodeIndexSeek", children=[])
        return SimpleNamespace(plan=SimpleNamespace(operator_type="ProduceResults", children=[child]))


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **params):
        if query.startswith("RETURN"):
            return _FakeResult(health=True)
        assert query.startswith("EXPLAIN")
        assert "_workspace_id: $workspace_id" in query
        assert params == {"workspace_id": "grbench:test", "row_cap": 1}
        return _FakeResult()


class _FakeDriver:
    def verify_connectivity(self):
        return None

    def session(self, **_kwargs):
        return _FakeSession()

    def close(self):
        return None


def test_dozerdb_probe_is_bounded_scoped_and_content_free() -> None:
    result = probe_dozerdb(
        uri="bolt://unused",
        username="neo4j",
        password="secret",
        database="neo4j",
        workspace_id="grbench:test",
        driver_factory=lambda *_args, **_kwargs: _FakeDriver(),
    )
    assert result.status == "pass"
    assert result.detail["plan_digest"]
    assert "secret" not in json.dumps(result.to_dict())


def test_frozen_artifacts_cross_validate_digests_coverage_and_pairs(tmp_path) -> None:
    discovery_base = {
        "schema_version": "seocho.agentic_os.discovery_cache.v1",
        "graph_digest": "graph-digest",
        "contract": {"tool_schema_digest": "runtime-tool-schema"},
        "cases": [],
    }
    responses = {"B0": {"x": "a"}, "C-": {"x": "b"}, "S1": {"x": "c"}}
    hashes = {
        arm: __import__("hashlib").sha256(
            json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        for arm, response in responses.items()
    }
    discovery_base["cases"] = [
        {"qid": "q1", "responses": responses, "tokens": {"B0": 1, "C-": 1, "S1": 1}, "response_sha256": hashes}
    ]
    discovery = {**discovery_base, "cache_digest": canonical_digest(discovery_base)}
    cohort_base = {
        "schema_version": "seocho.agentic_os.cohort.v1",
        "dataset": "GRBench",
        "seed": 0,
        "cases": [{"qid": "q1", "stratum": "physics"}],
        "exclusions": {},
    }
    cohort = {**cohort_base, "cohort_digest": canonical_digest(cohort_base)}
    entries = [
        {"qid": "q1", "paired_unit": "q1:0:cold:0", "arm": arm}
        for arm in ("B0", "C-", "S1")
    ]
    schedule = {
        "schema_version": "seocho.agentic_os.schedule.v1",
        "cohort_digest": cohort["cohort_digest"],
        "entries": entries,
    }
    paths = [tmp_path / name for name in ("discovery.json", "cohort.json", "schedule.json")]
    for path, value in zip(paths, (discovery, cohort, schedule)):
        _write_json(path, value)
    result = validate_frozen_artifacts(
        discovery_path=paths[0],
        cohort_path=paths[1],
        schedule_path=paths[2],
        graph_digest="graph-digest",
        required_arms=("B0", "C-", "S1"),
        expected_tool_schema_digest="runtime-tool-schema",
    )
    assert result.status == "pass"
    assert result.detail["schedule_digest"]
    mismatch = validate_frozen_artifacts(
        discovery_path=paths[0],
        cohort_path=paths[1],
        schedule_path=paths[2],
        graph_digest="graph-digest",
        required_arms=("B0", "C-", "S1"),
        expected_tool_schema_digest="changed-runtime-schema",
    )
    assert mismatch.status == "fail"
    assert mismatch.detail["checks"]["runtime_tool_schema_matches"] is False


def test_observability_privacy_self_test_and_runner_missing(tmp_path) -> None:
    result = validate_observability_and_privacy(
        telemetry_paths=[],
        sentinels=["PRIVATE-CANARY"],
        sample_output=tmp_path / "samples.csv",
    )
    assert result.status == "pass"
    assert result.detail["checks"]["privacy_detector_fails_closed"] is True
    without_canary = validate_observability_and_privacy(
        telemetry_paths=[],
        sentinels=[],
        sample_output=tmp_path / "samples-without-canary.csv",
    )
    assert without_canary.status == "fail"
    assert without_canary.detail["checks"]["privacy_sentinel_supplied"] is False
    missing = validate_required_runners(tmp_path, required=("missing.py",))
    assert missing.status == "missing"


def test_report_blocks_gpu_on_fail_or_missing() -> None:
    report = build_admission_report(
        [GateResult("ok", "pass", {}), GateResult("bad", "missing", {})]
    )
    assert report["admitted"] is False
    assert report["gpu_provisioning"] == "blocked"
    assert report["report_digest"]
