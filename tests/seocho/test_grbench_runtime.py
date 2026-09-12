from __future__ import annotations

import pytest

from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError, has_anchor_predicate, validate_grbench_read_query


_QUERY = """MATCH (s:GRBenchNode {_workspace_id: $workspace_id, source_node_id: 'a'})
-[r:GRBENCH_REL]->(o:GRBenchNode {_workspace_id: $workspace_id})
RETURN s.source_node_id AS s, r.relation_name AS p, o.source_node_id AS o"""


def test_grbench_query_validator_requires_scope_and_typed_evidence_columns() -> None:
    assert validate_grbench_read_query(_QUERY, row_cap=5).endswith("LIMIT 5")
    with pytest.raises(ValueError, match="workspace"):
        validate_grbench_read_query("MATCH (n:GRBenchNode) RETURN n.id AS s, 'x' AS p, n.id AS o", row_cap=5)
    with pytest.raises(ValueError, match="missing_evidence_columns"):
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

    from seocho.eval.grbench_runtime import ANCHOR_PROPERTIES, GRBENCH_INDEX_STATEMENTS

    src = (Path(__file__).parents[2] / "scripts/benchmarks/load_grbench_dozerdb.py").read_text(encoding="utf-8")
    joined = "\n".join(GRBENCH_INDEX_STATEMENTS)
    for prop in ANCHOR_PROPERTIES + ("relation_name",):   # every anchor property has an index
        assert f"ON (n.{prop})" in joined or f"ON (n.{prop}," in joined or f"ON (r.{prop})" in joined
    assert "CREATE TEXT INDEX grbench_node_name_text" in joined
    assert "GRBENCH_INDEX_STATEMENTS" in src and "CREATE INDEX" not in src   # one contract, no local copy
    assert "CALL db.awaitIndexes(1800)" in src and "SHOW INDEXES" in src
    runtime = (Path(__file__).parents[2] / "src/seocho/eval/grbench_runtime.py").read_text(encoding="utf-8")
    assert "workspace_id=self.workspace_id, timeout=" not in runtime   # never a Cypher parameter again
    assert runtime.count("bolt_query(") == 5   # execute_select, execute_rows, profile_select, _diagnostic_rows + the helper


def test_validator_rejects_unanchored_scans_with_a_repair_code() -> None:
    scope = "{_workspace_id: $workspace_id"
    unanchored = f"MATCH (n:GRBenchNode {scope}}}) RETURN count(n) AS s, 'p' AS p, 'o' AS o"
    with pytest.raises(QueryPolicyError) as exc:
        validate_grbench_read_query(unanchored, row_cap=5)
    assert exc.value.code == "unanchored_scan"
    node_type_only = f"MATCH (n:GRBenchNode {scope}, node_type: 'paper'}}) RETURN n.id AS s, 'p' AS p, 'o' AS o"
    with pytest.raises(QueryPolicyError, match="unanchored_scan"):
        validate_grbench_read_query(node_type_only, row_cap=5)
    inline = f"MATCH (n:GRBenchNode {scope}, feature_name: 'x'}}) RETURN n.id AS s, 'p' AS p, 'o' AS o"
    assert validate_grbench_read_query(inline, row_cap=5).endswith("LIMIT 5")
    where = f"MATCH (n:GRBenchNode {scope}}}) WHERE n.feature_title CONTAINS 'graph' RETURN n.id AS s, 'p' AS p, 'o' AS o"
    assert has_anchor_predicate(where) and validate_grbench_read_query(where, row_cap=5)
    # the guard is a policy, not a syntax rule: it can be switched off for baselines
    assert validate_grbench_read_query(unanchored, row_cap=5, require_anchor=False)
    with pytest.raises(QueryPolicyError, match="missing_workspace_scope"):
        validate_grbench_read_query("MATCH (n:GRBenchNode {feature_name: 'x'}) RETURN n.id AS s, 'p' AS p, 'o' AS o", row_cap=5)


def test_execute_rows_shares_validator_timeout_and_receipt_with_execute_select() -> None:
    executor = GRBenchDozerExecutor(_Driver(), database="neo4j", workspace_id="workspace-a", row_cap=5, require_evidence_columns=False)
    text, receipt = executor.execute_rows(_QUERY)
    assert '"related"' in text  # rows go back to the model...
    assert "related" not in str(receipt)  # ...never into the receipt
    assert receipt["query_timeout_s"] == 15.0 and receipt["rows"] == 1 and receipt["truncated"] is False
    for key in ("validator_ms", "pool_acquire_ms", "bolt_submit_ms", "hydrate_ms", "serialize_ms", "server_available_ms", "query_digest"):
        assert key in receipt


class _ScriptedSession:
    """Main query returns no rows; the diagnostic templates answer from a table."""

    statements: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **params):
        assert type(query).__name__ == "Query" and query.timeout == 15
        assert params["workspace_id"] == "workspace-a"
        type(self).statements.append((query.text, params))
        text = query.text
        if text.startswith("MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: $node_id})"):
            rows = [{"relation": "paper", "degree": 12}]
        elif "n.feature_name = $value" in text and params.get("value") == "Hiroshi Furukawa":
            rows = [{"id": "2300633696", "node_type": "author"}]
        else:
            rows = []
        return _ScriptedResult(rows)


class _ScriptedResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter([_ScriptedRecord(row) for row in self._rows])

    def consume(self):
        return _Summary()


class _ScriptedRecord:
    def __init__(self, row):
        self._row = row

    def data(self):
        return dict(self._row)


class _ScriptedDriver:
    def session(self, **kwargs):
        assert kwargs == {"database": "neo4j", "default_access_mode": "READ"}
        return _ScriptedSession()


_EMPTY_PATTERN = (
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Hiroshi Furukawa'})"
    "-[r:GRBENCH_REL {relation_name: 'author'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) RETURN p.id AS id"
)


def test_execute_rows_zero_row_hint_appends_diagnosis_and_keeps_receipt_content_free() -> None:
    import json

    _ScriptedSession.statements = []
    executor = GRBenchDozerExecutor(
        _ScriptedDriver(), database="neo4j", workspace_id="workspace-a", row_cap=5,
        require_evidence_columns=False, zero_row_hint=True,
    )
    text, receipt = executor.execute_rows(_EMPTY_PATTERN)
    payload = json.loads(text)
    assert payload["rows"] == []
    (anchor,) = payload["diagnostic"]["anchors"]
    assert anchor["match"] == "exact" and anchor["degree_out"] == {"paper": 12} and anchor["degree_class"] == "low"
    assert payload["diagnostic"]["hint"].startswith("Diagnostic: the anchor node(s) exist")
    assert receipt["rows"] == 0 and receipt["zero_row_diagnostic"]["hint_kind"] == "resolved"
    assert receipt["zero_row_diagnostic"]["reads"] == 2 and receipt["zero_row_diagnostic"]["hint_ms"] >= 0
    assert "Furukawa" not in json.dumps(receipt) and "2300633696" not in json.dumps(receipt)
    # main query + exact lookup + degree = 3 Bolt round trips, each with the transaction timeout and workspace scope
    assert len(_ScriptedSession.statements) == 3
    assert all("LIMIT" in stmt for stmt, _ in _ScriptedSession.statements)


def test_execute_rows_without_hint_keeps_the_plain_row_list() -> None:
    import json

    _ScriptedSession.statements = []
    executor = GRBenchDozerExecutor(
        _ScriptedDriver(), database="neo4j", workspace_id="workspace-a", row_cap=5, require_evidence_columns=False,
    )
    text, receipt = executor.execute_rows(_EMPTY_PATTERN)
    assert json.loads(text) == [] and "zero_row_diagnostic" not in receipt
    assert len(_ScriptedSession.statements) == 1
