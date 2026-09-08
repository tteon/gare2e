#!/usr/bin/env python3
"""Run local admission gates before provisioning an AgenticOS GPU instance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from seocho.eval.agentic_os_admission import (
    DEFAULT_REQUIRED_ARMS,
    build_admission_report,
    probe_dozerdb,
    probe_gtsqa_oxigraph,
    validate_frozen_artifacts,
    validate_grbench_import_receipt,
    validate_observability_and_privacy,
    validate_required_runners,
    validate_s4_semantic_bundle,
)
from seocho.eval.agentic_os_artifacts import canonical_digest
from seocho.eval.agentic_graph_agents import grbench_react_tools, gtsqa_react_tools


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--import-receipt", required=True, type=Path)
    parser.add_argument("--gtsqa-parquet", required=True, type=Path)
    parser.add_argument("--stage", choices=("static", "main"), default="main")
    parser.add_argument(
        "--lane",
        choices=("grbench", "gtsqa"),
        help="Main-stage lane used to bind the frozen cache to its runtime discovery-tool schema.",
    )
    parser.add_argument("--discovery-cache", type=Path)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--semantic-bundle", type=Path)
    parser.add_argument("--taxonomy-catalog", type=Path)
    parser.add_argument("--previous-taxonomy-catalog", type=Path)
    parser.add_argument("--report-out", required=True, type=Path)
    parser.add_argument("--sample-out", required=True, type=Path)
    parser.add_argument("--telemetry", action="append", type=Path, default=[])
    parser.add_argument("--privacy-sentinel", action="append", default=[])
    parser.add_argument("--required-arm", action="append", default=[])
    parser.add_argument("--bolt-uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--username", default="neo4j")
    parser.add_argument("--password-env", default="NEO4J_PASSWORD")
    args = parser.parse_args()

    import_gate = validate_grbench_import_receipt(args.import_receipt)
    import_detail = import_gate.detail
    workspace_id = str(import_detail.get("workspace_id", ""))
    gates = [
        validate_observability_and_privacy(
            telemetry_paths=args.telemetry,
            sentinels=args.privacy_sentinel,
            sample_output=args.sample_out,
        ),
        validate_required_runners(args.repo_root),
    ]
    if args.lane in {None, "grbench"}:
        gates.extend(
            (
                import_gate,
                probe_dozerdb(
                    uri=args.bolt_uri,
                    username=args.username,
                    password=os.environ.get(args.password_env, "").strip(),
                    database=args.database,
                    workspace_id=workspace_id,
                ),
            )
        )
    if args.lane in {None, "gtsqa"}:
        gates.append(probe_gtsqa_oxigraph(args.gtsqa_parquet))
    if args.stage == "main":
        required_paths = {
            "--discovery-cache": args.discovery_cache,
            "--cohort": args.cohort,
            "--schedule": args.schedule,
            "--lane": args.lane,
        }
        if args.lane == "grbench":
            required_paths.update(
                {
                    "--semantic-bundle": args.semantic_bundle,
                    "--taxonomy-catalog": args.taxonomy_catalog,
                    "--previous-taxonomy-catalog": args.previous_taxonomy_catalog,
                }
            )
        absent = [name for name, value in required_paths.items() if value is None]
        if absent:
            parser.error("main stage requires " + ", ".join(absent))
        graph_digest = (
            str(import_detail.get("schema_digest", ""))
            if args.lane == "grbench"
            else _sha256(args.gtsqa_parquet)
        )
        gates.append(
            validate_frozen_artifacts(
                discovery_path=args.discovery_cache,
                cohort_path=args.cohort,
                schedule_path=args.schedule,
                graph_digest=graph_digest,
                required_arms=(
                    tuple(args.required_arm)
                    or (("B0", "C-", "S1") if args.lane == "grbench" else DEFAULT_REQUIRED_ARMS)
                ),
                require_serving_tokenizer=True,
                expected_tool_schema_digest=canonical_digest(
                    (
                        grbench_react_tools()
                        if args.lane == "grbench"
                        else gtsqa_react_tools()
                    )[0]["function"]
                ),
            )
        )
        if args.lane == "grbench":
            gates.append(
                validate_s4_semantic_bundle(
                    import_receipt_path=args.import_receipt,
                    discovery_path=args.discovery_cache,
                    semantic_bundle_path=args.semantic_bundle,
                    taxonomy_catalog_path=args.taxonomy_catalog,
                    previous_taxonomy_catalog_path=args.previous_taxonomy_catalog,
                )
            )
    report = build_admission_report(gates, stage=args.stage)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"admitted": report["admitted"], "counts": report["counts"], "report": str(args.report_out)}, sort_keys=True))
    return 0 if report["admitted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
