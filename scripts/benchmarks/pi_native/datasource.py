"""Worker projection of existing TaskCase; the original payload stays evaluator-side."""

from __future__ import annotations
from typing import Any


def request_for(
    case: Any, *, graph_binding: dict[str, Any], question: str | None = None
) -> dict[str, Any]:
    """Bind the existing datasource's question, without serializing its gold payload.

    GTSQA uses the question from the original selected graph case. FinBench uses
    its already-bound question (account/scale substitutions belong to its loader).
    This function does not create a graph, corpus, tool, or scorer.
    """
    if not graph_binding.get("binding_id") or not graph_binding.get("snapshot_sha256"):
        raise ValueError("explicit_dataset_binding_required")
    if (
        graph_binding.get("task") != case.task
        or str(graph_binding.get("case_id")) != case.case_id
    ):
        raise ValueError("dataset_binding_identity")
    if case.task in ("gtsqa", "finbench"):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("original_bound_question_required")
    else:
        original = case.payload.question
        if question is not None and question != original:
            raise ValueError("question_rewrite")
        question = original
    return dict(
        task=case.task,
        case_id=case.case_id,
        question=question,
        binding_id=graph_binding["binding_id"],
        snapshot_sha256=graph_binding["snapshot_sha256"],
    )
