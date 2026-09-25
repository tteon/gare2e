"""Opt-in query attempt evidence at the existing GRBench execution boundary.

Private replay inputs are separate from shareable numeric receipts. Cache reuse
is an explicit ablation, never silently enabled by the observation flag.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def append_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


class QueryEvidence:
    """One synchronous agent session; callers must not share across concurrent tasks."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        snapshot_id: str,
        mode: str = "raw",
        private_replay: Path | None = None,
        cache_entries: int = 64,
        cache_bytes: int = 1_000_000,
    ) -> None:
        if mode not in {"raw", "exact_result_reuse"}:
            raise ValueError(
                "Unsupported intervention; do not label unimplemented modes as measured"
            )
        if mode != "raw" and not snapshot_id:
            raise ValueError("Reuse requires an explicitly immutable graph snapshot")
        if cache_entries < 1 or cache_bytes < 1:
            raise ValueError("Cache bounds must be positive")
        self.path, self.run_id, self.snapshot_id = path, run_id, snapshot_id
        self.mode, self.private_replay = mode, private_replay
        self.cache_entries, self.cache_bytes = cache_entries, cache_bytes
        self.cache: OrderedDict[str, tuple[Any, str, int]] = OrderedDict()
        self.context: dict[str, Any] = {}
        self.active: list[dict[str, Any]] = []

    def configuration(self) -> dict[str, Any]:
        """Content-free measurement contract for the runner manifest."""
        return {
            "enabled": True,
            "schema_version": "gare2e.query_attempt.v1",
            "mode": self.mode,
            "snapshot_id": self.snapshot_id or None,
            "cache_entries": self.cache_entries,
            "cache_bytes": self.cache_bytes,
            "private_replay_enabled": self.private_replay is not None,
        }

    def session(
        self,
        case_id: str,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.cache.clear()
        self.context = {
            "case_id": case_id,
            "session_id": session_id or uuid.uuid4().hex,
            "trace_id": trace_id,
            "tool_call_id": None,
            "turn_id": None,
        }

    def tool(self, tool_call_id: str, turn_id: str | int) -> None:
        self.context.update(tool_call_id=tool_call_id, turn_id=str(turn_id))

    def submission(self) -> None:
        if self.active:
            self.active[-1]["db_queries_executed"] += 1

    def invoke(
        self,
        owner: Any,
        method: Callable[..., Any],
        query: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if not self.context:
            raise ValueError("Query evidence requires a session identity")
        query_id = uuid.uuid4().hex
        begin, unix = time.monotonic_ns(), time.time_ns()
        parameters = args[0] if args else kwargs.get("parameters")
        bound = {**dict(parameters or {}), "workspace_id": owner.workspace_id}
        scope = (
            "diagnostic_replay"
            if method.__name__ == "profile_select"
            else "auxiliary_query"
            if method.__name__ == "_diagnostic_rows"
            else "timed_execution"
        )
        key = digest(
            [
                self.context["session_id"],
                self.snapshot_id,
                owner.database,
                owner.workspace_id,
                method.__name__,
                query,
                bound,
                owner.row_cap,
                owner.byte_cap,
                owner.zero_row_hint,
            ]
        )
        event = {
            "schema_version": "gare2e.query_attempt.v1",
            **self.context,
            "run_id": self.run_id,
            "workspace_id": owner.workspace_id,
            "snapshot_id": self.snapshot_id or None,
            "mode": self.mode,
            "query_id": query_id,
            "query_digest": hashlib.sha256(query.encode()).hexdigest(),
            "parameter_digest": digest(bound),
            "scope": scope,
            "started_monotonic_ns": begin,
            "started_unix_ns": unix,
            "clock_domain": "process_monotonic",
            "outcome": "error",
            "error_class": None,
            "db_queries_executed": 0,
            "reused_from": None,
            "receipt": None,
            "plan": None,
        }
        event["parent_query_id"] = self.active[-1]["query_id"] if self.active else None
        self.active.append(event)
        try:
            # Validation still runs on hits. Cache requires explicit snapshot and full result.
            owner.validate(query) if method.__name__ != "_diagnostic_rows" else None
            cacheable = (
                self.mode == "exact_result_reuse"
                and method.__name__ == "execute_rows"
                and not re.search(
                    r"\b(rand|randomUUID|datetime|timestamp|date|time|localdatetime|localtime|CALL)\b",
                    query,
                    re.I,
                )
            )
            cached = self.cache.get(key) if cacheable else None
            if cached is not None:
                result, source_id, _ = copy.deepcopy(cached)
                self.cache.move_to_end(key)
                text, receipt = result
                receipt = {
                    "rows": receipt["rows"],
                    "result_bytes": receipt["result_bytes"],
                    "truncated": False,
                    "cache_hit": True,
                    "wall_ms": (time.monotonic_ns() - begin) / 1e6,
                }
                result = (text, receipt)
                event.update(outcome="reused", reused_from=source_id, receipt=receipt)
            else:
                result = method(owner, query, *args, **kwargs)
                receipt = (
                    result[1]
                    if isinstance(result, tuple)
                    else getattr(result, "receipt", None)
                )
                if method.__name__ == "profile_select":
                    event["plan"] = result["plan"]
                    receipt = {
                        k: result.get(k)
                        for k in (
                            "wall_ms",
                            "server_available_ms",
                            "server_consumed_ms",
                            "actual_runtime",
                        )
                    }
                event.update(outcome="ok", receipt=receipt)
                if (
                    cacheable
                    and receipt
                    and not receipt.get("truncated")
                    and receipt.get("rows", owner.row_cap) < owner.row_cap
                    and not owner.zero_row_hint
                ):
                    size = len(result[0].encode())
                    if size <= self.cache_bytes:
                        self.cache[key] = (copy.deepcopy(result), query_id, size)
                        while (
                            len(self.cache) > self.cache_entries
                            or sum(v[2] for v in self.cache.values()) > self.cache_bytes
                        ):
                            self.cache.popitem(last=False)
            if isinstance(result, tuple) and isinstance(result[1], dict):
                result[1].update(
                    query_id=query_id,
                    db_queries_executed=event["db_queries_executed"],
                    reused_from=event["reused_from"],
                    optimization_mode=self.mode,
                )
            return result
        except Exception as exc:
            event["error_class"] = type(exc).__name__
            raise
        finally:
            event.update(
                ended_monotonic_ns=time.monotonic_ns(),
                db_queries_executed=event["db_queries_executed"],
            )
            event["latency_ms"] = (event["ended_monotonic_ns"] - begin) / 1e6
            self.active.pop()
            append_private(self.path, event)
            if self.private_replay:
                append_private(
                    self.private_replay,
                    {
                        "query_id": query_id,
                        "query": query,
                        "parameters": bound,
                        "snapshot_id": self.snapshot_id,
                        "scope": scope,
                    },
                )


def observed(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def wrapped(owner: Any, query: str, *args: Any, **kwargs: Any) -> Any:
        observer = getattr(owner, "query_evidence", None)
        return (
            observer.invoke(owner, method, query, *args, **kwargs)
            if observer
            else method(owner, query, *args, **kwargs)
        )

    return wrapped


def from_environment(run_id: str) -> QueryEvidence | None:
    directory = os.environ.get("GARE2E_QUERY_EVIDENCE_DIR")
    if not directory:
        return None
    # Each process/run gets its own append-only file; no concurrent JSONL writes.
    stem = digest([run_id, os.getpid(), uuid.uuid4().hex])[:24]
    return QueryEvidence(
        Path(directory) / f"{stem}.queries.jsonl",
        run_id=run_id,
        snapshot_id=os.environ.get("GARE2E_GRAPH_SNAPSHOT", ""),
        mode=os.environ.get("GARE2E_QUERY_MODE", "raw"),
        private_replay=Path(directory) / f"{stem}.private.jsonl"
        if os.environ.get("GARE2E_PRIVATE_REPLAY") == "1"
        else None,
    )
