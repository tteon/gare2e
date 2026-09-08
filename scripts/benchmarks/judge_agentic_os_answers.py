#!/usr/bin/env python3
"""Blindly score private AgenticOS answers with an OpenAI-compatible judge.

The script reads ignored private prediction artifacts and writes only
content-free, exactly correlated score rows.  Current GRBench/Graph-CoT and
GTSQA sources do not provide gold prose rationales, so rationale and AR scores
remain null by contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from openai import OpenAI

from seocho.eval.gtsqa import load_gtsqa_cases


PROMPT_ID = "agentic_os.answer_accuracy.v1"
CORRELATION_FIELDS = (
    "run_id",
    "case_id",
    "arm",
    "seed",
    "repetition",
    "cache_phase",
    "role",
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_ABSTENTION = re.compile(r"\b(abstain|cannot answer|insufficient evidence|do not know)\b", re.IGNORECASE)
_SYSTEM = """You are a strict, independent answer evaluator.
You do not know the generator, experimental arm, or retrieval topology.
Compare the candidate with the reference for semantic correctness, not wording.
Use score 1 only when the answer is correct and complete, 0.5 when it contains a
correct but materially incomplete subset, and 0 when it is wrong, unsupported,
or abstains despite an answerable reference. Ignore prose reasoning except where
it changes or contradicts the final answer. Return JSON only:
{"verdict":"correct|partial|incorrect","answer_score":0|0.5|1}
"""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_receipt(attempt: Path) -> Mapping[str, Any]:
    direct = attempt / "run-receipt.json"
    if direct.is_file():
        payload = _load_json(direct)
        cases = payload.get("cases", ()) if isinstance(payload, Mapping) else ()
        if len(cases) != 1 or not isinstance(cases[0], Mapping):
            raise ValueError(f"expected one case receipt: {direct}")
        return cases[0]
    rows = (attempt / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in rows if line.strip()]
    if len(parsed) != 1 or not isinstance(parsed[0], Mapping):
        raise ValueError(f"expected one JSONL receipt: {attempt}")
    return parsed[0]


def _read_prediction(attempt: Path, case_id: str) -> tuple[str, str | None]:
    direct = attempt / "predictions.private.json"
    if direct.is_file():
        payload = _load_json(direct)
        row = payload.get(case_id) if isinstance(payload, Mapping) else None
        if not isinstance(row, Mapping):
            raise ValueError(f"missing private prediction for {case_id}: {direct}")
        return str(row.get("prediction") or ""), str(row["answer"]) if row.get("answer") is not None else None
    path = attempt / "predictions.private.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("case_id")) == case_id:
            return str(row.get("answer") or ""), str(row["gold_answer"]) if row.get("gold_answer") is not None else None
    raise ValueError(f"missing private prediction for {case_id}: {path}")


def _manifest_lane(attempt: Path) -> str:
    payload = _load_json(attempt / "manifest.json")
    return str(payload.get("lane") or "")


def _grbench_questions(path: Path) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[str(row["qid"])] = (str(row["question"]), str(row["answer"]))
    return rows


def _attempt_dirs(roots: Iterable[Path]) -> list[Path]:
    return sorted({path.parent for root in roots for path in root.glob("attempts/*/manifest.json")})


def collect_cases(
    roots: Iterable[Path],
    *,
    grbench_data: Path,
    gtsqa_parquet: Path,
) -> list[dict[str, Any]]:
    """Join private predictions to questions/gold without exposing arm to the judge."""
    attempts = _attempt_dirs(roots)
    grbench = _grbench_questions(grbench_data)
    staged: list[tuple[Path, Mapping[str, Any], str, str, str | None]] = []
    gts_ids: set[int] = set()
    for attempt in attempts:
        receipt = _read_receipt(attempt)
        case_id = str(receipt.get("case_id") or receipt.get("qid"))
        prediction, private_gold = _read_prediction(attempt, case_id)
        lane = _manifest_lane(attempt)
        staged.append((attempt, receipt, lane, prediction, private_gold))
        if "GTSQA" in lane.upper():
            gts_ids.add(int(case_id))
    gtsqa = {
        str(case.case_id): (case.question, json.dumps(list(case.answer_ids), ensure_ascii=False))
        for case in load_gtsqa_cases(gtsqa_parquet, case_ids=sorted(gts_ids))
    } if gts_ids else {}

    rows: list[dict[str, Any]] = []
    for _attempt, receipt, lane, prediction, private_gold in staged:
        case_id = str(receipt.get("case_id") or receipt.get("qid"))
        if "GTSQA" in lane.upper():
            question, source_gold = gtsqa[case_id]
        else:
            question, source_gold = grbench[case_id]
        gold = private_gold if private_gold is not None else source_gold
        correlation = {field: receipt.get(field) for field in CORRELATION_FIELDS}
        if any(value is None for value in correlation.values()):
            raise ValueError(f"incomplete correlation for {case_id}")
        rows.append({**correlation, "question": question, "gold_answer": gold, "prediction": prediction})
    return rows


def parse_judgement(text: str) -> tuple[str, float]:
    match = _JSON_OBJECT.search(text)
    if not match:
        raise ValueError("judge returned no JSON object")
    payload = json.loads(match.group(0))
    verdict = str(payload.get("verdict") or "").lower()
    score = float(payload.get("answer_score"))
    expected = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
    if verdict not in expected or score != expected[verdict]:
        raise ValueError("judge verdict and score are inconsistent")
    return verdict, score


def _api_key() -> str:
    key = os.getenv("MARA_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("MARA_API_KEY is not configured")
    return key


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--grbench-data", required=True, type=Path)
    parser.add_argument("--gtsqa-parquet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--errors", required=True, type=Path)
    parser.add_argument("--model", default="gpt-oss-120b")
    parser.add_argument("--base-url", default="https://api.cloud.mara.com/v1")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()
    if args.max_retries < 1:
        raise ValueError("--max-retries must be positive")

    cases = collect_cases(
        args.run_roots,
        grbench_data=args.grbench_data,
        gtsqa_parquet=args.gtsqa_parquet,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.errors.parent.mkdir(parents=True, exist_ok=True)
    completed: set[tuple[str, ...]] = set()
    if args.output.is_file():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                completed.add(tuple(str(row[field]) for field in CORRELATION_FIELDS))

    client = OpenAI(api_key=_api_key(), base_url=args.base_url)
    for ordinal, case in enumerate(cases, 1):
        key = tuple(str(case[field]) for field in CORRELATION_FIELDS)
        if key in completed:
            continue
        user = json.dumps(
            {
                "question": case["question"],
                "reference_answer": case["gold_answer"],
                "candidate_answer": case["prediction"],
            },
            ensure_ascii=False,
        )
        last_error: Exception | None = None
        for retry in range(args.max_retries):
            try:
                response = client.chat.completions.create(
                    model=args.model,
                    temperature=0,
                    max_tokens=256,
                    messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                )
                verdict, score = parse_judgement(response.choices[0].message.content or "")
                output = {
                    **{field: case[field] for field in CORRELATION_FIELDS},
                    "answer_score": score,
                    "rationale_score": None,
                    "ar_score": None,
                    "verdict": verdict,
                    "judge_model": args.model,
                    "judge_prompt_id": PROMPT_ID,
                    "gold_rationale_available": False,
                    "question_sha256": _digest(str(case["question"])),
                    "gold_answer_sha256": _digest(str(case["gold_answer"])),
                    "prediction_sha256": _digest(str(case["prediction"])),
                }
                with args.output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(output, sort_keys=True) + "\n")
                completed.add(key)
                break
            except Exception as exc:  # provider and strict-parser failures are resumable
                last_error = exc
                if retry + 1 < args.max_retries:
                    time.sleep(2**retry)
        else:
            with args.errors.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    **{field: case[field] for field in CORRELATION_FIELDS},
                    "error_class": type(last_error).__name__,
                    "error": str(last_error),
                    "judge_model": args.model,
                    "judge_prompt_id": PROMPT_ID,
                }, sort_keys=True) + "\n")
        print(json.dumps({"ordinal": ordinal, "total": len(cases), "scored": len(completed)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
