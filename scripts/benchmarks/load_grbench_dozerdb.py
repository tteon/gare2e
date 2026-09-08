#!/usr/bin/env python3
"""Project one official GRBench ``graph.json`` into a live DozerDB database.

This is deliberately a baseline loader: it creates no inferred facts and uses
one neutral LPG label/type.  A later ontology/taxonomy arm receives exactly the
same source graph and questions.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path
from typing import Any, Iterable

from seocho.eval.grbench import batched, graph_schema_receipt, iter_grbench_nodes, iter_grbench_relationships


def _write_batches(
    driver: Any,
    batches: Iterable[list[dict[str, Any]]],
    *,
    kind: str,
    database: str,
    workspace_id: str,
    source_id: str,
) -> dict[str, int]:
    total = {"batches": 0, "nodes_created": 0, "relationships_created": 0, "errors": 0}
    for batch in batches:
        if kind == "nodes":
            rows = [
                {
                    "id": item["id"],
                    "props": {**dict(item["properties"]), "_workspace_id": workspace_id, "_source_id": source_id},
                }
                for item in batch
            ]
            statement = (
                "UNWIND $rows AS row "
                "MERGE (n:GRBenchNode {id: row.id, _workspace_id: $workspace_id}) "
                "SET n += row.props"
            )
        else:
            rows = [
                {
                    "source": item["source"],
                    "target": item["target"],
                    "props": {**dict(item["properties"]), "_workspace_id": workspace_id, "_source_id": source_id},
                }
                for item in batch
            ]
            statement = (
                "UNWIND $rows AS row "
                "MATCH (a:GRBenchNode {id: row.source, _workspace_id: $workspace_id}) "
                "MATCH (b:GRBenchNode {id: row.target, _workspace_id: $workspace_id}) "
                "MERGE (a)-[r:GRBENCH_REL {relation_name: row.props.relation_name, _workspace_id: $workspace_id}]->(b) "
                "SET r += row.props"
            )
        with driver.session(database=database) as session:
            summary = session.execute_write(
                lambda tx: tx.run(statement, rows=rows, workspace_id=workspace_id).consume()
            )
        total["batches"] += 1
        total["nodes_created"] += int(summary.counters.nodes_created or 0)
        total["relationships_created"] += int(summary.counters.relationships_created or 0)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, help="Official graph.json to project through the Bolt driver.")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip writes; verify a neo4j-admin bulk import against --schema-receipt and emit the same receipt shape.",
    )
    parser.add_argument(
        "--schema-receipt",
        type=Path,
        help="Exporter manifest (grbench-import-manifest.json) carrying the content-free schema/topology receipt.",
    )
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--source-id", default="grbench:official")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    password = os.environ.get("NEO4J_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD must be set; it is never written to a receipt")

    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    if args.verify_only:
        if args.schema_receipt is None:
            raise ValueError("--verify-only requires --schema-receipt")
        manifest = json.loads(args.schema_receipt.read_text(encoding="utf-8"))
        schema = manifest["schema"]
        if manifest.get("workspace_id") != args.workspace_id:
            raise RuntimeError(
                "bulk import manifest workspace differs from the requested workspace; "
                "the Text2Cypher runners would query an empty scope"
            )
        graph_name = str(manifest.get("source", {}).get("graph_file", "graph.json"))
        execution_surface = "neo4j_admin_bulk_import"
        empty = {"batches": 0, "nodes_created": 0, "relationships_created": 0, "errors": 0}
        node_summary, relationship_summary = dict(empty), dict(empty)
    else:
        if args.graph is None:
            raise ValueError("--graph is required unless --verify-only is set")
        with args.graph.open(encoding="utf-8") as handle:
            graph = json.load(handle)
        schema = graph_schema_receipt(graph)
        graph_name = args.graph.name
        execution_surface = "direct_neo4j_driver"
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver("bolt://127.0.0.1:7687", auth=("neo4j", password))
    try:
        # The direct baseline owns its own ordinary LPG index.  This is not a
        # SEOCHO feature; it prevents each MERGE/MATCH batch from degenerating
        # into a label scan on the multi-gigabyte official graph.
        with driver.session(database=args.database) as session:
            session.run(
                "CREATE INDEX grbench_node_lookup IF NOT EXISTS "
                "FOR (n:GRBenchNode) ON (n.id, n._workspace_id)"
            ).consume()
        if not args.verify_only:
            node_summary = _write_batches(
                driver, batched(iter_grbench_nodes(graph), args.batch_size), kind="nodes",
                database=args.database, workspace_id=args.workspace_id, source_id=args.source_id,
            )
            relationship_summary = _write_batches(
                driver, batched(iter_grbench_relationships(graph), args.batch_size), kind="relationships",
                database=args.database, workspace_id=args.workspace_id, source_id=args.source_id,
            )
        else:
            # A bulk-imported store populates the lookup index after start-up;
            # the fixed gate queries below must not race that population.
            with driver.session(database=args.database) as session:
                session.run("CALL db.awaitIndexes(600)").consume()
        with driver.session(database=args.database) as session:
            check = [record.data() for record in session.run(
                "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
                "RETURN count(n) AS node_count",
                workspace_id=args.workspace_id,
            )]
            edge_check = [record.data() for record in session.run(
                "MATCH (:GRBenchNode {_workspace_id: $workspace_id})-[r:GRBENCH_REL "
                "{_workspace_id: $workspace_id}]->(:GRBenchNode {_workspace_id: $workspace_id}) "
                "RETURN count(r) AS edge_count",
                workspace_id=args.workspace_id,
            )]
    finally:
        driver.close()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    receipt = {
        "schema_version": "seocho.grbench_lpg_projection_receipt.v1",
        "source": {"graph_file": graph_name, "source_id": args.source_id},
        "workspace_id": args.workspace_id,
        "database": args.database,
        "schema": schema,
        "write": {"nodes": node_summary, "relationships": relationship_summary},
        "verification": {
            "node_count": int(check[0]["node_count"]) if check else 0,
            "edge_count": int(edge_check[0]["edge_count"]) if edge_check else 0,
            "source_counts_match": bool(
                check
                and edge_check
                and int(check[0]["node_count"]) == schema["topology"]["node_count"]
                and int(edge_check[0]["edge_count"]) == schema["topology"]["edge_count"]
            ),
        },
        "time": {"wall_ms": round((time.perf_counter() - started) * 1000, 3)},
        "process": {"max_rss_kib": int(usage.ru_maxrss)},
        "execution_surface": execution_surface,
    }
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verification": receipt["verification"], "wall_ms": receipt["time"]["wall_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
