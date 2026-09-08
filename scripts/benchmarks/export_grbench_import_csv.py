#!/usr/bin/env python3
"""Export one official GRBench ``graph.json`` as ``neo4j-admin database import`` CSV.

The co-located Vast appliance previously projected the graph through the Bolt
driver at run time.  For MAPLE/Physics (2.76M nodes, 67.3M relationships) that
took longer than the paid remote budget (incident V13), so the projection is
now materialised once, locally, as gzip CSV that the appliance bulk-imports
offline before DozerDB starts.  Rows carry exactly the properties
``iter_grbench_nodes``/``iter_grbench_relationships`` would have written, so the
runtime schema is unchanged; only the load surface differs.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from seocho.eval.grbench import graph_schema_receipt, iter_grbench_nodes, iter_grbench_relationships

MANIFEST_SCHEMA_VERSION = "seocho.grbench_import_csv_manifest.v1"
NODES_FILE = "grbench-nodes.csv.gz"
RELATIONSHIPS_FILE = "grbench-rels.csv.gz"
MANIFEST_FILE = "grbench-import-manifest.json"
NODE_LABEL = "GRBenchNode"
RELATIONSHIP_TYPE = "GRBENCH_REL"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cell(value: Any) -> str:
    """Render one property value the way the direct-driver loader stored it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def node_header(feature_names: list[str]) -> list[str]:
    return [f"id:ID({NODE_LABEL})", ":LABEL", "node_type", "source_node_id", *feature_names, "_workspace_id", "_source_id"]


def relationship_header() -> list[str]:
    return [
        f":START_ID({NODE_LABEL})", f":END_ID({NODE_LABEL})", ":TYPE",
        "relation_name", "source_node_type", "_workspace_id", "_source_id",
    ]


def export_graph(
    *, graph: dict[str, Any], output_dir: Path, workspace_id: str, source_id: str, graph_name: str
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = output_dir / NODES_FILE
    rels_path = output_dir / RELATIONSHIPS_FILE
    for existing in (nodes_path, rels_path):
        if existing.exists():
            raise FileExistsError(f"refusing to overwrite export: {existing}")
    schema = graph_schema_receipt(graph)
    feature_names = sorted(schema["topology"]["feature_names"])
    started = time.perf_counter()
    node_rows = 0
    non_string_scalars = 0
    with gzip.open(nodes_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(node_header(feature_names))
        for item in iter_grbench_nodes(graph):
            props = item["properties"]
            for name in feature_names:
                value = props.get(name)
                if isinstance(value, (int, float, bool)) and not isinstance(value, str):
                    non_string_scalars += 1
            writer.writerow(
                [
                    item["id"], NODE_LABEL, props["node_type"], props["source_node_id"],
                    *(_cell(props.get(name)) for name in feature_names),
                    workspace_id, source_id,
                ]
            )
            node_rows += 1
    relationship_rows = 0
    with gzip.open(rels_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(relationship_header())
        for item in iter_grbench_relationships(graph):
            props = item["properties"]
            writer.writerow(
                [
                    item["source"], item["target"], RELATIONSHIP_TYPE,
                    props["relation_name"], props["source_node_type"], workspace_id, source_id,
                ]
            )
            relationship_rows += 1
    if node_rows != schema["topology"]["node_count"] or relationship_rows != schema["topology"]["edge_count"]:
        raise RuntimeError("exported row counts diverge from the graph schema receipt")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": {"graph_file": graph_name, "source_id": source_id},
        "workspace_id": workspace_id,
        "schema": schema,
        "files": {
            "nodes": {"name": NODES_FILE, "rows": node_rows, "bytes": nodes_path.stat().st_size, "sha256": _sha256(nodes_path)},
            "relationships": {
                "name": RELATIONSHIPS_FILE, "rows": relationship_rows,
                "bytes": rels_path.stat().st_size, "sha256": _sha256(rels_path),
            },
        },
        "columns": {"nodes": node_header(feature_names), "relationships": relationship_header()},
        "typing": {
            "all_properties_imported_as_strings": True,
            "non_string_scalar_values_in_source": non_string_scalars,
        },
        "import_command_hint": (
            f"neo4j-admin database import full neo4j --overwrite-destination=true "
            f"--multiline-fields=true --nodes={NODES_FILE} --relationships={RELATIONSHIPS_FILE}"
        ),
        "time": {"wall_ms": round((time.perf_counter() - started) * 1000, 3)},
    }
    (output_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workspace-id", default="grbench-maple-physics")
    parser.add_argument("--source-id", default="grbench:official")
    args = parser.parse_args()
    with args.graph.open(encoding="utf-8") as handle:
        graph = json.load(handle)
    manifest = export_graph(
        graph=graph, output_dir=args.output_dir, workspace_id=args.workspace_id,
        source_id=args.source_id, graph_name=args.graph.name,
    )
    print(json.dumps({"files": {k: {"rows": v["rows"], "bytes": v["bytes"]} for k, v in manifest["files"].items()}, "wall_ms": manifest["time"]["wall_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
