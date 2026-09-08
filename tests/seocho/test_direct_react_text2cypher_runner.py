from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


def _runner():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_direct_react_text2cypher.py"
    spec = importlib.util.spec_from_file_location("direct_react_text2cypher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workspace_scope_is_required_for_every_node_pattern() -> None:
    runner = _runner()
    scoped = (
        "MATCH (a:GRBenchNode {_workspace_id: $workspace_id})-[:GRBENCH_REL]->"
        "(b:GRBenchNode {_workspace_id: $workspace_id}) RETURN b"
    )
    assert runner._safe_read_query(scoped, 5).endswith("LIMIT 5")
    with pytest.raises(runner.QueryValidationError) as error:
        runner._safe_read_query(
            "MATCH (a:GRBenchNode {_workspace_id: $workspace_id})-[:GRBENCH_REL]->"
            "(b:GRBenchNode) RETURN b",
            5,
        )
    assert error.value.code == "missing_workspace_scope"


def test_frozen_discovery_cache_selects_requested_arm(tmp_path) -> None:
    runner = _runner()
    cache = {
        "schema_version": "seocho.agentic_os.discovery_cache.v1",
        "cache_digest": "d" * 64,
        "cases": [
            {"qid": "7", "responses": {"B0": {"kind": "base"}, "C-": {"kind": "amber"}, "S1": {"kind": "semantic"}}}
        ],
    }
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    responses, digest = runner._load_discovery_cache(path, "S1")
    assert json.loads(responses["7"]) == {"kind": "semantic"}
    assert digest == "d" * 64


def test_usage_receipt_reads_standard_nested_reasoning_tokens() -> None:
    runner = _runner()
    receipt = runner._usage_receipt(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        },
        1.5,
    )
    assert receipt["thinking_tokens"] == 3


def test_textual_tool_markup_is_not_accepted_as_a_final_answer() -> None:
    runner = _runner()
    assert runner.contains_unparsed_tool_markup(
        '<tool_call><function=inspect_graph_schema>{}</function></tool_call>'
    )
    assert not runner.contains_unparsed_tool_markup("A concise evidence-grounded answer")


class _Summary:
    result_available_after = 1
    result_consumed_after = 2


class _Result:
    def __iter__(self):
        return iter(())

    def consume(self):
        return _Summary()


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def run(self, statement, **parameters):
        assert parameters["workspace_id"] == "grbench:maple:physics"
        return _Result()


class _Driver:
    def session(self, **_):
        return _Session()

    def close(self):
        return None


@pytest.mark.parametrize(
    ("condition", "expected_tools", "expected_class"),
    [
        ("closed_book", 0, "behavioral_baseline"),
        ("tool_auto", 2, "behavioral_baseline"),
        ("forced_plumbing", 2, "plumbing_smoke"),
    ],
)
def test_raw_grbench_conditions_complete_deterministic_e2e(
    tmp_path, monkeypatch, condition, expected_tools, expected_class
) -> None:
    runner = _runner()
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps({"qid": "7", "question": "q", "answer": "a"}) + "\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    predictions = tmp_path / "predictions.json"
    calls = 0

    def fake_post(_url, payload):
        nonlocal calls
        calls += 1
        if condition == "closed_book":
            assert "tools" not in payload
            message = {"content": "a"}
        elif calls == 1:
            descriptions = [tool["function"]["description"] for tool in payload["tools"]]
            assert "Do not call for greetings" in descriptions[0]
            assert "only after inspect_graph_schema" in descriptions[1]
            assert "never repeat an identical failed query" in descriptions[1]
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "schema",
                        "function": {"name": "inspect_graph_schema", "arguments": "{}"},
                    }
                ],
            }
        elif calls == 2:
            query = (
                "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
                "RETURN n.id AS s, n.id AS p, n.id AS o LIMIT 1"
            )
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "query",
                        "function": {"name": "execute_cypher", "arguments": json.dumps({"query": query})},
                    }
                ],
            }
        else:
            message = {"content": "a"}
        return {
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }, 1.0

    monkeypatch.setattr(runner, "_post", fake_post)
    monkeypatch.setattr(runner.GraphDatabase, "driver", lambda *_args, **_kwargs: _Driver())
    monkeypatch.setenv("NEO4J_PASSWORD", "test-only")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_direct_react_text2cypher.py",
            "--questions",
            str(questions),
            "--base-url",
            "http://unused",
            "--model",
            "test-model",
            "--receipt-out",
            str(receipt),
            "--predictions-out",
            str(predictions),
            "--condition",
            condition,
            "--stage",
            "smoke",
            "--limit",
            "1",
        ],
    )
    assert runner.main() == 0
    case = json.loads(receipt.read_text(encoding="utf-8"))["cases"][0]
    assert case["condition"] == condition
    assert case["evidence_class"] == expected_class
    assert len(case["tools"]) == expected_tools
    assert case["success"] is (condition == "closed_book")
    assert case["answer_status"] == (
        "direct_answer" if condition == "closed_book" else "unsupported_answer"
    )


def test_shared_token_budget_discards_over_budget_answer(tmp_path, monkeypatch) -> None:
    runner = _runner()
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        json.dumps({"qid": "7", "question": "q", "answer": "a"}) + "\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    predictions = tmp_path / "predictions.json"

    monkeypatch.setattr(
        runner,
        "_post",
        lambda _url, _payload: (
            {
                "choices": [{"message": {"content": "must not be accepted"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2},
            },
            1.0,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_direct_react_text2cypher.py",
            "--questions",
            str(questions),
            "--base-url",
            "http://unused",
            "--model",
            "test-model",
            "--receipt-out",
            str(receipt),
            "--predictions-out",
            str(predictions),
            "--condition",
            "closed_book",
            "--stage",
            "smoke",
            "--token-budget",
            "10",
            "--limit",
            "1",
        ],
    )

    assert runner.main() == 0
    case = json.loads(receipt.read_text(encoding="utf-8"))["cases"][0]
    assert case["terminal"] == "budget_exceeded"
    assert case["answer_status"] == "empty"
    assert case["success"] is False
    assert case["budget"]["exceeded"] is True
    assert json.loads(predictions.read_text(encoding="utf-8"))["7"]["prediction"] == ""
