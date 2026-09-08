from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).parents[2] / "scripts" / "benchmarks" / "judge_agentic_os_answers.py"
_SPEC = importlib.util.spec_from_file_location("judge_agentic_os_answers", _SCRIPT)
assert _SPEC and _SPEC.loader
JUDGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(JUDGE)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_parse_judgement_requires_consistent_discrete_score() -> None:
    assert JUDGE.parse_judgement('prefix {"verdict":"partial","answer_score":0.5}') == (
        "partial",
        0.5,
    )
    with pytest.raises(ValueError, match="inconsistent"):
        JUDGE.parse_judgement('{"verdict":"correct","answer_score":0.5}')


def test_collect_grbench_case_preserves_exact_correlation(tmp_path: Path) -> None:
    root = tmp_path / "cell"
    attempt = root / "attempts" / "000000"
    correlation = {
        "run_id": "run-1",
        "case_id": "7",
        "arm": "B0",
        "seed": 3,
        "repetition": 0,
        "cache_phase": "warm",
        "role": "single_agent",
    }
    _write(attempt / "manifest.json", {"lane": "GRBench-Text2Cypher"})
    _write(attempt / "run-receipt.json", {"cases": [correlation]})
    _write(attempt / "predictions.private.json", {"7": {"prediction": "A", "answer": "B"}})
    dataset = tmp_path / "gr.jsonl"
    _write(dataset, {"qid": "7", "question": "Q?", "answer": "B"})

    rows = JUDGE.collect_cases(
        [root],
        grbench_data=dataset,
        gtsqa_parquet=tmp_path / "unused.parquet",
    )

    assert rows == [{**correlation, "question": "Q?", "gold_answer": "B", "prediction": "A"}]
