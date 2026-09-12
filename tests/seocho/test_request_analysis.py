from __future__ import annotations

import json

import pytest

from seocho.eval import request_analysis as ra
from seocho.eval.grbench_runtime import validate_grbench_read_query


def test_extract_anchor_literals_covers_inline_where_list_and_params() -> None:
    query = (
        "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Hiroshi Furukawa'})"
        "-[r:GRBENCH_REL {relation_name: 'paper'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) "
        "WHERE p.feature_title CONTAINS 'phase' OR toLower(p.feature_title) = toLower('Magnetodisk') "
        "AND p.id IN ['1', '2'] AND p.source_node_id = $sid RETURN p.id AS id"
    )
    literals = ra.extract_anchor_literals(query, {"sid": 77})
    assert [(l.property, l.operator, l.value) for l in literals] == [
        ("feature_name", "exact", "Hiroshi Furukawa"),
        ("feature_title", "contains", "phase"),
        ("feature_title", "exact", "Magnetodisk"),
        ("id", "in", "1"),
        ("id", "in", "2"),
        ("source_node_id", "exact", "77"),
    ]
    assert literals[2].folded == "toLower"
    assert literals[5].kind == "param"
    # relation_name is not an anchor property; escaped quotes survive; duplicates collapse
    dup = ra.extract_anchor_literals("MATCH (n:GRBenchNode {feature_name: 'O\\'Brien'}) WHERE n.feature_name = 'O\\'Brien' RETURN n")
    assert [(l.operator, l.value) for l in dup] == [("exact", "O'Brien")]
    assert ra.extract_anchor_literals("MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) WHERE n.node_type = 'paper' RETURN n") == ()


def test_unbound_param_and_regex_literals_are_not_resolvable() -> None:
    (param,) = ra.extract_anchor_literals("MATCH (n:GRBenchNode {id: $node_id}) RETURN n")
    assert param.value is None and not param.resolvable
    (regex,) = ra.extract_anchor_literals("MATCH (n:GRBenchNode) WHERE n.feature_title =~ '(?i).*phase.*' RETURN n")
    assert regex.operator == "regex" and not regex.resolvable


class _Graph:
    """Scripted graph reader: records every statement, answers from a tiny table."""

    def __init__(self) -> None:
        self.nodes = {
            ("feature_name", "Hiroshi Furukawa"): ("2300633696", "author"),
            ("feature_title", "phase separation dynamics"): ("3126056174", "paper"),
        }
        self.degrees = {"2300633696": {"paper": 12}, "3126056174": {"author": 3, "venue": 1, "reference": 40, "cited_by": 250}}
        self.statements: list[tuple[str, dict]] = []

    def __call__(self, statement: str, parameters):
        self.statements.append((statement, dict(parameters)))
        # every diagnostic statement must pass the executor's validator (anchor required, no evidence columns)
        validate_grbench_read_query(statement, row_cap=20, require_evidence_columns=False, require_anchor=True)
        if statement.startswith("MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: $node_id})"):
            return [{"relation": k, "degree": v} for k, v in self.degrees.get(parameters["node_id"], {}).items()]
        prop = "feature_name" if "n.feature_name" in statement else "feature_title" if "n.feature_title" in statement else "id"
        value = parameters["value"]
        hits = []
        for (p, text), (nid, ntype) in self.nodes.items():
            if p != prop:
                continue
            if ("CONTAINS" in statement and value in text) or ("CONTAINS" not in statement and value == text):
                hits.append({"id": nid, "node_type": ntype})
        return hits


def test_resolve_anchor_exact_then_contains_then_case_folds() -> None:
    graph = _Graph()
    exact = ra.resolve_anchor(graph, ra.AnchorLiteral("feature_name", "exact", "Hiroshi Furukawa", "string"))
    assert exact.match == "exact" and exact.reads == 1 and exact.candidates[0]["node_type"] == "author"
    contains = ra.resolve_anchor(graph, ra.AnchorLiteral("feature_title", "exact", "separation", "string"))
    assert contains.match == "contains" and contains.reads == 2
    folded = ra.resolve_anchor(graph, ra.AnchorLiteral("feature_title", "exact", "Phase Separation Dynamics", "string"))
    assert folded.match == "exact" and folded.reads == 3  # exact, contains, exact(lowercase)
    lowered_name = ra.resolve_anchor(graph, ra.AnchorLiteral("feature_name", "exact", "hiroshi furukawa", "string", folded="toLower"))
    assert lowered_name.match == "contains" and lowered_name.reads == 3  # exact, contains, contains(title-case)
    miss = ra.resolve_anchor(graph, ra.AnchorLiteral("feature_name", "exact", "Nobody Here", "string"))
    assert miss.match == "none" and not miss.candidates


def test_degree_class_thresholds_follow_request_019() -> None:
    assert ra.degree_class(0) == "low" and ra.degree_class(19) == "low"
    assert ra.degree_class(20) == "mid" and ra.degree_class(199) == "mid"
    assert ra.degree_class(200) == "hub" and ra.degree_class(None) is None


def test_zero_row_diagnostic_resolved_anchor_reports_pattern_hint_and_content_free_receipt() -> None:
    graph = _Graph()
    query = (
        "MATCH (a:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Hiroshi Furukawa'})"
        "-[r:GRBENCH_REL {relation_name: 'author'}]->(p:GRBenchNode {_workspace_id: $workspace_id}) RETURN p.id AS id"
    )
    observation, receipt = ra.zero_row_diagnostic(graph, query)
    (anchor,) = observation["anchors"]
    assert anchor["match"] == "exact" and anchor["candidates"] == [{"id": "2300633696", "node_type": "author"}]
    assert anchor["degree_out"] == {"paper": 12} and anchor["degree_class"] == "low"
    assert observation["hint"].startswith("Diagnostic: the anchor node(s) exist")
    assert receipt["hint_kind"] == "resolved" and receipt["anchors_extracted"] == 1 and receipt["anchors_resolved"] == 1
    assert receipt["matches"] == ["exact"] and receipt["degree_classes"] == ["low"] and receipt["reads"] == 2
    dumped = json.dumps(receipt)
    for forbidden in ("Furukawa", "2300633696", "author"):
        assert forbidden not in dumped
    # the degree read is workspace-scoped on both ends and anchored on id
    degree_statement = graph.statements[-1][0]
    assert degree_statement == ra.DEGREE_STATEMENT and degree_statement.count("$workspace_id") == 2


def test_zero_row_diagnostic_unresolved_mixed_and_no_anchor_kinds() -> None:
    graph = _Graph()
    _, miss = ra.zero_row_diagnostic(graph, "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, feature_name: 'Nobody'}) RETURN n.id AS id")
    assert miss["hint_kind"] == "unresolved" and miss["matches"] == ["none"] and miss["degree_classes"] == []
    obs, mixed = ra.zero_row_diagnostic(
        graph,
        "MATCH (a:GRBenchNode {feature_name: 'Hiroshi Furukawa'})-->(p:GRBenchNode {feature_title: 'missing title'}) RETURN p.id AS id",
    )
    assert mixed["hint_kind"] == "mixed" and mixed["matches"] == ["exact", "none"]
    assert obs["hint"].startswith("Diagnostic: some anchors resolved")
    _, none = ra.zero_row_diagnostic(graph, "MATCH (n:GRBenchNode {id: $node_id}) RETURN n.id AS id")
    assert none["hint_kind"] == "no_anchor" and none["anchors_extracted"] == 1 and none["matches"] == ["skipped"]
    # anchor cap bounds the extra reads
    graph.statements.clear()
    ra.zero_row_diagnostic(graph, "MATCH (n:GRBenchNode) WHERE n.id IN ['1','2','3','4'] RETURN n.id AS id", anchor_cap=2)
    assert len({p.get("value") for _, p in graph.statements}) == 2


def test_numeric_id_literal_is_resolved_as_string_with_note() -> None:
    graph = _Graph()
    graph.nodes[("id", "2300633696")] = ("2300633696", "author")
    obs, receipt = ra.zero_row_diagnostic(graph, "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: 2300633696})-[:GRBENCH_REL]->(m) RETURN m.id AS id")
    assert obs["anchors"][0]["note"].startswith("matched after treating the numeric literal as a string")
    assert receipt["anchors_resolved"] == 1
    obs, receipt = ra.zero_row_diagnostic(graph, "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: '404'}) RETURN n.id AS id")
    assert receipt["hint_kind"] == "unresolved" and obs["hint"].startswith("Diagnostic: no node in this workspace has that id")


def test_extract_question_mentions_titles_and_names() -> None:
    mentions = ra.extract_question_mentions(
        'Who are the authors of "Phase separation dynamics in late stage"? Did Hiroshi Furukawa collaborate with Karoly Szego in 1990?'
    )
    assert [(m.kind, m.text) for m in mentions] == [
        ("title", "Phase separation dynamics in late stage"),
        ("name", "Hiroshi Furukawa"),
        ("name", "Karoly Szego"),
    ]
    assert mentions[0].as_literal() == ra.AnchorLiteral("feature_title", "exact", "phase separation dynamics in late stage", "string")
    assert ra.extract_question_mentions("Which venue published the most papers?") == ()


def test_analyze_request_emits_content_free_router_block() -> None:
    graph = _Graph()
    analysis = ra.analyze_request(
        'Which paper by Hiroshi Furukawa is cited most, and does it relate to "phase separation dynamics"?',
        family="most_cited_paper_of_author",
        run=graph,
    )
    assert analysis.intent == "argmax" and analysis.needs_argmax and analysis.hops_estimate == 2
    assert analysis.mentions == 2 and analysis.anchors_resolved == 2
    assert analysis.resolvability == ["exact", "exact"] and sorted(analysis.anchor_types) == ["author", "paper"]
    assert analysis.degree_class_max == "hub" and analysis.reads == 4
    receipt = analysis.receipt()
    assert receipt["schema_version"] == "seocho.request_analysis.v1" and "resolved_anchors" not in receipt
    assert "Furukawa" not in json.dumps(receipt) and len(receipt["question_digest"]) == 64
    assert {a["kind"]: a["id"] for a in analysis.resolved_anchors} == {"title": "3126056174", "name": "2300633696"}
    lexical = ra.analyze_request("Who wrote it?", family="unknown_family")
    assert lexical.intent is None and lexical.mentions == 0 and lexical.reads == 0


def test_family_intent_table_covers_every_grbench_family() -> None:
    expected = {
        "authors_of_paper", "citation_count", "venue_of_paper", "coauthor_on_paper", "author_of_both_papers", "closest_collaborator",
        "collaborators_in_year", "joint_paper_count", "most_cited_paper_of_author", "paper_recommendation", "author_paper_count",
        "venue_of_collaboration",
    }
    assert set(ra.FAMILY_INTENT) == expected
    for shape in ra.FAMILY_INTENT.values():
        assert shape["intent"] in {"lookup", "count", "set_intersection", "argmax", "recommendation"}
