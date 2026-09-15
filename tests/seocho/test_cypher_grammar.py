from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from seocho.eval.cypher_grammar import (
    CYPHER_ONTOLOGY_GRAMMAR,
    CYPHER_READ_GRAMMAR,
    CYPHER_READ_GRAMMAR_DIGEST,
    GRAMMARS,
    GRAMMAR_NEGATIVE_SAMPLES,
    GRAMMAR_ONTOLOGY_NEGATIVE_SAMPLES,
    GRAMMAR_ONTOLOGY_POSITIVE_SAMPLES,
    GRAMMAR_POSITIVE_SAMPLES,
)

xgr = pytest.importorskip("xgrammar")


def _compiled(text: str = CYPHER_READ_GRAMMAR):
    grammar = xgr.Grammar.from_ebnf(text)
    # a byte-level stand-in vocabulary: accept_string walks characters, so any tokenizer info works
    info = xgr.TokenizerInfo([chr(i) for i in range(256)], vocab_type=xgr.VocabType.RAW, stop_token_ids=[0])
    return xgr.GrammarCompiler(info).compile_grammar(grammar)


def _accepts(compiled, text: str) -> bool:
    matcher = xgr.GrammarMatcher(compiled)
    return matcher.accept_string(text) and (matcher.is_terminated() or matcher.accept_token(0))


def test_grammar_accepts_the_agents_read_only_shapes_and_rejects_policy_violations() -> None:
    compiled = _compiled()
    assert [_accepts(compiled, s) for s in GRAMMAR_POSITIVE_SAMPLES] == [True] * len(GRAMMAR_POSITIVE_SAMPLES)
    assert [_accepts(compiled, s) for s in GRAMMAR_NEGATIVE_SAMPLES] == [False] * len(GRAMMAR_NEGATIVE_SAMPLES)
    assert len(CYPHER_READ_GRAMMAR_DIGEST) == 64


def test_grammar_forces_scope_on_every_labelled_node() -> None:
    compiled = _compiled()
    assert not _accepts(compiled, "MATCH (p:GRBenchNode {feature_title: 'x'}) RETURN p.id AS id")
    assert _accepts(compiled, "MATCH (p:GRBenchNode {_workspace_id: $workspace_id, feature_title: 'x'}) RETURN p.id AS id")
    assert not _accepts(compiled, "MATCH (p:GRBenchNode {_workspace_id: 'grbench'}) RETURN p.id AS id")


def test_clean_cypher_strips_fences() -> None:
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_grbench_grammar_cypher.py"
    spec = importlib.util.spec_from_file_location("run_grbench_grammar_cypher", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.clean_cypher("```cypher\nMATCH (n) RETURN n\n```") == "MATCH (n) RETURN n"
    assert module.clean_cypher("  MATCH (n) RETURN n ;\n") == "MATCH (n) RETURN n ;"
    assert module.CONSTRAINED_MODES == ("xgrammar", "xgrammar-ontology", "none")


def test_ontology_grammar_closes_names_over_the_grbench_vocabulary() -> None:
    plain, onto = _compiled(), _compiled(CYPHER_ONTOLOGY_GRAMMAR)
    assert [_accepts(onto, s) for s in GRAMMAR_POSITIVE_SAMPLES] == [True] * len(GRAMMAR_POSITIVE_SAMPLES)
    assert [_accepts(onto, s) for s in GRAMMAR_ONTOLOGY_POSITIVE_SAMPLES] == [True] * len(GRAMMAR_ONTOLOGY_POSITIVE_SAMPLES)
    assert [_accepts(onto, s) for s in GRAMMAR_NEGATIVE_SAMPLES] == [False] * len(GRAMMAR_NEGATIVE_SAMPLES)
    # names outside the schema: admitted by the plain grammar, refused by the ontology grammar
    assert [_accepts(plain, s) for s in GRAMMAR_ONTOLOGY_NEGATIVE_SAMPLES] == [True] * len(GRAMMAR_ONTOLOGY_NEGATIVE_SAMPLES)
    assert [_accepts(onto, s) for s in GRAMMAR_ONTOLOGY_NEGATIVE_SAMPLES] == [False] * len(GRAMMAR_ONTOLOGY_NEGATIVE_SAMPLES)
    assert set(GRAMMARS) == {"xgrammar", "xgrammar-ontology"}
