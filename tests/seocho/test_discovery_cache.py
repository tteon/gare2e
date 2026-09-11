from __future__ import annotations

import re

import pytest

from seocho.eval.discovery_cache import (
    CohortCase,
    build_cohort_manifest,
    build_discovery_cache,
    build_paired_schedule,
    validate_discovery_cache,
)


class _WordTokenizer:
    def count(self, text: str) -> int:
        return len(re.findall(r"[A-Za-z0-9_-]+", text))


class _CountingWordTokenizer(_WordTokenizer):
    def __init__(self) -> None:
        self.calls = 0

    def count(self, text: str) -> int:
        self.calls += 1
        return super().count(text)


def _records() -> list[dict[str, object]]:
    return [
        {
            "qid": "q1",
            "b0_response": {"labels": ["GRBenchNode"], "relationship_property": "relation_name"},
            "s1_response": {"labels": ["ScientificPaper"], "relationship_property": "cites"},
            "forbidden_terms": ["ScientificPaper", "cites", "answer-secret"],
        }
    ]


def test_discovery_cache_matches_c_negative_tokens_and_revalidates() -> None:
    tokenizer = _WordTokenizer()
    cache = build_discovery_cache(
        records=_records(),
        graph_digest="graph-digest",
        taxonomy_digest="taxonomy-digest",
        tool_schema={"name": "inspect_graph_schema"},
        token_counter=tokenizer,
    )
    case = cache["cases"][0]
    assert case["tokens"]["C-"] == case["tokens"]["S1"]
    assert cache["contract"]["tool_availability"] == ["B0", "C-", "S1"]
    validate_discovery_cache(cache, token_counter=tokenizer)
    broken = {**cache, "cases": [{**case, "tokens": {**case["tokens"], "S1": 999}}]}
    with pytest.raises(ValueError, match="tokenizer drift"):
        validate_discovery_cache(broken, token_counter=tokenizer)


def test_discovery_cache_can_revalidate_only_the_active_attempt() -> None:
    tokenizer = _WordTokenizer()
    records = _records() + [
        {
            "qid": "43",
            "b0_response": {"status": "available"},
            "s1_response": {"labels": ["Book"], "relationship_property": "written_by"},
        }
    ]
    cache = build_discovery_cache(
        records=records,
        graph_digest="graph-digest",
        tool_schema={"name": "inspect_graph_schema"},
        token_counter=tokenizer,
    )
    scoped_tokenizer = _CountingWordTokenizer()
    validate_discovery_cache(cache, token_counter=scoped_tokenizer, qids={"q1"})
    assert scoped_tokenizer.calls == 3
    with pytest.raises(ValueError, match="selected qids"):
        validate_discovery_cache(cache, token_counter=tokenizer, qids={"missing"})


def test_negative_control_uses_bounded_tokenizer_calls() -> None:
    tokenizer = _CountingWordTokenizer()
    build_discovery_cache(
        records=_records(),
        graph_digest="graph-digest",
        tool_schema={"name": "inspect_graph_schema"},
        token_counter=tokenizer,
    )
    # Includes negative-control construction, three final arm counts, and no
    # post-build validation.  A linear 512-step search would exceed this cap.
    assert tokenizer.calls < 20


def test_negative_control_rejects_forbidden_term_leak() -> None:
    rows = _records()
    rows[0]["forbidden_terms"] = ["amber"]
    with pytest.raises(ValueError, match="forbidden"):
        build_discovery_cache(
            records=rows,
            graph_digest="g",
            tool_schema={"name": "inspect"},
            token_counter=_WordTokenizer(),
        )


def test_paired_schedule_covers_all_arms_per_unit_deterministically() -> None:
    cohort = build_cohort_manifest(
        dataset="GRBench",
        cases=[CohortCase("q1", "physics"), CohortCase("q2", "physics")],
    )
    first = build_paired_schedule(cohort=cohort, arms=["B0", "C-", "S1"], seeds=[7], repetitions=2)
    second = build_paired_schedule(cohort=cohort, arms=["B0", "C-", "S1"], seeds=[7], repetitions=2)
    assert first == second
    assert len(first) == 2 * 1 * 2 * 2 * 3
    unit = [row for row in first if row["paired_unit"] == "q1:7:cold:0"]
    assert {row["arm"] for row in unit} == {"B0", "C-", "S1"}
    assert [row["paired_ordinal"] for row in unit] == [0, 1, 2]


def test_optional_o1_arm_is_frozen_and_validated_when_every_record_carries_it() -> None:
    tokenizer = _WordTokenizer()
    records = [{**_records()[0], "o1_response": {"labels": ["ScientificPaper"], "relationship_property": "cites", "semantics": {"cites": "source paper cites target paper"}}}]
    cache = build_discovery_cache(records=records, graph_digest="g", tool_schema={"name": "inspect_graph_schema"}, token_counter=tokenizer)
    assert cache["contract"]["tool_availability"] == ["B0", "C-", "S1", "O1"]
    case = cache["cases"][0]
    assert set(case["responses"]) == {"B0", "C-", "S1", "O1"}
    assert case["tokens"]["O1"] > case["tokens"]["S1"]
    validate_discovery_cache(cache, token_counter=tokenizer)
    # a cache without the optional arm still validates against the base contract
    base = build_discovery_cache(records=_records(), graph_digest="g", tool_schema={"name": "inspect_graph_schema"}, token_counter=tokenizer)
    assert base["contract"]["tool_availability"] == ["B0", "C-", "S1"]
    validate_discovery_cache(base, token_counter=tokenizer)


def test_optional_arm_must_be_present_on_every_record() -> None:
    tokenizer = _WordTokenizer()
    records = [
        {**_records()[0], "o1_response": {"x": 1}},
        {**_records()[0], "qid": "q2"},
    ]
    with pytest.raises(ValueError, match="every record or none"):
        build_discovery_cache(records=records, graph_digest="g", tool_schema={"name": "inspect_graph_schema"}, token_counter=tokenizer)
