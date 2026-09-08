from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = (
        Path(__file__).parents[2]
        / "scripts"
        / "benchmarks"
        / "preflight_vllm_agentic.py"
    )
    spec = importlib.util.spec_from_file_location("preflight_vllm_agentic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prom_counter_sums_label_series_and_ignores_created_metric() -> None:
    module = _module()
    metrics = """
# HELP vllm:prefix_cache_hits_total Prefix hits
vllm:prefix_cache_hits_total{engine="0",model_name="m"} 1568.0
vllm:prefix_cache_hits_total{engine="1",model_name="m"} 32
vllm:prefix_cache_hits_created{engine="0",model_name="m"} 999
"""

    assert module._prom_counter(metrics, "vllm:prefix_cache_hits_total") == 1600.0
    assert module._prom_counter(metrics, "vllm:missing_total") is None


def test_native_tool_gate_validates_name_and_arguments_without_retaining_content() -> (
    None
):
    module = _module()
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "lookup",
                                "arguments": '{"key":"alpha"}',
                            }
                        }
                    ]
                },
            }
        ]
    }

    receipt = module._tool_call_contract(response)

    assert receipt["passed"] is True
    assert "arguments" not in receipt
    assert (
        module._tool_call_contract(
            {
                **response,
                "choices": [
                    {
                        **response["choices"][0],
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"key":"beta"}',
                                    }
                                }
                            ]
                        },
                    }
                ],
            }
        )["passed"]
        is False
    )
