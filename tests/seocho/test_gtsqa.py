from seocho.eval.gtsqa import (
    canonical_identifier,
    case_from_mapping,
    load_gtsqa_cases,
    profile_gtsqa_gold_graphs,
    render_gtsqa_nquads,
    score_gtsqa_retrieval,
)


def _row() -> dict:
    return {
        "id": 7,
        "question": "Which person wrote X?",
        "sparql_query": "SELECT ?x WHERE {}",
        "seed_entities": ["X (Q1)"],
        "all_answers_wikikg2": ["Q3"],
        "graph": [["X (Q1)", "author (P50)", "Y (Q2)"], ["Y (Q2)", "related (P1)", "Z (Q3)"]],
        "full_answer_subgraph_wikikg2": [["Q1", "P50", "Q2"]],
        "n_hops": 1,
        "graph_isomorphism": "path",
        "test_type": ["in-distribution"],
    }


def test_gtsqa_normalizes_ids_and_scores_gold_edges() -> None:
    case = case_from_mapping(_row())
    assert canonical_identifier("X (Q1)") == "Q1"
    assert case.workspace_id == "gtsqa-case-7"
    score = score_gtsqa_retrieval([{"source": "Q1", "relation": "P50", "target": "Q2"}], case)
    assert score.precision == score.recall == score.f1 == 1.0


def test_gtsqa_gold_score_rejects_non_gold_retrieval_edges() -> None:
    score = score_gtsqa_retrieval(
        [{"source": "Q2", "relation": "P1", "target": "Q3"}], case_from_mapping(_row())
    )
    assert score.precision == score.recall == score.f1 == 0.0


def test_gtsqa_profile_is_based_on_true_gold_edges() -> None:
    report = profile_gtsqa_gold_graphs([case_from_mapping(_row())])
    assert report["gold_subgraph_edge_count"]["mean"] == 1.0
    assert report["candidate_graph_edge_count"]["mean"] == 2.0
    assert report["gold_relation_frequency"] == {"P50": 1}


def test_gtsqa_rdf_export_keeps_each_case_in_its_named_graph() -> None:
    nquads, ontology = render_gtsqa_nquads([case_from_mapping(_row())])
    assert "<urn:seocho:gtsqa:case:7>" in nquads
    assert "<urn:seocho:gtsqa:predicate:P50>" in nquads
    assert "rdf-schema#label" in ontology


def test_load_gtsqa_cases_streams_and_preserves_requested_order(tmp_path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for case_id in (7, 8):
        row = _row()
        row["id"] = case_id
        row["question"] = f"question {case_id}"
        rows.append(row)
    path = tmp_path / "cases.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    cases = load_gtsqa_cases(path, case_ids=[8, 7])
    assert [case.case_id for case in cases] == [8, 7]
