"""Bounded, content-free PyOxigraph runtime receipts for GTSQA.

This module is deliberately a benchmark executor rather than request-time
SEOCHO ontology reasoning.  It gives the Text2SPARQL lane a live, local RDF
engine and makes its execution boundaries observable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .gtsqa import GTSQACase, render_gtsqa_nquads


_FORBIDDEN = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|MOVE|COPY|ADD|SERVICE)\b",
    re.I,
)
_SELECT = re.compile(r"^\s*SELECT\b", re.I)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OxigraphQueryResult:
    """Raw rows for private evaluation plus a content-free timing receipt."""

    rows: tuple[dict[str, str], ...]
    receipt: dict[str, Any]


class GTSQAOxigraphExecutor:
    """One case-scoped in-memory Oxigraph store with SELECT-only execution."""

    def __init__(self, case: GTSQACase, *, row_cap: int = 100, byte_cap: int = 24_000) -> None:
        if row_cap < 1 or byte_cap < 1:
            raise ValueError("row_cap and byte_cap must be positive")
        self.case = case
        self.row_cap = row_cap
        self.byte_cap = byte_cap
        from pyoxigraph import RdfFormat, Store

        self._store = Store()
        nquads, _ = render_gtsqa_nquads([case])
        started = time.perf_counter()
        cpu_started = time.thread_time()
        self._store.load(input=nquads.encode("utf-8"), format=RdfFormat.N_QUADS)
        self.load_ms = round((time.perf_counter() - started) * 1000, 3)
        self.load_cpu_ms = round((time.thread_time() - cpu_started) * 1000, 3)
        self.graph_digest = _digest(nquads)

    def execute_select(self, query: str) -> OxigraphQueryResult:
        """Execute a bounded SELECT and return content-free instrumentation."""
        started = time.perf_counter()
        normalized = query.strip()
        validate_started = time.perf_counter()
        validate_cpu_started = time.thread_time()
        if not _SELECT.match(normalized) or _FORBIDDEN.search(normalized):
            raise ValueError("only read-only SELECT SPARQL is allowed")
        validate_ms = round((time.perf_counter() - validate_started) * 1000, 3)
        validate_cpu_ms = round((time.thread_time() - validate_cpu_started) * 1000, 3)
        execute_started = time.perf_counter()
        execute_cpu_started = time.thread_time()
        # Parsing and planning are internal to Store.query; this timestamp is
        # therefore labelled store_submit rather than a falsely precise parser
        # duration.
        try:
            results = self._store.query(normalized, use_default_graph_as_union=True)
        except Exception as error:
            # PyOxigraph exposes parser/evaluator failures through engine-
            # specific exception classes (including its own SyntaxError).
            # Normalize them at the executor boundary so an agent can repair
            # the query without crashing the benchmark process or retaining
            # query/error text in the public receipt.
            raise RuntimeError("SPARQL store rejected query") from error
        store_submit_ms = round((time.perf_counter() - execute_started) * 1000, 3)
        store_submit_cpu_ms = round((time.thread_time() - execute_cpu_started) * 1000, 3)
        variables = tuple(results.variables)
        iterate_started = time.perf_counter()
        iterate_cpu_started = time.thread_time()
        rows: list[dict[str, str]] = []
        first_result_ms: float | None = None
        for solution in results:
            if first_result_ms is None:
                first_result_ms = round((time.perf_counter() - iterate_started) * 1000, 3)
            row = {
                str(variable): str(solution[variable])
                for variable in variables
                if solution[variable] is not None
            }
            rows.append(row)
            if len(rows) >= self.row_cap:
                break
        iterate_ms = round((time.perf_counter() - iterate_started) * 1000, 3)
        iterate_cpu_ms = round((time.thread_time() - iterate_cpu_started) * 1000, 3)
        serialize_started = time.perf_counter()
        serialize_cpu_started = time.thread_time()
        encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        truncated = len(encoded) > self.byte_cap
        if truncated:
            # Do not return a partial JSON structure to an agent. The caller
            # receives a deterministic bounded prefix of complete rows instead.
            while rows and len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) > self.byte_cap:
                rows.pop()
            encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        serialize_ms = round((time.perf_counter() - serialize_started) * 1000, 3)
        serialize_cpu_ms = round((time.thread_time() - serialize_cpu_started) * 1000, 3)
        receipt = {
            "schema_version": "agentic_os.oxigraph_select_receipt.v1",
            "workspace_id": self.case.workspace_id,
            "case_id": self.case.case_id,
            "graph_digest": self.graph_digest,
            "query_digest": _digest(normalized),
            "load_ms": self.load_ms,
            "load_cpu_ms": self.load_cpu_ms,
            "validation_ms": validate_ms,
            "validation_cpu_ms": validate_cpu_ms,
            "store_submit_ms": store_submit_ms,
            "store_submit_cpu_ms": store_submit_cpu_ms,
            "first_result_ms": first_result_ms,
            "iterate_ms": iterate_ms,
            "iterate_cpu_ms": iterate_cpu_ms,
            "serialize_ms": serialize_ms,
            "serialize_cpu_ms": serialize_cpu_ms,
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "rows": len(rows),
            "result_bytes": len(encoded),
            "truncated": truncated,
            "engine": "pyoxigraph",
        }
        return OxigraphQueryResult(rows=tuple(rows), receipt=receipt)


class SeochoOxigraphExecutor:
    """SEOCHO-managed adapter around the same case-scoped Oxigraph engine.

    It intentionally does not add ontology reasoning to the request path. The
    adapter owns workspace-policy admission and emits its overhead separately;
    S3 can disable policy to isolate the adapter/transport path, while S4
    enables it as part of the complete governed package.
    """

    def __init__(
        self,
        case: GTSQACase,
        *,
        policy_check: Callable[..., None] | None = None,
        row_cap: int = 100,
        byte_cap: int = 24_000,
    ) -> None:
        self.case = case
        self._policy_check = policy_check
        self._delegate = GTSQAOxigraphExecutor(
            case,
            row_cap=row_cap,
            byte_cap=byte_cap,
        )

    def execute_select(self, query: str) -> OxigraphQueryResult:
        policy_ms: float | None = None
        if self._policy_check is not None:
            started = time.perf_counter()
            self._policy_check(
                action="run_agent",
                workspace_id=self.case.workspace_id,
            )
            policy_ms = round((time.perf_counter() - started) * 1000, 3)
        result = self._delegate.execute_select(query)
        return OxigraphQueryResult(
            rows=result.rows,
            receipt={
                **result.receipt,
                "execution_surface": "seocho_oxigraph_adapter",
                "policy_ms": policy_ms,
            },
        )


__all__ = ["GTSQAOxigraphExecutor", "OxigraphQueryResult", "SeochoOxigraphExecutor"]
