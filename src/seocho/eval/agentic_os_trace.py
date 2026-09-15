"""W3C trace context for AgenticOS experiment cases.

One benchmark case is one trace.  The trace id is derived deterministically
from ``(run_id, case_id)`` so that spans exported later from receipts, spans
vLLM exports itself (``--otlp-traces-endpoint``) and any Agents SDK processor
all land in the same trace without a shared live tracer.  Every outbound
chat-completion request carries a fresh child span id in a ``traceparent``
header, and that span id is written into the request's receipt entry so the
receipt → OTLP exporter (:mod:`seocho.eval.agentic_os_otel`) reuses it.

Nothing here reads prompts or answers; identifiers only.
"""
from __future__ import annotations

import contextvars
import hashlib
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, MutableMapping

TRACEPARENT_HEADER = "traceparent"
_SAMPLED = "01"


def case_trace_id(run_id: str, case_id: str) -> str:
    """32-hex W3C trace id, stable for a (run, case) pair."""
    return hashlib.sha256(f"{run_id}\x1f{case_id}".encode("utf-8")).hexdigest()[:32]


def derived_span_id(*parts: Any) -> str:
    """16-hex span id derived from identifiers (for receipts that recorded none)."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def new_span_id() -> str:
    return secrets.token_hex(8)


def traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-{_SAMPLED}"


@dataclass(frozen=True)
class CaseTrace:
    run_id: str
    case_id: str
    trace_id: str
    root_span_id: str

    def receipt(self) -> dict[str, str]:
        return {
            "schema_version": "seocho.agentic_os_trace.v1",
            "trace_id": self.trace_id,
            "root_span_id": self.root_span_id,
            "propagation": "w3c_traceparent",
        }


_CURRENT: contextvars.ContextVar[CaseTrace | None] = contextvars.ContextVar("seocho_agentic_os_case_trace", default=None)


def current_case_trace() -> CaseTrace | None:
    return _CURRENT.get()


@contextmanager
def case_trace(run_id: str, case_id: str) -> Iterator[CaseTrace]:
    """Bind one case's trace for the duration of its episode."""
    trace = CaseTrace(
        run_id=str(run_id),
        case_id=str(case_id),
        trace_id=case_trace_id(str(run_id), str(case_id)),
        root_span_id=derived_span_id(run_id, case_id, "case"),
    )
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)


def inject_traceparent(headers: MutableMapping[str, Any]) -> str | None:
    """Add a ``traceparent`` for a new child span of the current case; return its span id.

    Returns ``None`` (and leaves ``headers`` untouched) when no case trace is
    bound, so library callers outside an experiment stay unaffected.
    """
    trace = _CURRENT.get()
    if trace is None:
        return None
    span_id = new_span_id()
    headers[TRACEPARENT_HEADER] = traceparent(trace.trace_id, span_id)
    return span_id
