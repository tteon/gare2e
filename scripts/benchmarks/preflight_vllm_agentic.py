#!/usr/bin/env python3
"""Verify a vLLM endpoint's native tools and prefix-cache telemetry.

The emitted receipt intentionally excludes prompts, completions, thought text,
and function arguments.  It uses only SEOCHO's neutral evaluation parser, not
runtime or treatment behavior, so it is valid before a direct-agent baseline.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from seocho.eval.agentic_os_observability import (
    REQUIRED_VLLM_METRIC_NAMES,
    parse_vllm_metric_metadata,
    parse_vllm_metrics,
)


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urlopen(request, timeout=180) as response:  # noqa: S310 - benchmark URL is explicit
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"vLLM HTTP {error.code}: {detail}") from error


def _request_text(url: str) -> str:
    with urlopen(url, timeout=30) as response:  # noqa: S310 - benchmark URL is explicit
        return response.read().decode("utf-8", errors="replace")


def _prom_counter(metrics: str, name: str) -> float | None:
    """Sum one Prometheus counter across labels without a client dependency."""
    pattern = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([^\s]+)$")
    total = 0.0
    found = False
    for line in metrics.splitlines():
        match = pattern.match(line.strip())
        if match:
            total += float(match.group(1))
            found = True
    return total if found else None


def _counter_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or after < before:
        return None
    return after - before


def _tool_call_contract(response: dict[str, Any]) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = message.get("tool_calls") or []
    valid = False
    if len(tool_calls) == 1:
        function = tool_calls[0].get("function") or {}
        try:
            arguments = json.loads(str(function.get("arguments") or ""))
        except json.JSONDecodeError:
            arguments = None
        valid = bool(
            choice.get("finish_reason") == "tool_calls"
            and function.get("name") == "lookup"
            and arguments == {"key": "alpha"}
        )
    return {
        "passed": valid,
        "tool_call_count": len(tool_calls),
        "finish_reason": choice.get("finish_reason"),
        "expected_tool": "lookup",
        "expected_argument_contract": "key=alpha",
    }


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _completion_receipt(response: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = response.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    tool_calls = message.get("tool_calls") or []
    return {
        "http_ms": round(elapsed_ms, 3),
        "finish_reason": choice.get("finish_reason"),
        "tool_call_count": len(tool_calls),
        "tool_names": sorted(
            str((call.get("function") or {}).get("name", "")) for call in tool_calls
        ),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_tokens": details.get("cached_tokens"),
        },
    }


def _complete(base_url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = _request_json(f"{base_url.rstrip('/')}/v1/chat/completions", payload)
    return response, (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--metrics-out", type=Path)
    args = parser.parse_args()
    receipt: dict[str, Any] = {
        "schema_version": "agentic_os.vllm_preflight_receipt.v2",
        "endpoint": {"base_url": args.base_url, "model": args.model},
        "required_metric_names": sorted(REQUIRED_VLLM_METRIC_NAMES),
        "passed": False,
        "blockers": [],
    }
    metrics_after_warm = ""
    try:
        models_payload = _request_json(f"{args.base_url.rstrip('/')}/v1/models")
        served_models = sorted(
            str(item.get("id"))
            for item in models_payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        )
        model_served = args.model in served_models
        tool = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Retrieve one opaque value by key.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
        }
        tool_payload = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Call lookup for key alpha. Use the tool now.",
                }
            ],
            "tools": [tool],
            "tool_choice": "required",
            "temperature": 0,
            "max_tokens": 256,
        }
        tool_response, tool_elapsed = _complete(args.base_url, tool_payload)
        tool_contract = _tool_call_contract(tool_response)
        # Repeating an identical long prefix allows a cache-telemetry probe
        # without retaining semantic workload material in the receipt.
        prefix = "Benchmark cache sentinel. " * 500
        cache_payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": prefix + "Return exactly: ok"}],
            "temperature": 0,
            "max_tokens": 8,
        }
        reset_response = _request_json(
            f"{args.base_url.rstrip('/')}/reset_prefix_cache", {}
        )
        prefix_reset_succeeded = reset_response.get("success") is True
        metrics_before = _request_text(f"{args.base_url.rstrip('/')}/metrics")
        cold_response, cold_elapsed = _complete(args.base_url, cache_payload)
        metrics_after_cold = _request_text(f"{args.base_url.rstrip('/')}/metrics")
        warm_response, warm_elapsed = _complete(args.base_url, cache_payload)
        metrics_after_warm = _request_text(f"{args.base_url.rstrip('/')}/metrics")

        before = parse_vllm_metrics(metrics_before)
        after_cold = parse_vllm_metrics(metrics_after_cold)
        after_warm = parse_vllm_metrics(metrics_after_warm)
        cold_query_delta = _counter_delta(
            before.get("vllm:prefix_cache_queries"),
            after_cold.get("vllm:prefix_cache_queries"),
        )
        cold_hit_delta = _counter_delta(
            before.get("vllm:prefix_cache_hits"),
            after_cold.get("vllm:prefix_cache_hits"),
        )
        warm_query_delta = _counter_delta(
            after_cold.get("vllm:prefix_cache_queries"),
            after_warm.get("vllm:prefix_cache_queries"),
        )
        warm_hit_delta = _counter_delta(
            after_cold.get("vllm:prefix_cache_hits"),
            after_warm.get("vllm:prefix_cache_hits"),
        )
        api_cache_field = any(
            ((item.get("usage") or {}).get("prompt_tokens_details") or {}).get(
                "cached_tokens"
            )
            is not None
            for item in (cold_response, warm_response)
        )
        metric_names = set(after_warm)
        missing_metrics = sorted(REQUIRED_VLLM_METRIC_NAMES - metric_names)
        cache_config = (
            parse_vllm_metric_metadata(metrics_after_warm).get("vllm:cache_config_info")
            or {}
        )
        prefix_enabled = (
            str(cache_config.get("enable_prefix_caching", "")).lower() == "true"
        )
        warm_cache_observed = bool(
            warm_query_delta is not None
            and warm_query_delta > 0
            and warm_hit_delta is not None
            and warm_hit_delta > 0
        )
        blockers: list[str] = []
        if not model_served:
            blockers.append("requested_model_not_served")
        if not tool_contract["passed"]:
            blockers.append("native_tool_contract_failed")
        if missing_metrics:
            blockers.append("required_metric_families_missing")
        if not prefix_enabled:
            blockers.append("prefix_caching_not_enabled")
        if not prefix_reset_succeeded:
            blockers.append("prefix_cache_reset_failed")
        if not warm_cache_observed:
            blockers.append("warm_prefix_cache_hit_not_observed")
        receipt.update(
            {
                "served_models": served_models,
                "checks": {
                    "native_tool_call": _completion_receipt(
                        tool_response, tool_elapsed
                    ),
                    "native_tool_contract": tool_contract,
                    "prefix_cache_cold": _completion_receipt(
                        cold_response, cold_elapsed
                    ),
                    "prefix_cache_warm": _completion_receipt(
                        warm_response, warm_elapsed
                    ),
                    "prefix_cache_cold_prometheus": {
                        "queries_delta": cold_query_delta,
                        "hits_delta": cold_hit_delta,
                    },
                    "prefix_cache_reset": {
                        "supported": True,
                        "success": prefix_reset_succeeded,
                        "scope": "local_vllm_prefix_cache",
                    },
                    "prefix_cache_warm_prometheus": {
                        "queries_delta": warm_query_delta,
                        "hits_delta": warm_hit_delta,
                        "hit_ratio": (
                            warm_hit_delta / warm_query_delta
                            if warm_query_delta is not None
                            and warm_query_delta > 0
                            and warm_hit_delta is not None
                            else None
                        ),
                    },
                    "metrics": {
                        "missing_required": missing_metrics,
                        "cache_config_info": cache_config,
                        "api_cached_tokens_field_observed": api_cache_field,
                    },
                },
                "blockers": blockers,
                "passed": not blockers,
            }
        )
    except Exception as exc:
        receipt["failure_class"] = type(exc).__name__
        receipt["failure_message"] = str(exc)[:1000]
        receipt["blockers"] = ["preflight_exception"]
        receipt["passed"] = False
    finally:
        _write_receipt(args.receipt_out, receipt)
        if args.metrics_out and metrics_after_warm:
            selected = [
                line
                for line in metrics_after_warm.splitlines()
                if line.startswith(("# HELP vllm:", "# TYPE vllm:", "vllm:"))
            ]
            args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
            args.metrics_out.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"passed": receipt["passed"], "blockers": receipt["blockers"]},
            sort_keys=True,
        )
    )
    return 0 if receipt["passed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
