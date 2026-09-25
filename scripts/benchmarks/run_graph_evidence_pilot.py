#!/usr/bin/env python3
"""Bounded query-evidence pilot inside the existing co-located appliance.

This is a diagnostic pilot, not a throughput or quality promotion experiment.
It reuses both existing agent runners, the database executor and lifecycle.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase
from seocho.eval.grbench_runtime import GRBenchDozerExecutor
from seocho.eval.query_evidence import QueryEvidence, append_private
from seocho.eval.agentic_os_trace import case_trace_id


def run(
    command: list[str],
    output: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 900,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (
        (output / "stdout.private.log").open("w") as out,
        (output / "stderr.private.log").open("w") as err,
    ):
        subprocess.run(
            command, env=env, stdout=out, stderr=err, timeout=timeout, check=True
        )


def fixed_gate(root: Path, snapshot: str, anchor_id: str) -> None:
    driver = GraphDatabase.driver(
        "bolt://127.0.0.1:7687", auth=("neo4j", os.environ["NEO4J_PASSWORD"])
    )
    query = "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: $id}) RETURN n.id AS id"
    outcomes = []
    try:
        for index, mode in enumerate(
            (
                "observer_off",
                "raw",
                "exact_result_reuse",
                "exact_result_reuse",
                "raw",
                "observer_off",
            )
        ):
            cid = f"run-fixed-{index}-{mode}"
            directory = root / cid
            directory.mkdir(parents=True)
            observer = (
                None
                if mode == "observer_off"
                else QueryEvidence(
                    directory / "fixed.queries.jsonl",
                    run_id=cid,
                    snapshot_id=snapshot,
                    mode=mode,
                )
            )
            if observer:
                observer.session("lookup", trace_id=case_trace_id(cid, "lookup"))
            executor = GRBenchDozerExecutor(
                driver,
                database="neo4j",
                workspace_id="grbench-maple-physics",
                require_evidence_columns=False,
                query_evidence=observer,
            )
            values = []
            times = []
            start = time.time()
            for i in range(20):
                if observer:
                    observer.tool(f"tool-{i}", i)
                t = time.perf_counter()
                text, receipt = executor.execute_rows(query, {"id": anchor_id})
                times.append((time.perf_counter() - t) * 1000)
                values.append(text)
                if not json.loads(text):
                    raise RuntimeError("Fixed gate anchor missing")
            if len(set(values)) != 1:
                raise RuntimeError("Repeated query result parity failed")
            outcomes.append(
                {
                    "condition": cid,
                    "mode": mode,
                    "latency_ms": times,
                    "result_digest": hashlib.sha256(values[0].encode()).hexdigest(),
                    "evidence_class": "fixed_query_diagnostic",
                    "cache_state": "uncontrolled_warm",
                    "observer_enabled": observer is not None,
                }
            )
            # PROFILE only after the timed sequence; distinct scope and no concurrent work.
            if observer:
                observer.tool("profile", 21)
                executor.profile_select(query, {"id": anchor_id})
            (directory / "run-receipt.json").write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "run_id": cid,
                                "case_id": "lookup",
                                "started_unix_s": start,
                                "wall_ms": sum(times),
                                "terminal": "diagnostic_complete",
                                "turns": [],
                                "tools": [],
                            }
                        ]
                    }
                )
                + "\n"
            )
        if len({r["result_digest"] for r in outcomes}) != 1:
            raise RuntimeError("Cross-mode result parity failed")
        (root / "fixed-gate.json").write_text(
            json.dumps({"passed": True, "conditions": outcomes}, indent=2) + "\n"
        )
    finally:
        driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/workspace/input"))
    parser.add_argument("--artifacts", type=Path, default=Path("/artifacts/pilot"))
    parser.add_argument("--anchor-id", required=True)
    parser.add_argument("--question-count", type=int, default=3)
    args = parser.parse_args()
    os.umask(0o077)
    if args.artifacts.exists():
        raise FileExistsError("Pilot root must be fresh")
    root = args.artifacts
    root.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[2]
    scripts = repo / "scripts/benchmarks"
    snapshot = hashlib.sha256(
        (args.input / "grbench-import-manifest.json").read_bytes()
    ).hexdigest()
    model = os.environ["VLLM_MODEL"]
    base = "http://127.0.0.1:8000"
    run(
        [
            sys.executable,
            str(scripts / "preflight_agentic_os_services.py"),
            "--base-url",
            base,
            "--model",
            model,
            "--grbench-import-receipt",
            "/artifacts/e2e/grbench-load-receipt.json",
            "--gtsqa-parquet",
            str(args.input / "gtsqa.parquet"),
            "--receipt-out",
            str(root / "service-readiness.json"),
        ],
        root / "service-gate",
    )
    fixed_gate(root, snapshot, args.anchor_id)
    # A bounded SPARQL tool gate verifies the second appliance backend before cells.
    run(
        [
            sys.executable,
            str(scripts / "run_gtsqa_text2sparql_react.py"),
            "--parquet",
            str(args.input / "gtsqa.parquet"),
            "--base-url",
            base,
            "--model",
            model,
            "--run-root",
            str(root / "sparql-gate"),
            "--run-id",
            "graph-evidence-sparql-gate",
            "--case-id",
            "102",
            "--limit",
            "1",
            "--max-turns",
            "6",
            "--max-output-tokens",
            "512",
            "--token-budget",
            "16384",
            "--enable-thinking",
            "false",
            "--condition",
            "forced_plumbing",
        ],
        root / "sparql-gate-output",
    )
    sparql = [
        json.loads(line)
        for line in (root / "sparql-gate/receipts.jsonl").read_text().splitlines()
    ]
    if not sparql or not all(
        r.get("trace", {}).get("trace_id") and r.get("tools", {}).get("events")
        for r in sparql
    ):
        raise RuntimeError("SPARQL trace/tool gate failed")
    for lane, script in (
        ("text2cypher", "run_direct_react_text2cypher.py"),
        ("graphcot", "run_grbench_graphcot_react.py"),
    ):
        for index, mode in [
            ("gate", "raw"),
            ("0", "raw"),
            ("1", "exact_result_reuse"),
            ("2", "exact_result_reuse"),
            ("3", "raw"),
        ]:
            cid = f"run-{lane}-{index}-{mode}"
            out = root / cid
            out.mkdir()
            env = {
                **os.environ,
                "GARE2E_GRAPH_SNAPSHOT": snapshot,
                "GARE2E_QUERY_MODE": mode,
                "GARE2E_QUERY_EVIDENCE_DIR": str(out / "queries"),
                "GARE2E_PRIVATE_REPLAY": "1",
            }
            command = [
                sys.executable,
                str(scripts / script),
                "--questions",
                str(args.input / "grbench-questions.json"),
                "--base-url",
                base,
                "--model",
                model,
                "--run-id",
                cid,
                "--receipt-out",
                str(out / "run-receipt.json"),
                "--predictions-out",
                str(out / "predictions.private.json"),
                "--manifest-out",
                str(out / "manifest.json"),
                "--limit",
                str(1 if index == "gate" else args.question_count),
                "--max-turns",
                "8",
                "--max-output-tokens",
                "512",
                "--token-budget",
                "16384",
                "--enable-thinking",
                "false",
                "--condition",
                "tool_auto",
                "--seed",
                "0",
                "--metrics-url",
                base + "/metrics",
                "--host-samples-out",
                str(out / "host.csv"),
                "--db-pid",
                os.environ["DOZERDB_PID"],
            ]
            run(command, out, env=env)
            receipt = json.loads((out / "run-receipt.json").read_text())
            logs = [
                json.loads(line)
                for p in (out / "queries").glob("*.queries.jsonl")
                for line in p.read_text().splitlines()
            ]
            gate = (
                bool(logs)
                and all(c.get("trace", {}).get("trace_id") for c in receipt["cases"])
                and any(q["db_queries_executed"] for q in logs)
            )
            append_private(
                root / "progress.jsonl",
                {
                    "condition": cid,
                    "completed_unix_s": time.time(),
                    "query_attempts": len(logs),
                    "trace_tool_gate": gate,
                },
            )
            if not gate:
                raise RuntimeError(
                    "Cypher trace/tool gate failed; later cells not launched"
                )
    run(
        [
            sys.executable,
            str(scripts / "export_graph_query_evidence.py"),
            "--run-root",
            str(root),
            "--output-dir",
            str(root / "report"),
        ],
        root / "export-output",
    )
    (root / "pilot-receipt.json").write_text(
        json.dumps(
            {
                "completed": True,
                "snapshot_id": snapshot,
                "model": model,
                "question_file_sha256": hashlib.sha256(
                    (args.input / "grbench-questions.json").read_bytes()
                ).hexdigest(),
                "concurrency": 1,
                "order": "raw/reuse/reuse/raw within lane",
                "questions_per_cell": args.question_count,
                "limitations": [
                    "Quality unscored; final output is not correctness.",
                    "Warm cache uncontrolled; no cold comparison.",
                    "Pilot does not establish throughput, p99 or GPU causal effect.",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
