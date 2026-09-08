from __future__ import annotations

import importlib.util
from pathlib import Path


def _lifecycle():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "vast_agentic_os_lifecycle.py"
    spec = importlib.util.spec_from_file_location("vast_agentic_os_lifecycle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vast_payload_normalization_and_destroy_verification() -> None:
    module = _lifecycle()

    assert module._rows([{"id": 7}]) == [{"id": 7}]
    assert module._rows({"instances": [{"id": 7}]}) == [{"id": 7}]
    assert module._instance_present({"instances": [{"id": 7}]}, 7) is True
    assert module._instance_present({"instances": [{"id": 8}]}, 7) is False
    assert module._instance_present([], 7) is False
