from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "label_agentic_os_difficulty.py"
    spec = importlib.util.spec_from_file_location("label_agentic_os_difficulty", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_structural_score_thresholds_are_comparable_across_lanes() -> None:
    m = _module()
    assert m.score_to_stratum(0) == "easy" and m.score_to_stratum(2) == "medium" and m.score_to_stratum(3) == "hard"
    # 1 hop, 1 anchor, no aggregation → easy
    assert m.structural_score(hops=1, anchors=1, patterns=1, aggregation=False, value_filter=False, argmax=False) == 0
    # the frozen GTSQA cohort shape: 3 hops, 2 seeds, 4 patterns → hard
    assert m.score_to_stratum(m.structural_score(hops=3, anchors=2, patterns=4, aggregation=False, value_filter=False, argmax=False)) == "hard"
    # 1-hop count → medium; argmax over 2 hops → hard
    assert m.score_to_stratum(m.structural_score(hops=1, anchors=1, patterns=1, aggregation=True, value_filter=False, argmax=False)) == "medium"
    assert m.score_to_stratum(m.structural_score(hops=2, anchors=1, patterns=2, aggregation=True, value_filter=False, argmax=True)) == "hard"


def test_gtsqa_row_labelling_reads_gold_sparql_structure() -> None:
    m = _module()
    row = {"id": 102, "n_hops": 3, "seed_entities": ["a", "b"], "graph_isomorphism": "((2)(1))", "test_type": ["unseen_graph_type"],
           "sparql_query": "SELECT ?answer WHERE { wd:Q1 wdt:P58 ?w. ?f wdt:P58 ?w; wdt:P344 wd:Q2; wdt:P272 ?answer. }",
           "all_answers_wikidata": ["Q434841"]}
    out = m.label_gtsqa_row(row)
    assert out["stratum"] == "hard" and out["patterns"] == 4 and out["anchors"] == 2 and out["multi_answer"] is False
    easy = m.label_gtsqa_row({"id": 1, "n_hops": 1, "seed_entities": ["a"], "sparql_query": "SELECT ?a WHERE { wd:Q1 wdt:P1 ?a }", "all_answers_wikidata": ["Q9"], "test_type": []})
    assert easy["stratum"] == "easy"
    # ORDER BY … LIMIT is an argmax
    top = m.label_gtsqa_row({"id": 2, "n_hops": 1, "seed_entities": ["a"], "sparql_query": "SELECT ?a WHERE { wd:Q1 wdt:P1 ?a } ORDER BY DESC(?n) LIMIT 1", "all_answers_wikidata": [], "test_type": []})
    assert top["argmax"] is True and top["stratum"] == "medium"


def test_grbench_templates_map_to_expected_strata() -> None:
    m = _module()
    cases = {
        'Can you name the authors of "effects of encounters"?': ("authors_of_paper", "easy"),
        'Where is the paper "x" published?': ("venue_of_paper", "easy"),
        'How many papers have cited "x" in their references?': ("citation_count", "medium"),
        'Who is the author of both the papers "x" and "y"?': ("author_of_both_papers", "medium"),
        'With whom does Robert A. Fesen co-write the paper "x"?': ("coauthor_on_paper", "medium"),
        "How many scholarly papers do K.M. Hung and Trevor J. Hall share authorship on?": ("joint_paper_count", "medium"),
        "What was the number of authors Barun Ghosh collaborated with in 2015": ("collaborators_in_year", "hard"),
        "Who collaborates most frequently with the author M. Spite?": ("closest_collaborator", "hard"),
        "Which is the most cited paper by author A.S. Rozhavsky?": ("most_cited_paper_of_author", "hard"),
        "Can you tell me the most frequently cited manuscript by T. Sekioka?": ("most_cited_paper_of_author", "hard"),
        "Can you tell me how many papers T Grycuk has written?": ("author_paper_count", "medium"),
        "Where did Changyong Song and Hyon Chol Kang mostly work together?": ("venue_of_collaboration", "hard"),
        "Which paper should be recommended to the reader of paper exact vortex nucleation?": ("paper_recommendation", "hard"),
    }
    for question, (family, stratum) in cases.items():
        out = m.label_grbench_question("q", question)
        assert (out["family"], out["stratum"]) == (family, stratum), (question, out)
