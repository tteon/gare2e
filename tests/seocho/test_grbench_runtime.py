from __future__ import annotations

import pytest

from seocho.eval.grbench_runtime import GRBenchDozerExecutor, validate_grbench_read_query


_QUERY = """MATCH (s:GRBenchNode {_workspace_id: $workspace_id})
-[r:GRBENCH_REL]->(o:GRBenchNode {_workspace_id: $workspace_id})
RETURN s.source_node_id AS s, r.relation_name AS p, o.source_node_id AS o"""


def test_grbench_query_validator_requires_scope_and_typed_evidence_columns() -> None:
    assert validate_grbench_read_query(_QUERY, row_cap=5).endswith("LIMIT 5")
    with pytest.raises(ValueError, match="workspace"):
        validate_grbench_read_query("MATCH (n:GRBenchNode) RETURN n.id AS s, 'x' AS p, n.id AS o", row_cap=5)
    with pytest.raises(ValueError, match="aliased"):
        validate_grbench_read_query(
            "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) RETURN n",
            row_cap=5,
        )


class _Summary:
    result_available_after = 2
    result_consumed_after = 3
    profile = {
        "operatorType": "ProduceResults",
        "args": {
            "Rows": 1,
            "EstimatedRows": 1,
            "DbHits": 2,
            "PageCacheHits": 3,
            "PageCacheMisses": 1,
        },
        "children": [],
    }


class _Record:
    def data(self):
        return {"s": "a", "p": "related", "o": "b"}


class _Result:
    def __iter__(self):
        return iter((_Record(),))

    def consume(self):
        return _Summary()


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **params):
        # seocho-dp9x: the statement must arrive as neo4j.Query with a server-side
        # transaction timeout; `timeout` must never be a Cypher parameter.
        assert type(query).__name__ == "Query", type(query)
        assert query.timeout == 15
        assert "LIMIT 5" in query.text
        assert params == {"workspace_id": "workspace-a"}
        return _Result()


class _Driver:
    def session(self, **kwargs):
        assert kwargs == {"database": "neo4j", "default_access_mode": "READ"}
        return _Session()


def test_grbench_executor_keeps_values_out_of_receipt() -> None:
    result = GRBenchDozerExecutor(
        _Driver(), database="neo4j", workspace_id="workspace-a", row_cap=5
    ).execute_select(_QUERY)
    assert result.rows == ({"s": "a", "p": "related", "o": "b"},)
    assert result.receipt["rows"] == 1
    assert result.receipt["bolt_iterate_hydrate_ms"] >= 0
    assert result.receipt["evidence_json_encode_ms"] >= 0
    assert "related" not in str(result.receipt)


def test_grbench_profile_replay_is_bounded_and_content_free() -> None:
    receipt = GRBenchDozerExecutor(
        _Driver(), database="neo4j", workspace_id="workspace-a", row_cap=5
    ).profile_select(_QUERY)

    assert receipt["scope"] == "separate_read_only_diagnostic_replay"
    assert receipt["plan"]["total_db_hits"] == 2
    assert receipt["plan"]["total_page_cache_hits"] == 3
    assert receipt["plan"]["total_page_cache_misses"] == 1
    assert "workspace-a" not in str(receipt)


def test_grbench_executor_timeout_is_a_transaction_timeout_not_a_parameter() -> None:
    from seocho.eval.grbench_runtime import bolt_query

    q = bolt_query("MATCH (n) RETURN n", timeout_s=7.5)
    assert type(q).__name__ == "Query" and q.timeout == 7.5 and q.text == "MATCH (n) RETURN n"
    result = GRBenchDozerExecutor(_Driver(), database="neo4j", workspace_id="workspace-a", row_cap=5).execute_select(_QUERY)
    assert result.receipt["query_timeout_s"] == 15.0
    with pytest.raises(ValueError):
        GRBenchDozerExecutor(_Driver(), database="neo4j", workspace_id="w", query_timeout_s=0)


def test_grbench_loader_creates_lookup_indexes_for_text2cypher_predicates() -> None:
    from pathlib import Path

    src = (Path(__file__).parents[2] / "scripts/benchmarks/load_grbench_dozerdb.py").read_text(encoding="utf-8")
    for prop in ("feature_name", "feature_title", "source_node_id", "relation_name"):
        assert f"ON (n.{prop})" in src or f"ON (r.{prop})" in src
    assert "CREATE TEXT INDEX grbench_node_name_text" in src
    assert "CALL db.awaitIndexes(1800)" in src and "SHOW INDEXES" in src
    runtime = (Path(__file__).parents[2] / "src/seocho/eval/grbench_runtime.py").read_text(encoding="utf-8")
    assert "workspace_id=self.workspace_id, timeout=" not in runtime   # never a Cypher parameter again
    assert runtime.count("bolt_query(") == 3
