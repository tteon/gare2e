from __future__ import annotations

import csv
import gzip
import importlib.util
import json
from pathlib import Path

import pytest


def _exporter():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "export_grbench_import_csv.py"
    spec = importlib.util.spec_from_file_location("export_grbench_import_csv", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _graph() -> dict:
    return {
        "paper_nodes": {
            "p1": {
                "features": {"title": "A, \"quoted\" title", "year": "1990", "label": ["x", "y"]},
                "neighbors": {"author": ["a1"], "reference": []},
            }
        },
        "author_nodes": {"a1": {"features": {"name": "Ada"}, "neighbors": {"paper": ["p1"]}}},
    }


def test_export_matches_direct_driver_projection_and_manifest(tmp_path) -> None:
    module = _exporter()
    manifest = module.export_graph(
        graph=_graph(), output_dir=tmp_path, workspace_id="grbench-test", source_id="grbench:official", graph_name="graph.json"
    )
    with gzip.open(tmp_path / "grbench-nodes.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        nodes = list(csv.DictReader(handle))
    with gzip.open(tmp_path / "grbench-rels.csv.gz", "rt", encoding="utf-8", newline="") as handle:
        rels = list(csv.DictReader(handle))

    assert [n["id:ID(GRBenchNode)"] for n in nodes] == ["p1", "a1"]
    paper = nodes[0]
    assert paper[":LABEL"] == "GRBenchNode"
    assert paper["node_type"] == "paper"
    assert paper["feature_title"] == 'A, "quoted" title'
    assert paper["feature_label"] == json.dumps(["x", "y"])
    assert paper["feature_name"] == ""
    assert paper["_workspace_id"] == "grbench-test"
    assert {(r[":START_ID(GRBenchNode)"], r[":END_ID(GRBenchNode)"], r["relation_name"]) for r in rels} == {
        ("p1", "a1", "author"),
        ("a1", "p1", "paper"),
    }
    assert all(r[":TYPE"] == "GRBENCH_REL" for r in rels)
    assert manifest["files"]["nodes"]["rows"] == manifest["schema"]["topology"]["node_count"] == 2
    assert manifest["files"]["relationships"]["rows"] == manifest["schema"]["topology"]["edge_count"] == 2
    assert manifest["typing"]["non_string_scalar_values_in_source"] == 0
    written = json.loads((tmp_path / "grbench-import-manifest.json").read_text(encoding="utf-8"))
    assert written["files"]["nodes"]["sha256"] == manifest["files"]["nodes"]["sha256"]


def test_export_refuses_to_overwrite(tmp_path) -> None:
    module = _exporter()
    module.export_graph(graph=_graph(), output_dir=tmp_path, workspace_id="w", source_id="s", graph_name="g")
    with pytest.raises(FileExistsError):
        module.export_graph(graph=_graph(), output_dir=tmp_path, workspace_id="w", source_id="s", graph_name="g")
