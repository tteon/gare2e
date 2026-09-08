from __future__ import annotations

from seocho.eval.grbench import graph_projection_rows, graph_schema_receipt, safe_property_name


def _graph() -> dict:
    return {
        "book_nodes": {
            "book:1": {
                "features": {"title": "Example", "publication year": 2024},
                "neighbors": {"author": ["author:1"]},
            }
        },
        "author_nodes": {
            "author:1": {"features": {"name": "Ada"}, "neighbors": {"book": ["book:1"]}}
        },
    }


def test_grbench_projection_keeps_original_type_and_relation_as_properties() -> None:
    nodes, relationships = graph_projection_rows(_graph())
    assert nodes[0]["label"] == "GRBenchNode"
    assert nodes[0]["properties"]["node_type"] == "book"
    assert nodes[0]["properties"]["feature_publication_year"] == 2024
    assert relationships[0]["type"] == "GRBENCH_REL"
    assert relationships[0]["properties"]["relation_name"] == "author"


def test_grbench_schema_receipt_is_content_free_and_deterministic() -> None:
    receipt = graph_schema_receipt(_graph())
    assert receipt["topology"]["node_count"] == 2
    assert receipt["topology"]["edge_count"] == 2
    assert receipt["topology"]["node_types"] == {"author": 1, "book": 1}
    assert "Example" not in str(receipt)
    assert len(receipt["schema_digest"]) == 64
    assert safe_property_name("Publication year") == "feature_publication_year"
