#!/usr/bin/env python3
"""Execute frozen SEOCHO AgenticOS treatment queries against DozerDB.

This runner is intentionally LLM-free.  It is the prepared-query replay and
admission surface used to validate S2a/S2b/S3/S4 before an agent E2E runner is
allowed to claim a component effect.  Generated query text and result rows are
written only to an optional private output; telemetry contains digests/counts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from itertools import islice
from typing import Any, Mapping, Sequence

from neo4j import GraphDatabase

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.policy import require_runtime_permission
from runtime.taxonomy_registry import RuntimeTaxonomyRegistry
from seocho.eval.agentic_os_artifacts import (
    Correlation,
    append_jsonl,
    canonical_digest,
    require_fresh_outputs,
    write_manifest,
)
from seocho.eval.agentic_os_treatments import (
    PreparedQuery,
    SemanticBundleAdmission,
    SeochoTreatmentExecutor,
    TreatmentAdmissionError,
    build_treatment_manifest,
    grbench_projection_ontology,
)
from seocho.ontology.versioning import ontology_schema_fingerprint
from seocho.operating_layer import SeochoOS
from seocho.store.graph import Neo4jGraphStore, enforce_read_workspace_scope
from seocho.taxonomy_catalog import TaxonomyCatalog


class _StockDriverStore:
    """GraphStore-shaped stock Neo4j driver adapter for the S2b control arm."""

    def __init__(self, driver: Any) -> None:
        self.driver = driver

    def query(
        self,
        cypher: str,
        *,
        params: Mapping[str, Any] | None = None,
        database: str = "neo4j",
        workspace_id: str | None = None,
        enforce_workspace_filter: bool = False,
    ) -> list[dict[str, Any]]:
        if enforce_workspace_filter:
            enforce_read_workspace_scope(cypher)
        values = dict(params or {})
        if workspace_id is not None and "workspace_id" not in values:
            values["workspace_id"] = workspace_id
        with self.driver.session(database=database, default_access_mode="READ") as session:
            return [record.data() for record in session.run(cypher, parameters=values)]


def _load_prepared(path: Path, workspace_id: str) -> list[PreparedQuery]:
    queries: list[PreparedQuery] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            try:
                queries.append(
                    PreparedQuery(
                        query_id=str(item["query_id"]),
                        cypher=str(item["cypher"]),
                        params=dict(item.get("params") or {}),
                        workspace_id=workspace_id,
                    )
                )
            except Exception as exc:
                raise ValueError(f"invalid prepared query at line {line_number}") from exc
    if not queries:
        raise ValueError("prepared query file was empty")
    return queries


def _load_s4_admission(
    *,
    semantic_bundle_path: Path,
    taxonomy_catalog_path: Path,
    discovery_cache_path: Path,
    projection_receipt_path: Path,
    workspace_id: str,
) -> tuple[SemanticBundleAdmission, TaxonomyCatalog]:
    catalog_payload = json.loads(taxonomy_catalog_path.read_text(encoding="utf-8"))
    catalog = TaxonomyCatalog.from_dict(catalog_payload)
    recorded_digest = str(catalog_payload.get("catalog_sha256", ""))
    if recorded_digest and recorded_digest != catalog.digest:
        raise TreatmentAdmissionError("taxonomy catalog content does not match its digest")
    bundle_payload = json.loads(semantic_bundle_path.read_text(encoding="utf-8"))
    discovery_payload = json.loads(discovery_cache_path.read_text(encoding="utf-8"))
    projection_payload = json.loads(projection_receipt_path.read_text(encoding="utf-8"))
    claimed_workspace = str(bundle_payload.get("workspace_id", ""))
    if claimed_workspace and claimed_workspace != workspace_id:
        raise TreatmentAdmissionError("semantic bundle workspace does not match the run")
    claimed_taxonomy = str(bundle_payload.get("taxonomy_digest", ""))
    if claimed_taxonomy and claimed_taxonomy != catalog.digest:
        raise TreatmentAdmissionError("semantic bundle taxonomy digest does not match the catalog")
    if str(bundle_payload.get("discovery_cache_digest", "")) != str(discovery_payload.get("cache_digest", "")):
        raise TreatmentAdmissionError("semantic bundle discovery cache digest mismatch")
    if str(bundle_payload.get("graph_digest", "")) != str(projection_payload.get("schema", {}).get("schema_digest", "")):
        raise TreatmentAdmissionError("semantic bundle graph digest mismatch")
    required_relations = tuple(
        str(value) for value in projection_payload.get("schema", {}).get("topology", {}).get("relation_names", {})
    )
    version = str(bundle_payload.get("taxonomy_version", ""))
    registry = RuntimeTaxonomyRegistry()
    registered = registry.register(catalog, workspace_id=workspace_id, version=version)
    active = registry.activate_registered(registered)
    # Activation is sourced from the runtime registry, not trusted from the
    # file.  All other fields remain immutable bundle inputs.
    bound = {
        **bundle_payload,
        "workspace_id": workspace_id,
        "taxonomy_digest": catalog.digest,
        "active_taxonomy_digest": active.catalog_sha256,
        "taxonomy_version": active.version,
    }
    semantic = SemanticBundleAdmission.from_mapping(bound)
    semantic.validate(
        workspace_id=workspace_id,
        graph_digest=str(projection_payload["schema"]["schema_digest"]),
        ontology_fingerprint=semantic.ontology_fingerprint,
        required_relation_names=required_relations,
    )
    return semantic, catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=("S2a", "S2b", "S3", "S4"))
    parser.add_argument("--prepared-queries", required=True, type=Path)
    parser.add_argument("--manifest-out", required=True, type=Path)
    parser.add_argument("--run-manifest-out", type=Path)
    parser.add_argument("--receipts-out", required=True, type=Path)
    parser.add_argument("--private-rows-out", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--graph-digest", required=True)
    parser.add_argument("--tool-schema-digest", required=True)
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--bolt-user", default="neo4j")
    parser.add_argument("--repetition", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-phase", choices=("cold", "warm", "none"), default="none")
    parser.add_argument("--row-cap", type=int, default=20)
    parser.add_argument("--token-budget", type=int, default=0)
    parser.add_argument("--max-inflight", type=int, default=1)
    parser.add_argument("--semantic-bundle", type=Path)
    parser.add_argument("--taxonomy-catalog", type=Path)
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--projection-receipt", type=Path)
    args = parser.parse_args()

    if args.arm in {"S2b", "S4"} and args.token_budget <= 0:
        raise TreatmentAdmissionError("governed arms require a positive --token-budget")
    run_manifest_out = args.run_manifest_out or args.manifest_out.with_name(
        f"{args.run_id}-run-manifest.json"
    )
    require_fresh_outputs(
        (args.manifest_out, run_manifest_out, args.receipts_out, args.private_rows_out)
    )

    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set")
    queries = _load_prepared(args.prepared_queries, args.workspace_id)
    ontology = grbench_projection_ontology()
    ontology_fingerprint = ontology_schema_fingerprint(ontology)
    semantic: SemanticBundleAdmission | None = None
    if args.arm == "S4":
        if any(value is None for value in (args.semantic_bundle, args.taxonomy_catalog, args.discovery_cache, args.projection_receipt)):
            raise TreatmentAdmissionError("S4 requires semantic bundle, taxonomy catalog, discovery cache, and projection receipt")
        semantic, _ = _load_s4_admission(
            semantic_bundle_path=args.semantic_bundle,
            taxonomy_catalog_path=args.taxonomy_catalog,
            discovery_cache_path=args.discovery_cache,
            projection_receipt_path=args.projection_receipt,
            workspace_id=args.workspace_id,
        )

    policy = {
        "workspace_id": args.workspace_id,
        "row_cap": args.row_cap,
        "token_budget": args.token_budget,
        "max_inflight": args.max_inflight,
        "runtime_permission": "run_agent" if args.arm in {"S2b", "S4"} else None,
    }
    manifest = build_treatment_manifest(
        arm=args.arm,
        workspace_id=args.workspace_id,
        graph_digest=args.graph_digest,
        ontology_fingerprint=ontology_fingerprint if args.arm == "S4" else "",
        prepared_queries=queries,
        tool_schema_digest=args.tool_schema_digest,
        policy=policy,
        semantic_admission=semantic,
    )
    write_manifest(args.manifest_out, manifest)
    write_manifest(
        run_manifest_out,
        {
            "schema_version": "agentic_os.run_manifest.v1",
            "run_id": args.run_id,
            "lane": "GRBench-Prepared-Text2Cypher-Replay",
            "arm": args.arm,
            "mechanism_manifest_digest": manifest["arm_digest"],
            "config": {
                "model": "none_prepared_query_replay",
                "reasoning_mode": "none",
                "topology": "prepared_query_replay",
                "arm": args.arm,
                "seed": args.seed,
                "repetition": args.repetition,
                "cache_phase": args.cache_phase,
                "row_cap": args.row_cap,
                "token_budget": args.token_budget,
            },
        },
    )

    driver = GraphDatabase.driver(args.bolt_uri, auth=(args.bolt_user, password))
    seocho_store: Neo4jGraphStore | None = None
    private_rows: dict[str, Sequence[Mapping[str, Any]]] = {}
    try:
        direct_executor = None
        graph_store = None
        os_layer = None
        os_session = None
        policy_check = None
        if args.arm == "S2a":
            def direct_executor(cypher: str, params: Mapping[str, Any], database: str):
                with driver.session(database=database, default_access_mode="READ") as session:
                    result = session.run(cypher, parameters=dict(params))
                    return [record.data() for record in islice(result, args.row_cap)]
        elif args.arm == "S2b":
            stock_store = _StockDriverStore(driver)
            os_layer = SeochoOS(
                ontology=ontology,
                graph_store=stock_store,
                database=args.database,
                workspace_id=args.workspace_id,
                max_inflight=args.max_inflight,
                token_budget=args.token_budget,
                row_cap=args.row_cap,
            )
            os_session = os_layer.session(f"{args.run_id}-query-replay")
            policy_check = require_runtime_permission
        else:
            seocho_store = Neo4jGraphStore(args.bolt_uri, args.bolt_user, password)
            graph_store = seocho_store
            if args.arm == "S4":
                os_layer = SeochoOS(
                    ontology=ontology,
                    graph_store=seocho_store,
                    database=args.database,
                    workspace_id=args.workspace_id,
                    max_inflight=args.max_inflight,
                    token_budget=args.token_budget,
                    row_cap=args.row_cap,
                )
                os_session = os_layer.session(f"{args.run_id}-query-replay")
                policy_check = require_runtime_permission

        executor = SeochoTreatmentExecutor(
            manifest=manifest,
            database=args.database,
            direct_executor=direct_executor,
            graph_store=graph_store,
            os_layer=os_layer,
            os_session=os_session,
            policy_check=policy_check,
            semantic_admission=semantic,
        )
        for query in queries:
            execution = executor.execute(query)
            correlation = Correlation(
                run_id=args.run_id,
                case_id=query.query_id,
                arm=args.arm,
                repetition=args.repetition,
                cache_phase=args.cache_phase,
                role="prepared_query_replay",
                seed=args.seed,
            )
            append_jsonl(args.receipts_out, {**correlation.receipt_fields(), **execution.receipt})
            if args.private_rows_out is not None:
                private_rows[query.query_id] = execution.rows
    finally:
        if seocho_store is not None:
            seocho_store.close()
        driver.close()

    if args.private_rows_out is not None:
        args.private_rows_out.parent.mkdir(parents=True, exist_ok=True)
        args.private_rows_out.write_text(
            json.dumps(private_rows, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    summary = {
        "arm": args.arm,
        "arm_digest": manifest["arm_digest"],
        "query_count": len(queries),
        "receipt_path_digest": canonical_digest(str(args.receipts_out)),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
