from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datasource import request_for


@pytest.mark.parametrize(
    "task", ["gtsqa", "grbench", "finbench", "finder", "finance", "graphrag-bench"]
)
def test_projection_keeps_gold_and_metadata_out(task):
    case = SimpleNamespace(
        task=task,
        case_id="1",
        payload=SimpleNamespace(question="question", expected_answer="SECRET_GOLD"),
    )
    binding = dict(
        task=task,
        case_id="1",
        binding_id="db-snapshot",
        snapshot_sha256="a" * 64,
        password="SECRET",
    )
    result = request_for(
        case,
        graph_binding=binding,
        question="question" if task in ("gtsqa", "finbench") else None,
    )
    assert result["question"] == "question" and "SECRET" not in repr(result)
    assert set(result) == {
        "task",
        "case_id",
        "question",
        "binding_id",
        "snapshot_sha256",
    }
    with pytest.raises(ValueError):
        request_for(case, graph_binding={**binding, "case_id": "other"})
