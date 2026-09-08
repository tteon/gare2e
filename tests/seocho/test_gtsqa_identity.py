from seocho.eval.gtsqa_identity import analyze_gtsqa_identity


def test_identity_analyzer_reports_colliding_surface_forms(tmp_path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist([{
        "id": 1,
        "graph": [
            ["Mercury (Q1)", "orbits (P1)", "Sun (Q9)"],
            ["Mercury (Q2)", "element of (P2)", "Table (Q8)"],
        ],
    }])
    path = tmp_path / "fixture.parquet"
    pq.write_table(table, path)
    report = analyze_gtsqa_identity(path)
    assert report["entity_surface_collision"]["surface_count"] == 1
    assert report["entity_surface_collision"]["relation_signature_jaccard_lt_0_1_rate"] == 1.0
