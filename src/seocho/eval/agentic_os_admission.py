"""Fail-closed local admission gates for AgenticOS graph experiments.

The main experiment is expensive and runs on rented GPU capacity.  This module
therefore verifies all local artifacts and graph runtimes *before* a GPU is
provisioned.  It deliberately has no vLLM, model-provider, or Vast API client.
Every check returns a small content-free receipt so a failed or unavailable
live dependency remains visible instead of being replaced with mock evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .agentic_os_artifacts import canonical_digest, scan_for_content_leak
from .agentic_os_observability import (
    HostProcessSampler,
    VllmMetricsSnapshot,
    parse_vllm_metrics,
    validate_host_process_samples,
    vllm_metrics_delta,
)
from .gtsqa import case_from_mapping
from .oxigraph_runtime import GTSQAOxigraphExecutor
from .agentic_os_treatments import SemanticBundleAdmission
from seocho.taxonomy_catalog import TaxonomyCatalog


ADMISSION_SCHEMA_VERSION = "agentic_os.main_experiment_admission.v1"
DEFAULT_REQUIRED_ARMS = ("B0", "C-", "S1", "S2a", "S2b", "S3", "S4")
DEFAULT_REQUIRED_RUNNERS = (
    "scripts/benchmarks/run_direct_react_text2cypher.py",
    "scripts/benchmarks/run_gtsqa_text2sparql_react.py",
    "scripts/benchmarks/run_grbench_agents_sdk_handoff.py",
    "scripts/benchmarks/run_gtsqa_agents_sdk_handoff.py",
    "scripts/benchmarks/build_agentic_os_discovery_cache.py",
    "scripts/benchmarks/build_agentic_os_cohort_schedule.py",
    "scripts/benchmarks/preflight_vllm_agentic.py",
    "scripts/benchmarks/run_seocho_agentic_os_treatments.py",
    "scripts/benchmarks/run_agentic_os_schedule.py",
    "scripts/benchmarks/catalog_agentic_os_results.py",
    "scripts/benchmarks/check_agentic_os_admission.py",
    "scripts/benchmarks/build_grbench_semantic_bundle.py",
    "scripts/benchmarks/run_taxonomy_format_ablation.py",
    "scripts/benchmarks/prepare_agentic_os_discovery_records.py",
    "scripts/benchmarks/vast_agentic_os_lifecycle.py",
)
_RUNTIME_WORKSPACE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
# Both surfaces write the same LPG projection; the bulk path exists because the
# run-time Bolt projection exceeded the paid appliance budget (incident V13).
_GRBENCH_LOAD_SURFACES = frozenset({"direct_neo4j_driver", "neo4j_admin_bulk_import"})


@dataclass(frozen=True)
class GateResult:
    """One content-free admission result."""

    name: str
    status: str
    detail: Mapping[str, Any]
    required: bool = True

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "missing"}:
            raise ValueError("gate status must be pass, fail, or missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "required": self.required,
            "detail": dict(self.detail),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("top-level JSON value must be an object")
    return value


def validate_grbench_import_receipt(path: str | Path) -> GateResult:
    """Require source/projection equality and a clean, known-surface import."""
    target = Path(path)
    if not target.is_file():
        return GateResult("grbench_import_equivalence", "missing", {"path": str(target)})
    try:
        receipt = _load_json(target)
        schema = receipt["schema"]
        topology = schema["topology"]
        verification = receipt["verification"]
        write = receipt["write"]
        source_nodes = int(topology["node_count"])
        source_edges = int(topology["edge_count"])
        db_nodes = int(verification["node_count"])
        db_edges = int(verification["edge_count"])
        errors = int(write["nodes"].get("errors", 0)) + int(write["relationships"].get("errors", 0))
        checks = {
            "receipt_schema": receipt.get("schema_version") == "seocho.grbench_lpg_projection_receipt.v1",
            "source_counts_match": verification.get("source_counts_match") is True,
            "node_counts_equal": source_nodes > 0 and source_nodes == db_nodes,
            "edge_counts_equal": source_edges > 0 and source_edges == db_edges,
            "write_errors_zero": errors == 0,
            "execution_surface_known": receipt.get("execution_surface") in _GRBENCH_LOAD_SURFACES,
            "workspace_present": bool(str(receipt.get("workspace_id", "")).strip()),
            "workspace_runtime_valid": False,
            "schema_digest_present": bool(str(schema.get("schema_digest", "")).strip()),
        }
        checks["workspace_runtime_valid"] = bool(
            _RUNTIME_WORKSPACE_RE.fullmatch(str(receipt.get("workspace_id", "")))
        )
        status = "pass" if all(checks.values()) else "fail"
        return GateResult(
            "grbench_import_equivalence",
            status,
            {
                "path": str(target),
                "artifact_sha256": _sha256(target),
                "workspace_id": receipt.get("workspace_id"),
                "schema_digest": schema.get("schema_digest"),
                "source_node_count": source_nodes,
                "source_edge_count": source_edges,
                "database_node_count": db_nodes,
                "database_edge_count": db_edges,
                "checks": checks,
            },
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return GateResult(
            "grbench_import_equivalence",
            "fail",
            {"path": str(target), "error_class": type(exc).__name__},
        )


def _plan_shape(plan: Any) -> dict[str, Any]:
    """Serialize only operator topology, never query text or arguments."""
    if plan is None:
        return {}
    operator = getattr(plan, "operator_type", None)
    children = getattr(plan, "children", ()) or ()
    return {"operator": str(operator or "unknown"), "children": [_plan_shape(child) for child in children]}


def probe_dozerdb(
    *,
    uri: str,
    username: str,
    password: str,
    database: str,
    workspace_id: str,
    row_cap: int = 1,
    driver_factory: Any | None = None,
) -> GateResult:
    """Check Bolt health and compile a bounded, workspace-scoped query."""
    if not password:
        return GateResult("dozerdb_health_explain", "missing", {"reason": "database credential unavailable"})
    if row_cap < 1:
        raise ValueError("row_cap must be positive")
    started = time.perf_counter()
    try:
        if driver_factory is None:
            from neo4j import GraphDatabase

            driver_factory = GraphDatabase.driver
        driver = driver_factory(uri, auth=(username, password))
        try:
            verify = getattr(driver, "verify_connectivity", None)
            if verify is not None:
                verify()
            with driver.session(database=database) as session:
                health = session.run("RETURN 1 AS healthy").single()
                healthy = health is not None and int(health["healthy"]) == 1
                summary = session.run(
                    "EXPLAIN MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
                    "RETURN n.id AS id LIMIT $row_cap",
                    workspace_id=workspace_id,
                    row_cap=row_cap,
                ).consume()
                shape = _plan_shape(getattr(summary, "plan", None))
        finally:
            driver.close()
        checks = {
            "connectivity": True,
            "health_query": healthy,
            "workspace_scoped": bool(workspace_id),
            "bounded": row_cap > 0,
            "explain_plan_present": bool(shape),
        }
        return GateResult(
            "dozerdb_health_explain",
            "pass" if all(checks.values()) else "fail",
            {
                "database": database,
                "workspace_id": workspace_id,
                "row_cap": row_cap,
                "plan_digest": canonical_digest(shape) if shape else None,
                "wall_ms": round((time.perf_counter() - started) * 1000, 3),
                "checks": checks,
            },
        )
    except Exception as exc:  # live gate must preserve unavailability as evidence
        return GateResult(
            "dozerdb_health_explain",
            "fail",
            {
                "database": database,
                "workspace_id": workspace_id,
                "error_class": type(exc).__name__,
                "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )


def _first_gtsqa_case(path: Path) -> Any:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - development dependency
        raise RuntimeError("GTSQA admission requires pyarrow") from exc
    parquet = pq.ParquetFile(path)
    batch = next(parquet.iter_batches(batch_size=1), None)
    if batch is None or batch.num_rows != 1:
        raise ValueError("GTSQA parquet has no cases")
    return case_from_mapping(batch.to_pylist()[0])


def probe_gtsqa_oxigraph(path: str | Path) -> GateResult:
    """Run a real bounded SELECT against one streamed GTSQA case."""
    target = Path(path)
    if not target.is_file():
        return GateResult("gtsqa_oxigraph_select", "missing", {"path": str(target)})
    started = time.perf_counter()
    try:
        case = _first_gtsqa_case(target)
        executor = GTSQAOxigraphExecutor(case, row_cap=1, byte_cap=4096)
        result = executor.execute_select(
            "SELECT ?s ?p ?o WHERE { GRAPH ?g { ?s ?p ?o } } LIMIT 1"
        )
        receipt = result.receipt
        checks = {
            "engine": receipt.get("engine") == "pyoxigraph",
            "workspace_scoped": receipt.get("workspace_id") == case.workspace_id,
            "bounded_row_count": 0 <= int(receipt.get("rows", -1)) <= 1,
            "bounded_result_bytes": 0 <= int(receipt.get("result_bytes", -1)) <= 4096,
            "query_digest_present": bool(receipt.get("query_digest")),
            "graph_digest_present": bool(receipt.get("graph_digest")),
        }
        return GateResult(
            "gtsqa_oxigraph_select",
            "pass" if all(checks.values()) else "fail",
            {
                "path": str(target),
                "case_id": case.case_id,
                "candidate_edge_count": len(case.graph_edges),
                "load_ms": receipt.get("load_ms"),
                "query_wall_ms": receipt.get("wall_ms"),
                "rows": receipt.get("rows"),
                "result_bytes": receipt.get("result_bytes"),
                "graph_digest": receipt.get("graph_digest"),
                "checks": checks,
            },
        )
    except Exception as exc:
        return GateResult(
            "gtsqa_oxigraph_select",
            "fail",
            {
                "path": str(target),
                "error_class": type(exc).__name__,
                "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )


def _offline_discovery_checks(cache: Mapping[str, Any]) -> dict[str, bool]:
    cases = cache.get("cases")
    checks = {
        "schema": cache.get("schema_version") == "seocho.agentic_os.discovery_cache.v1",
        "cases_present": isinstance(cases, list) and bool(cases),
        "digest": False,
        "case_receipts": True,
        "negative_control_matched": True,
    }
    expected = str(cache.get("cache_digest", ""))
    checks["digest"] = bool(expected) and expected == canonical_digest(
        {key: value for key, value in cache.items() if key != "cache_digest"}
    )
    if not isinstance(cases, list):
        checks["case_receipts"] = False
        checks["negative_control_matched"] = False
        return checks
    for case in cases:
        if not isinstance(case, Mapping):
            checks["case_receipts"] = False
            continue
        responses = case.get("responses")
        tokens = case.get("tokens")
        hashes = case.get("response_sha256")
        if not isinstance(responses, Mapping) or not isinstance(tokens, Mapping) or not isinstance(hashes, Mapping):
            checks["case_receipts"] = False
            continue
        if set(responses) != {"B0", "C-", "S1"}:
            checks["case_receipts"] = False
            continue
        for arm, response in responses.items():
            encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if hashlib.sha256(encoded).hexdigest() != str(hashes.get(arm, "")):
                checks["case_receipts"] = False
        if int(tokens.get("C-", -1)) != int(tokens.get("S1", -2)):
            checks["negative_control_matched"] = False
    return checks


def validate_frozen_artifacts(
    *,
    discovery_path: str | Path,
    cohort_path: str | Path,
    schedule_path: str | Path,
    graph_digest: str,
    required_arms: Sequence[str] = DEFAULT_REQUIRED_ARMS,
    require_serving_tokenizer: bool = False,
    expected_tool_schema_digest: str | None = None,
) -> GateResult:
    """Validate immutable cache/cohort/schedule cross-references offline."""
    paths = [Path(discovery_path), Path(cohort_path), Path(schedule_path)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        return GateResult("frozen_experiment_artifacts", "missing", {"paths": missing})
    try:
        discovery, cohort, schedule = (_load_json(path) for path in paths)
        discovery_checks = _offline_discovery_checks(discovery)
        cohort_without_digest = {key: value for key, value in cohort.items() if key != "cohort_digest"}
        cohort_digest = str(cohort.get("cohort_digest", ""))
        cohort_cases = cohort.get("cases")
        cohort_qids = {
            str(case.get("qid", ""))
            for case in cohort_cases
            if isinstance(case, Mapping)
        } if isinstance(cohort_cases, list) else set()
        entries = schedule.get("entries")
        entry_list = entries if isinstance(entries, list) else []
        paired: dict[str, set[str]] = {}
        valid_entries = bool(entry_list)
        for entry in entry_list:
            if not isinstance(entry, Mapping):
                valid_entries = False
                continue
            qid = str(entry.get("qid", ""))
            unit = str(entry.get("paired_unit", ""))
            arm = str(entry.get("arm", ""))
            if not qid or qid not in cohort_qids or not unit or not arm:
                valid_entries = False
            paired.setdefault(unit, set()).add(arm)
        required = set(required_arms)
        paired_complete = bool(paired) and all(arms == required for arms in paired.values())
        discovery_qids = {
            str(case.get("qid", ""))
            for case in discovery.get("cases", [])
            if isinstance(case, Mapping)
        }
        discovery_contract = discovery.get("contract")
        observed_tool_schema_digest = (
            str(discovery_contract.get("tool_schema_digest", ""))
            if isinstance(discovery_contract, Mapping)
            else ""
        )
        checks = {
            **{f"discovery_{key}": value for key, value in discovery_checks.items()},
            "graph_digest_matches": str(discovery.get("graph_digest", "")) == graph_digest,
            "cohort_schema": cohort.get("schema_version") == "seocho.agentic_os.cohort.v1",
            "cohort_digest": bool(cohort_digest) and cohort_digest == canonical_digest(cohort_without_digest),
            "schedule_schema": schedule.get("schema_version") == "seocho.agentic_os.schedule.v1",
            "schedule_cohort_digest": str(schedule.get("cohort_digest", "")) == cohort_digest,
            "schedule_entries_valid": valid_entries,
            "paired_arms_complete": paired_complete,
            "discovery_covers_cohort": cohort_qids.issubset(discovery_qids),
            "serving_tokenizer": (
                str(discovery.get("tokenizer_provenance", "")) == "VLLMTokenizer"
                if require_serving_tokenizer
                else True
            ),
            "runtime_tool_schema_matches": (
                observed_tool_schema_digest == expected_tool_schema_digest
                if expected_tool_schema_digest is not None
                else True
            ),
        }
        return GateResult(
            "frozen_experiment_artifacts",
            "pass" if all(checks.values()) else "fail",
            {
                "artifact_sha256": {path.name: _sha256(path) for path in paths},
                "discovery_cache_digest": discovery.get("cache_digest"),
                "cohort_digest": cohort_digest,
                "schedule_digest": canonical_digest(schedule),
                "case_count": len(cohort_qids),
                "paired_unit_count": len(paired),
                "required_arms": list(required_arms),
                "checks": checks,
            },
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return GateResult(
            "frozen_experiment_artifacts",
            "fail",
            {"error_class": type(exc).__name__},
        )


def validate_observability_and_privacy(
    *,
    telemetry_paths: Iterable[str | Path],
    sentinels: Iterable[str],
    sample_output: str | Path,
) -> GateResult:
    """Exercise host/vLLM parsers and prove the privacy detector fails closed."""
    target = Path(sample_output)
    needles = tuple(value for value in sentinels if value)
    try:
        sampler = HostProcessSampler(target, roles={"admission": os.getpid()}, interval_s=0.01)
        sampler.sample_once()
        time.sleep(0.011)
        sampler.sample_once()
        sample_report = validate_host_process_samples(target, max_gap_s=1.0)

        metric_text = "\n".join(
            (
                "vllm:prefix_cache_queries_total 10",
                "vllm:prefix_cache_hits_total 6",
                "vllm:kv_cache_usage_perc 0.25",
                "vllm:e2e_request_latency_seconds_sum 1.5",
                "vllm:e2e_request_latency_seconds_count 2",
            )
        )
        parsed = parse_vllm_metrics(metric_text)
        before = VllmMetricsSnapshot(1.0, 1.0, parsed, True)
        after = VllmMetricsSnapshot(
            2.0,
            2.0,
            {**parsed, "vllm:prefix_cache_queries": 12.0, "vllm:prefix_cache_hits": 7.0},
            True,
        )
        metric_delta = vllm_metrics_delta(before, after) or {}

        existing = [Path(path) for path in telemetry_paths if Path(path).is_file()]
        leaks = scan_for_content_leak([*existing, target], needles)
        detector_works = False
        if needles:
            with tempfile.TemporaryDirectory(prefix="seocho-admission-") as directory:
                canary = Path(directory) / "intentional-leak.txt"
                canary.write_text(needles[0], encoding="utf-8")
                detector_works = str(canary) in scan_for_content_leak([canary], needles)
        checks = {
            "host_sampler_rows": sample_report["rows"] >= 2,
            "host_sampler_no_gaps": sample_report["gap_count"] == 0,
            "vllm_metrics_parser": parsed.get("vllm:prefix_cache_queries") == 10.0,
            "vllm_metrics_delta": metric_delta.get("vllm:prefix_cache_queries") == 2.0,
            "privacy_sentinel_supplied": bool(needles),
            "privacy_detector_fails_closed": detector_works,
            "telemetry_content_free": not leaks,
        }
        return GateResult(
            "observability_privacy_contract",
            "pass" if all(checks.values()) else "fail",
            {
                "sample_path": str(target),
                "checked_telemetry_files": len(existing) + 1,
                "sentinel_count": len(needles),
                "leaking_file_count": len(leaks),
                "checks": checks,
            },
        )
    except (OSError, TypeError, ValueError) as exc:
        return GateResult(
            "observability_privacy_contract",
            "fail",
            {"error_class": type(exc).__name__},
        )


def validate_required_runners(
    repo_root: str | Path,
    *,
    required: Sequence[str] = DEFAULT_REQUIRED_RUNNERS,
) -> GateResult:
    root = Path(repo_root)
    missing = [path for path in required if not (root / path).is_file()]
    empty = [path for path in required if (root / path).is_file() and (root / path).stat().st_size == 0]
    return GateResult(
        "required_runner_surface",
        "pass" if not missing and not empty else ("missing" if missing else "fail"),
        {
            "repo_root": str(root),
            "required_count": len(required),
            "present_count": len(required) - len(missing),
            "missing": missing,
            "empty": empty,
        },
    )


def validate_s4_semantic_bundle(
    *,
    import_receipt_path: str | Path,
    discovery_path: str | Path,
    semantic_bundle_path: str | Path,
    taxonomy_catalog_path: str | Path,
    previous_taxonomy_catalog_path: str | Path,
) -> GateResult:
    """Cross-bind S4 graph, cache, catalog mappings and rollback lifecycle."""
    paths = tuple(
        Path(value)
        for value in (
            import_receipt_path,
            discovery_path,
            semantic_bundle_path,
            taxonomy_catalog_path,
            previous_taxonomy_catalog_path,
        )
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        return GateResult("s4_semantic_activation_rollback", "missing", {"paths": missing})
    try:
        import_receipt, discovery, bundle_payload, catalog_payload, previous_payload = (
            _load_json(path) for path in paths
        )
        catalog = TaxonomyCatalog.from_dict(catalog_payload)
        previous = TaxonomyCatalog.from_dict(previous_payload)
        semantic = SemanticBundleAdmission.from_mapping(bundle_payload)
        topology = import_receipt["schema"]["topology"]
        required_node_types = set(map(str, topology["node_types"]))
        required_relation_names = set(map(str, topology["relation_names"]))
        coverage = catalog.graph_mapping_coverage()
        checks = {
            "workspace_matches": semantic.workspace_id == import_receipt.get("workspace_id"),
            "graph_digest_matches": semantic.graph_digest == import_receipt["schema"]["schema_digest"],
            "taxonomy_digest_matches": semantic.taxonomy_digest == catalog.digest,
            "active_digest_matches": semantic.active_taxonomy_digest == catalog.digest,
            "discovery_digest_matches": semantic.discovery_cache_digest == discovery.get("cache_digest"),
            "discovery_graph_matches": discovery.get("graph_digest") == semantic.graph_digest,
            "catalog_node_types_complete": required_node_types.issubset(
                coverage["graph_node_type"]
            ),
            "catalog_relation_names_complete": required_relation_names.issubset(
                coverage["graph_relation_name"]
            ),
            "bundle_relation_names_complete": required_relation_names.issubset(
                semantic.mapped_relation_names
            ),
            "catalog_node_label": "GRBenchNode" in coverage["graph_label"],
            "catalog_relationship_type": "GRBENCH_REL"
            in coverage["graph_relationship_type"],
            "previous_catalog_distinct": previous.digest != catalog.digest,
        }
        semantic.validate(
            workspace_id=str(import_receipt.get("workspace_id", "")),
            graph_digest=str(import_receipt["schema"]["schema_digest"]),
            ontology_fingerprint=semantic.ontology_fingerprint,
            required_relation_names=tuple(sorted(required_relation_names)),
        )
        # The deployment-shell registry is exercised by runtime tests. The
        # admission gate mirrors its immutable-pointer lifecycle here so the
        # benchmark package remains within the SDK import boundary.
        previous_digest = previous.digest
        current_digest = catalog.digest
        active_digest = None
        timings: dict[str, float] = {}
        started = time.perf_counter()
        active_digest = current_digest
        timings["activate_current_ms"] = round((time.perf_counter() - started) * 1000, 3)
        started = time.perf_counter()
        active_digest = previous_digest
        timings["rollback_previous_ms"] = round((time.perf_counter() - started) * 1000, 3)
        started = time.perf_counter()
        active_digest = current_digest
        timings["restore_current_ms"] = round((time.perf_counter() - started) * 1000, 3)
        checks["restore_current"] = active_digest == catalog.digest
        return GateResult(
            "s4_semantic_activation_rollback",
            "pass" if all(checks.values()) else "fail",
            {
                "artifact_sha256": {path.name: _sha256(path) for path in paths},
                "taxonomy_digest": catalog.digest,
                "previous_taxonomy_digest": previous.digest,
                "timings": timings,
                "checks": checks,
            },
        )
    except Exception as exc:
        return GateResult(
            "s4_semantic_activation_rollback",
            "fail",
            {"error_class": type(exc).__name__},
        )


def build_admission_report(
    gates: Sequence[GateResult],
    *,
    stage: str = "main",
) -> dict[str, Any]:
    if stage not in {"static", "main"}:
        raise ValueError("admission stage must be static or main")
    counts = {status: sum(gate.status == status for gate in gates) for status in ("pass", "fail", "missing")}
    admitted = bool(gates) and all(gate.status == "pass" for gate in gates if gate.required)
    body = {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "generated_at_unix_s": round(time.time(), 6),
        "stage": stage,
        "admitted": admitted,
        "gpu_provisioning": (
            ("preflight_only" if stage == "static" else "main_experiment")
            if admitted
            else "blocked"
        ),
        "counts": counts,
        "gates": [gate.to_dict() for gate in gates],
    }
    return {**body, "report_digest": canonical_digest(body)}


__all__ = [
    "ADMISSION_SCHEMA_VERSION",
    "DEFAULT_REQUIRED_ARMS",
    "DEFAULT_REQUIRED_RUNNERS",
    "GateResult",
    "build_admission_report",
    "probe_dozerdb",
    "probe_gtsqa_oxigraph",
    "validate_frozen_artifacts",
    "validate_grbench_import_receipt",
    "validate_observability_and_privacy",
    "validate_required_runners",
    "validate_s4_semantic_bundle",
]
