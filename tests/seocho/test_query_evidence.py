from __future__ import annotations

import json
from pathlib import Path

import pytest

from seocho.eval.grbench_runtime import GRBenchDozerExecutor
from seocho.eval.query_evidence import QueryEvidence

QUERY = "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: $id}) RETURN n.id"


class Result:
    result_available_after = 1
    result_consumed_after = 2

    def __iter__(self):
        yield self

    def data(self):
        return {"id": "private-value"}

    def consume(self):
        return self


class Driver:
    def __init__(self):
        self.calls = []

    def session(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, query, **params):
        self.calls.append(params)
        return Result()


def test_reuse_is_session_parameter_scoped_and_logs_failures(tmp_path: Path) -> None:
    observer = QueryEvidence(
        tmp_path / "queries.jsonl",
        run_id="run",
        snapshot_id="immutable",
        mode="exact_result_reuse",
    )
    observer.session("q1")
    observer.tool("tool1", 1)
    driver = Driver()
    executor = GRBenchDozerExecutor(
        driver,
        database="neo4j",
        workspace_id="a",
        require_evidence_columns=False,
        query_evidence=observer,
    )
    a = executor.execute_rows(QUERY, {"id": "x", "workspace_id": "wrong"})
    b = executor.execute_rows(QUERY, {"id": "x"})
    assert a[0] == b[0] and b[1]["reused_from"] == a[1]["query_id"]
    assert b[1]["db_queries_executed"] == 0 and len(driver.calls) == 1
    assert driver.calls[0]["workspace_id"] == "a"
    executor.execute_rows(QUERY, {"id": "y"})
    observer.session("q2")
    executor.execute_rows(QUERY, {"id": "x"})
    assert len(driver.calls) == 3
    with pytest.raises(ValueError):
        executor.execute_rows("DELETE n")
    logs = [json.loads(line) for line in observer.path.read_text().splitlines()]
    assert logs[-1]["outcome"] == "error" and logs[-1]["db_queries_executed"] == 0
    assert "private-value" not in observer.path.read_text()
    assert len({q["query_id"] for q in logs}) == 5
    assert observer.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "row_cap,byte_cap,query",
    [(1, 12000, QUERY), (20, 1, QUERY), (20, 12000, QUERY + " + timestamp()")],
)
def test_capped_or_volatile_results_are_not_reused(
    tmp_path: Path, row_cap: int, byte_cap: int, query: str
) -> None:
    observer = QueryEvidence(
        tmp_path / "log", run_id="run", snapshot_id="frozen", mode="exact_result_reuse"
    )
    observer.session("q")
    driver = Driver()
    executor = GRBenchDozerExecutor(
        driver,
        database="neo4j",
        workspace_id="a",
        row_cap=row_cap,
        byte_cap=byte_cap,
        require_evidence_columns=False,
        query_evidence=observer,
    )
    executor.execute_rows(query, {"id": "x"})
    executor.execute_rows(query, {"id": "x"})
    assert len(driver.calls) == 2


def test_reuse_requires_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        QueryEvidence(
            tmp_path / "x", run_id="r", snapshot_id="", mode="exact_result_reuse"
        )
