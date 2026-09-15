"""Request analysis for the GRBench agent lanes (labs 019 / 021).

Two consumers share this module:

* the **router** (lab 019) analyses a *question* before any LLM call: which
  entities it names, whether they resolve to graph nodes, how connected those
  nodes are (degree class), and which template family / intent the question
  belongs to;
* the **zero-row diagnostic** (lab 021) analyses a *Cypher query* after it
  returned no rows: which indexed anchor literal it used, whether that anchor
  exists, and the anchor node's outgoing degree per relation name, so the
  model repairs the *pattern* instead of re-guessing the anchor (lab-s17:
  52 of 58 zero-row S1 queries had a matched anchor and an empty pattern).

Every graph read goes through a caller-supplied ``run(statement, parameters)``
callable so the executor's validator, workspace scope, Bolt timeout and caps
stay the only Bolt boundary.  Receipts emitted here are content-free: counts,
match kinds, degree classes and milliseconds — never a literal, a name or an
id.  Node ids and types are returned only in the *observation* handed back to
the model, which already lives in the private channel like the query rows.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .grbench_runtime import ANCHOR_PROPERTIES

Runner = Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]]

# Properties that hold human-readable text and carry a TEXT index (CONTAINS).
TEXT_ANCHOR_PROPERTIES = ("feature_name", "feature_title")
# Degree classes from lab 018 (anchor degree p50: easy 12 / medium 71 / hard 2)
# as fixed in request 019: low < 20, hub >= 200.
DEGREE_LOW_MAX = 20
DEGREE_HUB_MIN = 200

# Template families of the GRBench MAPLE/Physics questions (label_agentic_os_difficulty.py)
# mapped to the intent / shape metrics of request 019.  ``hops`` counts graph
# hops from the anchor; ``cardinality`` is the expected answer size.
FAMILY_INTENT: dict[str, dict[str, Any]] = {
    "authors_of_paper": {"intent": "lookup", "hops": 1, "cardinality": "few", "aggregation": False, "argmax": False, "value_filter": False},
    "venue_of_paper": {"intent": "lookup", "hops": 1, "cardinality": "one", "aggregation": False, "argmax": False, "value_filter": False},
    "citation_count": {"intent": "count", "hops": 1, "cardinality": "one", "aggregation": True, "argmax": False, "value_filter": False},
    "author_paper_count": {"intent": "count", "hops": 1, "cardinality": "one", "aggregation": True, "argmax": False, "value_filter": False},
    "coauthor_on_paper": {"intent": "lookup", "hops": 2, "cardinality": "few", "aggregation": False, "argmax": False, "value_filter": False},
    "author_of_both_papers": {"intent": "set_intersection", "hops": 2, "cardinality": "few", "aggregation": False, "argmax": False, "value_filter": False},
    "joint_paper_count": {"intent": "count", "hops": 2, "cardinality": "one", "aggregation": True, "argmax": False, "value_filter": False},
    "closest_collaborator": {"intent": "argmax", "hops": 3, "cardinality": "one", "aggregation": True, "argmax": True, "value_filter": False},
    "collaborators_in_year": {"intent": "set_intersection", "hops": 3, "cardinality": "many", "aggregation": False, "argmax": False, "value_filter": True},
    "most_cited_paper_of_author": {"intent": "argmax", "hops": 2, "cardinality": "one", "aggregation": True, "argmax": True, "value_filter": False},
    "paper_recommendation": {"intent": "recommendation", "hops": 3, "cardinality": "many", "aggregation": True, "argmax": False, "value_filter": False},
    "venue_of_collaboration": {"intent": "lookup", "hops": 3, "cardinality": "few", "aggregation": False, "argmax": False, "value_filter": False},
}


# --------------------------------------------------------------------------- #
# Anchor literals in a Cypher query
# --------------------------------------------------------------------------- #

_PROPS = "|".join(ANCHOR_PROPERTIES)
_STR = r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\""
_VALUE = rf"(?:{_STR}|\$([A-Za-z_][A-Za-z0-9_]*)|(\d+))"
# {feature_name: 'x'} / {id: $node_id} / {source_node_id: 123}
_INLINE = re.compile(rf"\b({_PROPS})\s*:\s*{_VALUE}", re.I)
# n.feature_title = 'x' | CONTAINS 'x' | STARTS WITH 'x' | ENDS WITH 'x' | =~ 'x' | IN ['a', 'b']
_WHERE = re.compile(
    rf"(?:(toLower|toUpper|trim)\s*\(\s*)?\b([A-Za-z_][A-Za-z0-9_]*)\.({_PROPS})\s*\)?\s*"
    rf"(=|<>|CONTAINS|STARTS\s+WITH|ENDS\s+WITH|=~|IN)\s*"
    rf"(?:(toLower|toUpper|trim)\s*\(\s*)?(?:{_VALUE}|\[([^\]]*)\])",
    re.I,
)
_LIST_ITEM = re.compile(_STR)


@dataclass(frozen=True)
class AnchorLiteral:
    """One indexed-property predicate found in a query."""

    property: str
    operator: str  # exact | contains | prefix | suffix | regex | in | not_equal
    value: str | None  # None when a $param was used and not bound
    kind: str  # string | number | param | list
    variable: str | None = None
    folded: str | None = None  # toLower / toUpper / trim applied by the model

    @property
    def resolvable(self) -> bool:
        return self.value is not None and self.operator in ("exact", "contains", "prefix", "suffix", "in")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _literal_value(single: str | None, double: str | None, param: str | None, number: str | None, parameters: Mapping[str, Any] | None) -> tuple[str | None, str]:
    if single is not None:
        return _unescape(single), "string"
    if double is not None:
        return _unescape(double), "string"
    if number is not None:
        return number, "number"
    if param is not None:
        bound = (parameters or {}).get(param)
        return (str(bound) if bound is not None else None), "param"
    return None, "unknown"


_OPERATORS = {"=": "exact", "<>": "not_equal", "contains": "contains", "=~": "regex", "in": "in"}


def _operator(token: str) -> str:
    folded = re.sub(r"\s+", " ", token.strip().lower())
    if folded == "starts with":
        return "prefix"
    if folded == "ends with":
        return "suffix"
    return _OPERATORS.get(folded, folded)


def extract_anchor_literals(query: str, parameters: Mapping[str, Any] | None = None) -> tuple[AnchorLiteral, ...]:
    """Find every indexed anchor predicate (property, operator, literal) in ``query``.

    Inline maps ``{feature_name: 'x'}`` are exact matches; ``WHERE`` predicates
    keep their operator.  ``$param`` values are taken from ``parameters`` when
    bound.  Duplicates (same property, operator and value) are collapsed in
    order of first appearance.
    """
    found: list[AnchorLiteral] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(item: AnchorLiteral) -> None:
        key = (item.property.lower(), item.operator, item.value)
        if key not in seen:
            seen.add(key)
            found.append(item)

    for match in _INLINE.finditer(query):
        prop, single, double, param, number = match.groups()
        value, kind = _literal_value(single, double, param, number, parameters)
        add(AnchorLiteral(property=prop.lower(), operator="exact", value=value, kind=kind))
    for match in _WHERE.finditer(query):
        fold_left, variable, prop, op_token, fold_right, single, double, param, number, items = match.groups()
        operator = _operator(op_token)
        if items is not None:
            values = [_unescape(a if a is not None else b) for a, b in _LIST_ITEM.findall(items)]
            for value in values:
                add(AnchorLiteral(property=prop.lower(), operator="in", value=value, kind="list", variable=variable, folded=(fold_left or None)))
            continue
        value, kind = _literal_value(single, double, param, number, parameters)
        add(AnchorLiteral(property=prop.lower(), operator=operator, value=value, kind=kind, variable=variable, folded=(fold_left or fold_right or None)))
    return tuple(found)


# --------------------------------------------------------------------------- #
# Resolution and degree through the executor boundary
# --------------------------------------------------------------------------- #

_SCOPE = "(n:GRBenchNode {_workspace_id: $workspace_id})"


def _lookup_statement(prop: str, operator: str, limit: int) -> str:
    if prop not in ANCHOR_PROPERTIES:  # defensive: never interpolate an unknown property
        raise ValueError(f"unsupported anchor property: {prop}")
    predicate = {"exact": f"n.{prop} = $value", "contains": f"n.{prop} CONTAINS $value"}[operator]
    return f"MATCH {_SCOPE} WHERE {predicate} RETURN n.id AS id, n.node_type AS node_type LIMIT {int(limit)}"


DEGREE_STATEMENT = (
    "MATCH (n:GRBenchNode {_workspace_id: $workspace_id, id: $node_id})"
    "-[r:GRBENCH_REL]->(m:GRBenchNode {_workspace_id: $workspace_id}) "
    "RETURN r.relation_name AS relation, count(m) AS degree"
)


@dataclass(frozen=True)
class ResolvedAnchor:
    literal: AnchorLiteral
    match: str  # exact | contains | none | skipped
    candidates: tuple[dict[str, str], ...] = ()
    reads: int = 0

    @property
    def resolved(self) -> bool:
        return self.match in ("exact", "contains") and bool(self.candidates)


def resolve_anchor(run: Runner, literal: AnchorLiteral, *, candidate_cap: int = 3) -> ResolvedAnchor:
    """Exact indexed lookup, then a CONTAINS fallback for name / title literals."""
    if not literal.resolvable:
        return ResolvedAnchor(literal=literal, match="skipped")
    value = literal.value or ""
    attempts: list[tuple[str, str]] = []
    if literal.operator in ("exact", "in") or literal.property not in TEXT_ANCHOR_PROPERTIES:
        attempts.append(("exact", value))
    if literal.property in TEXT_ANCHOR_PROPERTIES:
        attempts.append(("contains", value))
        lowered = value.lower()
        if lowered != value and literal.property == "feature_title":
            # paper titles and venue names are stored lowercase; the model often keeps the question's casing
            attempts.append(("exact", lowered))
            attempts.append(("contains", lowered))
        if literal.folded and literal.folded.lower() == "tolower" and literal.property == "feature_name" and value == lowered:
            # author names keep their capitalisation in the graph; the model lower-cased both sides
            attempts.append(("contains", value.title()))
    reads = 0
    seen_attempts: set[tuple[str, str]] = set()
    for match, attempt_value in attempts:
        if (match, attempt_value) in seen_attempts:
            continue
        seen_attempts.add((match, attempt_value))
        rows = run(_lookup_statement(literal.property, match, candidate_cap), {"value": attempt_value})
        reads += 1
        if rows:
            candidates = tuple({"id": str(row.get("id")), "node_type": str(row.get("node_type"))} for row in rows)
            return ResolvedAnchor(literal=literal, match=match, candidates=candidates, reads=reads)
    return ResolvedAnchor(literal=literal, match="none", reads=reads)


def degree_profile(run: Runner, node_id: str) -> dict[str, int]:
    """Outgoing degree of one node per relation name (GRBench stores both directions as separate edges)."""
    rows = run(DEGREE_STATEMENT, {"node_id": node_id})
    return {str(row.get("relation")): int(row.get("degree") or 0) for row in rows if row.get("relation") is not None}


def degree_class(total: int | None) -> str | None:
    if total is None:
        return None
    if total < DEGREE_LOW_MAX:
        return "low"
    if total >= DEGREE_HUB_MIN:
        return "hub"
    return "mid"


# --------------------------------------------------------------------------- #
# Zero-row diagnostic (lab 021)
# --------------------------------------------------------------------------- #

_HINT_RESOLVED = (
    "Diagnostic: the anchor node(s) exist; the rest of the pattern matched nothing. "
    "degree_out lists each anchor's outgoing relation names with counts — a relation_name absent there does not "
    "leave that node in that direction, so check relation_name spelling, arrow direction, the second hop and any "
    "property filter before changing the anchor."
)
_HINT_UNRESOLVED = (
    "Diagnostic: no node matches the anchor literal exactly or as a substring (CONTAINS is case-sensitive; paper "
    "titles and venue names are stored in lowercase, author names keep their capitalisation). Re-check the "
    "spelling or use a shorter distinctive substring."
)
_HINT_MIXED = (
    "Diagnostic: some anchors resolved (see candidates and degree_out) and some did not; fix the unresolved literal "
    "first, then check relation_name and direction against degree_out."
)
_HINT_NO_ANCHOR = "Diagnostic: the query returned no rows and no literal anchor value could be identified for diagnosis."
_HINT_UNRESOLVED_ID = (
    "Diagnostic: no node in this workspace has that id / source_node_id (ids are strings; quote them and copy them from "
    "an earlier result instead of guessing)."
)


def zero_row_diagnostic(
    run: Runner,
    query: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    anchor_cap: int = 2,
    candidate_cap: int = 3,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Diagnose an empty result: (observation for the model, content-free receipt).

    Up to ``anchor_cap`` anchor literals are resolved (exact, then CONTAINS);
    each resolved anchor gets its outgoing degree per relation.  The receipt
    carries counts, match kinds, degree classes and timings only.
    """
    started = time.perf_counter()
    literals = extract_anchor_literals(query, parameters)[:anchor_cap]
    anchors: list[dict[str, Any]] = []
    matches: list[str] = []
    classes: list[str | None] = []
    reads = 0
    for literal in literals:
        resolved = resolve_anchor(run, literal, candidate_cap=candidate_cap)
        reads += resolved.reads
        entry: dict[str, Any] = {
            "property": literal.property,
            "operator": literal.operator,
            "match": resolved.match,
            "candidates": list(resolved.candidates),
        }
        if literal.kind == "number" and resolved.resolved:
            entry["note"] = "matched after treating the numeric literal as a string; id properties are strings, quote them"
        if resolved.resolved:
            first = resolved.candidates[0]["id"]
            degrees = degree_profile(run, first)
            reads += 1
            entry["degree_out"] = degrees
            total = sum(degrees.values())
            entry["degree_class"] = degree_class(total)
            classes.append(entry["degree_class"])
        matches.append(resolved.match)
        anchors.append(entry)
    resolved_count = sum(1 for m in matches if m in ("exact", "contains"))
    attempted = [m for m in matches if m != "skipped"]
    if not attempted:
        hint_kind = "no_anchor"
    elif resolved_count == len(attempted):
        hint_kind = "resolved"
    elif resolved_count == 0:
        hint_kind = "unresolved"
    else:
        hint_kind = "mixed"
    hint = {"no_anchor": _HINT_NO_ANCHOR, "resolved": _HINT_RESOLVED, "unresolved": _HINT_UNRESOLVED, "mixed": _HINT_MIXED}[hint_kind]
    if hint_kind == "unresolved" and all(l.property not in TEXT_ANCHOR_PROPERTIES for l in literals if l.resolvable):
        hint = _HINT_UNRESOLVED_ID
    observation = {"anchors": anchors, "hint": hint}
    receipt = {
        "schema_version": "seocho.grbench_zero_row_diagnostic.v1",
        "anchors_extracted": len(literals),
        "anchors_resolved": resolved_count,
        "matches": matches,
        "degree_classes": [c for c in classes if c],
        "reads": reads,
        "hint_kind": hint_kind,
        "hint_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return observation, receipt


# --------------------------------------------------------------------------- #
# Question analysis (lab 019 router input)
# --------------------------------------------------------------------------- #

_QUOTED = re.compile(r"[\"“”']([^\"“”']{4,}?)\??[\"“”']")
_NAME_RUN = re.compile(r"\b([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){1,3})\b")
_NAME_STOPWORDS = {
    "Can", "What", "Which", "Who", "How", "Please", "Is", "Are", "Did", "Does", "List", "Name", "Find", "Identify",
    "In", "The", "Among", "Based", "Could", "Would", "Give", "Tell", "Show", "Whose", "Where", "When", "Was", "Were",
}


@dataclass(frozen=True)
class Mention:
    kind: str  # title | name
    text: str

    def as_literal(self) -> AnchorLiteral:
        prop = "feature_title" if self.kind == "title" else "feature_name"
        value = self.text.lower() if self.kind == "title" else self.text
        return AnchorLiteral(property=prop, operator="exact", value=value, kind="string")


def extract_question_mentions(question: str) -> tuple[Mention, ...]:
    """Entities a question names: quoted paper titles, capitalised author / venue name runs (lab 018 heuristics)."""
    mentions: list[Mention] = []
    seen: set[tuple[str, str]] = set()
    for text in _QUOTED.findall(question):
        item = Mention("title", text.strip())
        if (item.kind, item.text.casefold()) not in seen:
            seen.add((item.kind, item.text.casefold()))
            mentions.append(item)
    outside = _QUOTED.sub(" ", question)
    for match in _NAME_RUN.finditer(outside):
        tokens = match.group(1).split()
        while tokens and tokens[0] in _NAME_STOPWORDS:  # "Did Hiroshi Furukawa" -> "Hiroshi Furukawa"
            tokens = tokens[1:]
        if len(tokens) < 2:
            continue
        candidate = " ".join(tokens)
        item = Mention("name", candidate)
        if (item.kind, item.text.casefold()) not in seen:
            seen.add((item.kind, item.text.casefold()))
            mentions.append(item)
    return tuple(mentions)


@dataclass
class RequestAnalysis:
    """Content-free router input for one question (request 019 metric table)."""

    question_digest: str
    family: str | None
    intent: str | None
    hops_estimate: int | None
    expected_cardinality: str | None
    needs_aggregation: bool | None
    needs_argmax: bool | None
    has_value_filter: bool | None
    mentions: int
    anchors_resolved: int
    resolvability: list[str] = field(default_factory=list)
    anchor_types: list[str] = field(default_factory=list)
    degree_classes: list[str] = field(default_factory=list)
    degree_class_max: str | None = None
    reads: int = 0
    analysis_ms: float = 0.0
    # private: resolved node ids / types for the router's dispatch, never written to receipts
    resolved_anchors: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def receipt(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("resolved_anchors", None)
        data["schema_version"] = "seocho.request_analysis.v1"
        return data


_CLASS_RANK = {"low": 0, "mid": 1, "hub": 2}


def analyze_request(question: str, *, family: str | None = None, run: Runner | None = None, candidate_cap: int = 3) -> RequestAnalysis:
    """Build the request-analysis block: family / intent shape + resolved anchors + degree classes.

    ``run`` is optional: without a graph reader the analysis is purely
    lexical (mentions counted, nothing resolved).
    """
    import hashlib

    started = time.perf_counter()
    shape = FAMILY_INTENT.get(family or "", {})
    mentions = extract_question_mentions(question)
    analysis = RequestAnalysis(
        question_digest=hashlib.sha256(question.encode("utf-8")).hexdigest(),
        family=family,
        intent=shape.get("intent"),
        hops_estimate=shape.get("hops"),
        expected_cardinality=shape.get("cardinality"),
        needs_aggregation=shape.get("aggregation"),
        needs_argmax=shape.get("argmax"),
        has_value_filter=shape.get("value_filter"),
        mentions=len(mentions),
        anchors_resolved=0,
    )
    if run is not None:
        for mention in mentions:
            resolved = resolve_anchor(run, mention.as_literal(), candidate_cap=candidate_cap)
            analysis.reads += resolved.reads
            analysis.resolvability.append(resolved.match)
            if not resolved.resolved:
                continue
            analysis.anchors_resolved += 1
            first = resolved.candidates[0]
            degrees = degree_profile(run, first["id"])
            analysis.reads += 1
            klass = degree_class(sum(degrees.values()))
            analysis.anchor_types.append(first["node_type"])
            if klass:
                analysis.degree_classes.append(klass)
            analysis.resolved_anchors.append({"kind": mention.kind, "id": first["id"], "node_type": first["node_type"], "degree_out": degrees, "match": resolved.match})
        if analysis.degree_classes:
            analysis.degree_class_max = max(analysis.degree_classes, key=lambda c: _CLASS_RANK[c])
    analysis.analysis_ms = round((time.perf_counter() - started) * 1000, 3)
    return analysis


__all__ = [
    "AnchorLiteral",
    "DEGREE_HUB_MIN",
    "DEGREE_LOW_MAX",
    "DEGREE_STATEMENT",
    "FAMILY_INTENT",
    "Mention",
    "RequestAnalysis",
    "ResolvedAnchor",
    "TEXT_ANCHOR_PROPERTIES",
    "analyze_request",
    "degree_class",
    "degree_profile",
    "extract_anchor_literals",
    "extract_question_mentions",
    "resolve_anchor",
    "zero_row_diagnostic",
]
