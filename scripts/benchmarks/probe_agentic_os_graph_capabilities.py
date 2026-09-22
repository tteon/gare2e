#!/usr/bin/env python3
"""Read-only DB capability probe BEFORE warmup, not during throughput measurement.

Credentials come from NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD. No query payloads,
connection addresses, exception messages or arbitrary settings are exported.
Unsupported/denied procedures remain explicit gaps. This never installs plugins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


SETTINGS = (
    "server.bolt.thread_pool_min_size", "server.bolt.thread_pool_max_size",
    "server.cypher.parallel.worker_limit", "server.memory.pagecache.size",
    "server.memory.heap.initial_size", "server.memory.heap.max_size",
    "dbms.memory.transaction.total.max",
)
PROCEDURES = ("apoc.path.expandConfig", "apoc.path.subgraphNodes", "gds.version",
              "gds.graph.project", "gds.graph.list", "dbms.queryJmx")


def probe(driver: Any, database: str) -> dict[str, Any]:
    from neo4j import Query

    result: dict[str, Any] = {
        "schema_version": "agentic_os.graph_capabilities.v1", "timestamp_unix_s": time.time(),
        "scope": "preflight_read_only_not_timed_workload", "store_format": None,
        "store_format_reason": "Requires observed store metadata; db.format config is not proof of existing format",
        "actual_query_runtime": None,
        "runtime_reason": "Requires a representative execution plan; never infer Enterprise features from DozerDB name",
    }
    queries = {
        "components": ("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition", {}),
        "procedures": ("SHOW PROCEDURES YIELD name, mode WHERE name IN $names RETURN name, mode", {"names": list(PROCEDURES)}),
        "settings": ("SHOW SETTINGS YIELD name, value WHERE name IN $names RETURN name, value", {"names": list(SETTINGS)}),
    }
    for key, (query, parameters) in queries.items():
        started = time.monotonic()
        try:
            with driver.session(database=database, default_access_mode="READ") as session:
                rows = session.run(Query(query, timeout=10.0), parameters).data()
            result[key] = {"status": "observed", "rows": rows}
        except Exception as exc:
            result[key] = {"status": "unavailable", "rows": None, "error_class": type(exc).__name__}
        result[key]["probe_ms"] = (time.monotonic() - started) * 1000
    return result


def probe_environment(*, database: str = "neo4j") -> dict[str, Any]:
    from neo4j import GraphDatabase

    if not os.environ.get("NEO4J_PASSWORD"):
        return {"schema_version": "agentic_os.graph_capabilities.v1", "status": "not_collected",
                "reason": "NEO4J_PASSWORD_unavailable"}
    with GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
            connection_timeout=10, connection_acquisition_timeout=10,
            max_transaction_retry_time=0) as driver:
        return probe(driver, database)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    # Exclusive creation prevents overwriting historical evidence, including on failure.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        result = probe_environment(database=args.database)
        result.update({"workspace_id": args.workspace_id, "run_id": args.run_id,
                       "database_digest": hashlib.sha256(args.database.encode()).hexdigest()})
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
