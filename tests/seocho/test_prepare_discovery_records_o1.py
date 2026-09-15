"""O1 (ontology-enriched) discovery responses: data-derived, offline, leak-safe."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from seocho.eval.gtsqa import GTSQACase, GTSQAEdge

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("prep", ROOT / "scripts" / "benchmarks" / "prepare_agentic_os_discovery_records.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _case() -> GTSQACase:
    rows = [
        ["Alice (Q1)", "instance of (P31)", "human (Q5)"],
        ["Alice (Q1)", "employer (P108)", "Acme (Q2)"],
        ["Acme (Q2)", "instance of (P31)", "business (Q4830453)"],
        ["Bob (Q3)", "instance of (P31)", "human (Q5)"],
        ["Bob (Q3)", "employer (P108)", "Acme (Q2)"],
        ["Alice (Q1)", "date of birth (P569)", "1970-01-01"],
        ["Secret (Q9)", "instance of (P31)", "human (Q5)"],
    ]
    edges = tuple(GTSQAEdge.from_row(r) for r in rows)
    return GTSQACase(case_id=1, question="q", sparql_query="SELECT", seed_entities=("Q1",), answer_ids=("Q9",), graph_edges=edges, gold_edges=edges[:1], n_hops=1, graph_isomorphism="", test_types=())


def test_gtsqa_o1_adds_domain_range_and_object_kind_from_the_case_graph():
    o1 = _mod()._gtsqa_ontology(_case())
    rel = {r["id"]: r for r in o1["relations"]}
    assert o1["context_kind"] == "ontology_enriched_relation_vocabulary"
    assert rel["P108"]["domain"] == ["human"] and rel["P108"]["range"] == ["business"]
    assert rel["P108"]["object_kind"] == "entity" and rel["P108"]["edge_count"] == 2
    assert rel["P569"]["object_kind"] == "literal" and rel["P569"]["range"] == []
    assert "preferred" not in rel["P108"]  # compact: the S1 duplicate label is dropped
    # answers / seeds never appear as class labels
    text = str(o1)
    assert "Q9" not in text and "Secret" not in text


def test_grbench_o1_states_direction_and_property_roles():
    mod = _mod()
    schema = {"nodes": [], "relationships": [], "query_scope": "x"}
    o1 = mod._grbench_ontology(schema, {**schema, "taxonomy_hints": []})
    rels = {r["relation_name"]: r for r in o1["relations"]}
    assert rels["author"]["from"] == "paper" and rels["author"]["to"] == "author" and rels["author"]["inverse"] == "paper"
    assert rels["reference"]["inverse"] == "cited_by" and rels["cited_by"]["from"] == "paper"
    assert o1["property_semantics"]["feature_title"].startswith("paper title")
    assert any("_workspace_id" in idiom for idiom in o1["query_idioms"])
