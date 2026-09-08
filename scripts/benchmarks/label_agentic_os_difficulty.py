#!/usr/bin/env python3
"""Assign easy / medium / hard difficulty strata to GRBench and GTSQA questions.

The stratum is a *structural* score computed from the gold query or the
question template only — never from observed agent behaviour, which is the
dependent variable.  The same score is used for Cypher (GRBench MAPLE/Physics)
and SPARQL (GTSQA) so the strata are comparable across lanes:

    hops        1 → 0, 2 → 1, 3 → 2, 4+ → 3
    anchors     ≥ 2 seed entities / named anchors → +1
    patterns    ≥ 4 triple / relationship patterns → +1
    aggregation COUNT / DISTINCT count           → +1
    filter      year / value constraint          → +1
    argmax      "most", "closest", ORDER BY … LIMIT 1, superlative → +2

    easy = 0, medium = 1–2, hard ≥ 3

Orthogonal tags (distribution shift, multi-answer, text-property lookup) are
emitted as columns, not folded into the stratum.  Output is content-free for
GTSQA (ids and features only); GRBench rows carry the question template family.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any, Mapping

STRATA = ("easy", "medium", "hard")


def score_to_stratum(score: int) -> str:
    if score <= 0:
        return "easy"
    if score <= 2:
        return "medium"
    return "hard"


def structural_score(*, hops: int, anchors: int, patterns: int, aggregation: bool, value_filter: bool, argmax: bool) -> int:
    score = {1: 0, 2: 1, 3: 2}.get(hops, 3 if hops >= 4 else 0)
    score += 1 if anchors >= 2 else 0
    score += 1 if patterns >= 4 else 0
    score += 1 if aggregation else 0
    score += 1 if value_filter else 0
    score += 2 if argmax else 0
    return score


# --------------------------------------------------------------------- GTSQA
_SPARQL_PATTERN = re.compile(r"wdt:P\d+")


def label_gtsqa_row(row: Mapping[str, Any]) -> dict[str, Any]:
    sparql = str(row.get("sparql_query") or "")
    upper = sparql.upper()
    hops = int(row.get("n_hops") or 0)
    anchors = len(row.get("seed_entities") or [])
    patterns = len(_SPARQL_PATTERN.findall(sparql))
    aggregation = "COUNT" in upper or "GROUP BY" in upper
    value_filter = "FILTER" in upper or "MINUS" in upper or "NOT EXISTS" in upper
    argmax = "ORDER BY" in upper and "LIMIT" in upper
    score = structural_score(hops=hops, anchors=anchors, patterns=patterns, aggregation=aggregation, value_filter=value_filter, argmax=argmax)
    answers = row.get("all_answers_wikidata") or []
    return {
        "qid": str(row.get("id")),
        "lane": "gtsqa",
        "stratum": score_to_stratum(score),
        "score": score,
        "hops": hops,
        "anchors": anchors,
        "patterns": patterns,
        "aggregation": aggregation,
        "value_filter": value_filter,
        "argmax": argmax,
        "multi_answer": len(answers) > 1,
        "shape": row.get("graph_isomorphism"),
        "test_type": list(row.get("test_type") or []),
    }


# ------------------------------------------------------------------- GRBench
# Template families for GRBench MAPLE/Physics.  Each family carries the
# structural features of the gold Cypher an oracle would write.
_GRBENCH_FAMILIES: tuple[tuple[str, re.Pattern[str], dict[str, Any]], ...] = (
    ("closest_collaborator", re.compile(r"closest (collaborator|associate)|collaborates most frequently|highest number of collaborations", re.I),
     {"hops": 2, "anchors": 1, "patterns": 2, "aggregation": True, "value_filter": False, "argmax": True}),
    ("paper_recommendation", re.compile(r"reader of|should be recommended", re.I),
     {"hops": 2, "anchors": 2, "patterns": 4, "aggregation": True, "value_filter": False, "argmax": True}),
    ("venue_of_collaboration", re.compile(r"(mostly work together|chief place of collaboration)", re.I),
     {"hops": 2, "anchors": 2, "patterns": 3, "aggregation": True, "value_filter": False, "argmax": True}),
    ("author_paper_count", re.compile(r"how many papers .* (has|have) written", re.I),
     {"hops": 1, "anchors": 1, "patterns": 1, "aggregation": True, "value_filter": False, "argmax": False}),
    ("most_cited_paper_of_author", re.compile(r"most (frequently )?(referenced|cited) (publication|paper|manuscript)", re.I),
     {"hops": 2, "anchors": 1, "patterns": 2, "aggregation": True, "value_filter": False, "argmax": True}),
    ("collaborators_in_year", re.compile(r"(collaborat|co-authors).*\b(19|20)\d{2}\b|\b(19|20)\d{2}\b.*collaborat", re.I),
     {"hops": 2, "anchors": 1, "patterns": 2, "aggregation": True, "value_filter": True, "argmax": False}),
    ("joint_paper_count", re.compile(r"(how many|number of|count).*(jointly|together|share authorship|co-authored)|(jointly written|write together|share authorship on)", re.I),
     {"hops": 1, "anchors": 2, "patterns": 2, "aggregation": True, "value_filter": False, "argmax": False}),
    ("author_of_both_papers", re.compile(r"(author|wrote|writed|authored|penned).*both", re.I),
     {"hops": 1, "anchors": 2, "patterns": 2, "aggregation": False, "value_filter": False, "argmax": False}),
    ("coauthor_on_paper", re.compile(r"(co-write|collaborates with|teamed up with|worked with|co-authors with)", re.I),
     {"hops": 1, "anchors": 2, "patterns": 2, "aggregation": False, "value_filter": False, "argmax": False}),
    ("citation_count", re.compile(r"(how many|number of|count).*(cite|reference)|(cite|reference).*(how many|number of)", re.I),
     {"hops": 1, "anchors": 1, "patterns": 1, "aggregation": True, "value_filter": False, "argmax": False}),
    ("venue_of_paper", re.compile(r"(publication|published|venue|released|where can I find)", re.I),
     {"hops": 1, "anchors": 1, "patterns": 1, "aggregation": False, "value_filter": False, "argmax": False}),
    ("authors_of_paper", re.compile(r"(author|wrote|who penned|who writ)", re.I),
     {"hops": 1, "anchors": 1, "patterns": 1, "aggregation": False, "value_filter": False, "argmax": False}),
)


def label_grbench_question(qid: Any, question: str) -> dict[str, Any]:
    family, features = "unmatched", None
    for name, pattern, feats in _GRBENCH_FAMILIES:
        if pattern.search(question):
            family, features = name, feats
            break
    if features is None:
        return {"qid": str(qid), "lane": "grbench", "stratum": None, "score": None, "family": family, "text_lookup": '"' in question}
    score = structural_score(**features)
    return {
        "qid": str(qid),
        "lane": "grbench",
        "stratum": score_to_stratum(score),
        "score": score,
        "family": family,
        **features,
        "text_lookup": '"' in question,  # exact title string must be matched in the graph
    }


def distribution(rows: list[Mapping[str, Any]], key: str = "stratum") -> dict[str, int]:
    counts = collections.Counter(str(r.get(key)) for r in rows)
    return {k: counts.get(k, 0) for k in (*STRATA, "None") if counts.get(k)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtsqa-parquet", type=Path)
    parser.add_argument("--grbench-questions", type=Path, help="GRBench processed data.json (JSONL: qid, question, answer)")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report: list[str] = ["# Difficulty strata", ""]
    if args.gtsqa_parquet:
        import pyarrow.parquet as pq

        table = pq.read_table(args.gtsqa_parquet, columns=["id", "n_hops", "seed_entities", "sparql_query", "all_answers_wikidata", "graph_isomorphism", "test_type"])
        rows = [label_gtsqa_row(r) for r in table.to_pylist()]
        with (args.out_dir / "gtsqa-difficulty.jsonl").open("w", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r, sort_keys=True) + "\n")
        report += ["## GTSQA", f"- rows: {len(rows)}", f"- strata: {distribution(rows)}",
                   f"- by hops: {dict(sorted(collections.Counter((r['hops'], r['stratum']) for r in rows).items()))}",
                   f"- test_type × stratum: {dict(sorted(collections.Counter((tuple(r['test_type']), r['stratum']) for r in rows).items()))}", ""]
    if args.grbench_questions:
        items = [json.loads(line) for line in args.grbench_questions.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [label_grbench_question(it.get("qid"), str(it.get("question", ""))) for it in items]
        with (args.out_dir / "grbench-difficulty.jsonl").open("w", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r, sort_keys=True) + "\n")
        fam = collections.Counter((r["family"], r["stratum"]) for r in rows)
        report += ["## GRBench MAPLE/Physics", f"- rows: {len(rows)}", f"- strata: {distribution(rows)}",
                   "- families: " + ", ".join(f"{f}={n} ({s})" for (f, s), n in sorted(fam.items(), key=lambda x: -x[1])), ""]
    (args.out_dir / "difficulty-distribution.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
