"""Receipts and root tracing for reproducible SEOCHO experiment runs.

This module deliberately records identities in traces/reports, never metric
labels.  It makes the execution runtime explicit: a direct SEOCHO call is not
silently described as an OpenAI Agents SDK run.
"""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import re
import importlib.metadata
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from ..metrics import get_metrics
from ..tracing import current_backend_names, enable_tracing, flush_tracing, start_span


def direct_runtime_receipt() -> dict[str, Any]:
    """Return the runtime identity for the deterministic SDK E2E path."""
    return {
        "schema_version": "seocho.experiment_runtime_receipt.v1",
        "execution_runtime": "seocho_direct",
        "agents_sdk_version": None,
        "max_turns": None,
        "toolset_digest": None,
    }


def agents_sdk_runtime_receipt(*, max_turns: int, toolset_digest: str) -> dict[str, Any]:
    """Return an explicit receipt for a real Agents SDK Runner experiment."""
    try:
        version = importlib.metadata.version("openai-agents")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("openai-agents must be installed for agents_sdk experiments") from exc
    return {
        "schema_version": "seocho.experiment_runtime_receipt.v1",
        "execution_runtime": "agents_sdk",
        "agents_sdk_version": version,
        "max_turns": max(1, int(max_turns)),
        "toolset_digest": str(toolset_digest),
    }


@contextmanager
def experiment_run_trace(
    *,
    receipt: Mapping[str, Any],
    run_name: str,
    workspace_id: str,
) -> Iterator[dict[str, Any]]:
    """Create the parent span and a report-safe run manifest fragment."""
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    runtime = str(receipt.get("execution_runtime", "unknown"))
    manifest = {
        "run_id": run_id,
        "outcome": "ok",
        "runtime_receipt": dict(receipt),
        "trace_backends": current_backend_names(),
        "trace_content_capture": str(os.getenv("SEOCHO_TRACE_CAPTURE_CONTENT", "")).lower()
        in {"1", "true", "yes", "on"},
    }
    outcome = "ok"
    with start_span(
        "experiment.run",
        metadata={
            "experiment.run_id": run_id,
            "experiment.name": run_name,
            "workspace_id": workspace_id,
            **dict(receipt),
        },
        tags=["experiment", f"runtime:{runtime}"],
    ) as span:
        try:
            yield manifest
        except Exception:
            outcome = "error"
            raise
        finally:
            # A process may return normally with per-file failures. Record that
            # degraded outcome at the root instead of calling the run successful.
            outcome = str(manifest.get("outcome", outcome))
            elapsed = time.perf_counter() - started
            span.set_metadata({"experiment.outcome": outcome, "duration_ms": round(elapsed * 1000, 2)})
            metrics = get_metrics()
            metrics.record("seocho.experiment.run.duration", elapsed, {"runtime": runtime, "outcome": outcome})
            metrics.add("seocho.experiment.run.count", attributes={"runtime": runtime, "outcome": outcome})
            metrics.add("seocho.observability.trace_complete.count", attributes={"workflow": "experiment", "outcome": outcome})


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_EXPERIMENT_KEYS = ("project_id", "campaign_id", "experiment_id", "run_id")
_EXPERIMENT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("experiment_context", default={})


def experiment_identity() -> dict[str, str]:
    """Require safe, stable storage identities only in the explicit Vast mode."""
    if os.getenv("SEOCHO_EXPERIMENT_OBSERVABILITY") != "1":
        return {}
    identity = {"project_id": os.getenv("SEOCHO_PROJECT_ID", ""),
                "campaign_id": os.getenv("SEOCHO_CAMPAIGN_ID", ""),
                "experiment_id": os.getenv("SEOCHO_EXPERIMENT_ID", ""),
                "run_id": os.getenv("SEOCHO_RUN_ID", "")}
    if any(not _ID.fullmatch(value) or ".." in value for value in identity.values()):
        raise ValueError("experiment observability requires safe project/campaign/experiment/run IDs")
    return identity


@contextmanager
def experiment_span(name: str, correlation: Mapping[str, Any] | None = None) -> Iterator[Any]:
    """Reuse SEOCHO tracing; never export prompts, responses or error messages."""
    identity = experiment_identity()
    if identity and "otlp" not in current_backend_names():
        if not enable_tracing(backend="otlp"):
            raise RuntimeError("experiment observability requires the SEOCHO otel extra")
        atexit.register(flush_tracing)
    metadata = {**_EXPERIMENT_CONTEXT.get(), **{key: value for key, value in (correlation or {}).items()
                if key in (*_EXPERIMENT_KEYS, "case_id", "workspace_id", "condition")}}
    # Deployment IDs own the storage path; runner-local IDs remain in private events.
    metadata.update(identity)
    token = _EXPERIMENT_CONTEXT.set(metadata)
    try:
        with start_span(name, metadata=metadata, redact_errors=True) as span:
            yield span
    finally:
        _EXPERIMENT_CONTEXT.reset(token)


def observe_experiment(name: str) -> Any:
    """Instrument synchronous and async experiment boundaries, preserving signatures."""
    def decorate(call: Any) -> Any:
        def correlation(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Mapping[str, Any]:
            private = kwargs.get("private_trace")
            if private is None and args:
                private = getattr(args[0], "private_trace", None)
            values = {**dict(kwargs.get("trace_metadata") or {}), **dict(getattr(private, "correlation", {}))}
            case = kwargs.get("case")
            if case is None and args and hasattr(args[0], "case_id"):
                case = args[0]
            if case is not None:
                values.update({"case_id": case.case_id, "workspace_id": case.workspace_id})
            return values
        def enrich(span: Any, result: Any) -> None:
            usage = getattr(result, "usage", None)
            if usage is not None:
                for output, names in {"input_tokens": ("input_tokens", "prompt_tokens"),
                                      "output_tokens": ("output_tokens", "completion_tokens")}.items():
                    for key in names:
                        value = usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)
                        if isinstance(value, int):
                            span.set_metadata({"gen_ai.usage." + output: value})
                            break
            receipt = getattr(result, "receipt", None)
            if isinstance(receipt, Mapping) and isinstance(receipt.get("success"), bool):
                span.set_metadata({"experiment.success": receipt["success"]})
        if inspect.iscoroutinefunction(call):
            @functools.wraps(call)
            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                with experiment_span(name, correlation(args, kwargs)) as span:
                    result = await call(*args, **kwargs)
                    enrich(span, result)
                    return result
            return asynchronous
        @functools.wraps(call)
        def synchronous(*args: Any, **kwargs: Any) -> Any:
            with experiment_span(name, correlation(args, kwargs)) as span:
                result = call(*args, **kwargs)
                enrich(span, result)
                return result
        return synchronous
    return decorate
