"""Content-free DozerDB execution boundary for GRBench agent experiments."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .plan_audit import audit_profile

try:  # the driver is a runtime dependency of the runners, not of the validator
    from neo4j import Query as _BoltQuery
except Exception:  # pragma: no cover - exercised only where neo4j is absent
    _BoltQuery = None  # type: ignore[assignment]


def bolt_query(text: str, *, timeout_s: float) -> Any:
    """Wrap Cypher so the server enforces a transaction timeout.

    ``Session.run(text, timeout=15)`` does *not* set a timeout: every keyword
    argument of ``run`` is a Cypher parameter, so ``$timeout`` was silently
    bound and GRBench scans ran 43–50 s unbounded (lab-s3/s5, bead seocho-dp9x).
    ``neo4j.Query(text, timeout=...)`` is the documented per-query control.
    """
    if _BoltQuery is None:
        return text
    return _BoltQuery(text, timeout=timeout_s)


_WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|LOAD\s+CSV|FOREACH)\b", re.I)
_UNSAFE_CALL = re.compile(r"\bCALL\s+(?!\{)", re.I)
_READ_START = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|CALL)\b", re.I)


def validate_grbench_read_query(query: str, *, row_cap: int, require_evidence_columns: bool = True) -> str:
    """Validate and cap a workspace-scoped GRBench read query."""
    cleaned = query.strip().rstrip(";")
    if (
        not _READ_START.match(cleaned)
        or _WRITE.search(cleaned)
        or _UNSAFE_CALL.search(cleaned)
        or not re.search(r"\bRETURN\b", cleaned, re.I)
    ):
        raise ValueError("only read-only Cypher with RETURN is allowed")
    node_patterns = len(re.findall(r":GRBenchNode\b", cleaned, flags=re.I))
    scope_patterns = len(re.findall(r"_workspace_id\s*:\s*\$workspace_id\b", cleaned, flags=re.I))
    if node_patterns == 0 or scope_patterns < node_patterns:
        raise ValueError("every GRBench node pattern must carry _workspace_id: $workspace_id")
    if require_evidence_columns:
        missing = [
            name
            for name in ("s", "p", "o")
            if not re.search(rf"\bAS\s+{name}\b", cleaned, flags=re.I)
        ]
        if missing:
            raise ValueError("query must return scalar evidence columns aliased as s, p, and o")
    if not re.search(r"\bLIMIT\b", cleaned, re.I):
        cleaned = f"{cleaned} LIMIT {row_cap}"
    return cleaned


@dataclass(frozen=True)
class GRBenchQueryResult:
    """Private evidence rows plus a content-free execution receipt."""

    rows: tuple[dict[str, str], ...]
    receipt: dict[str, Any]


class GRBenchDozerExecutor:
    """Execute bounded Text2Cypher queries through a caller-owned driver."""

    def __init__(
        self,
        driver: Any,
        *,
        database: str,
        workspace_id: str,
        row_cap: int = 20,
        byte_cap: int = 12_000,
        query_timeout_s: float = 15.0,
    ) -> None:
        if query_timeout_s <= 0:
            raise ValueError("query_timeout_s must be positive")
        self.driver = driver
        self.database = database
        self.workspace_id = workspace_id
        self.row_cap = row_cap
        self.byte_cap = byte_cap
        self.query_timeout_s = float(query_timeout_s)

    def execute_select(self, query: str) -> GRBenchQueryResult:
        started = time.perf_counter()
        validate_started = time.perf_counter()
        statement = validate_grbench_read_query(query, row_cap=self.row_cap)
        validator_ms = (time.perf_counter() - validate_started) * 1000
        pool_started = time.perf_counter()
        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            pool_acquire_ms = (time.perf_counter() - pool_started) * 1000
            submit_started = time.perf_counter()
            result = session.run(bolt_query(statement, timeout_s=self.query_timeout_s), workspace_id=self.workspace_id)
            bolt_submit_ms = (time.perf_counter() - submit_started) * 1000
            iterate_started = time.perf_counter()
            evidence_encode_ms = 0.0
            rows: list[dict[str, str]] = []
            private_bytes = 0
            first_record_ms: float | None = None
            truncated = False
            for record in result:
                if first_record_ms is None:
                    first_record_ms = (time.perf_counter() - iterate_started) * 1000
                raw = record.data()
                row = {name: str(raw[name]) for name in ("s", "p", "o") if name in raw}
                if len(row) != 3:
                    raise ValueError("query result omitted one of s, p, or o")
                encode_started = time.perf_counter()
                row_bytes = len(json.dumps(row, sort_keys=True).encode("utf-8"))
                evidence_encode_ms += (time.perf_counter() - encode_started) * 1000
                if private_bytes + row_bytes > self.byte_cap:
                    truncated = True
                    break
                rows.append(row)
                private_bytes += row_bytes
                if len(rows) >= self.row_cap:
                    truncated = True
                    break
            iterate_hydrate_encode_ms = (time.perf_counter() - iterate_started) * 1000
            consume_started = time.perf_counter()
            summary = result.consume()
            bolt_consume_ms = (time.perf_counter() - consume_started) * 1000
        receipt = {
            "schema_version": "seocho.grbench_dozer_query_receipt.v1",
            "query_digest": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "validator_ms": round(validator_ms, 3),
            "pool_acquire_ms": round(pool_acquire_ms, 3),
            "bolt_submit_ms": round(bolt_submit_ms, 3),
            "first_record_ms": round(first_record_ms, 3) if first_record_ms is not None else None,
            "iterate_serialize_ms": round(iterate_hydrate_encode_ms, 3),
            "bolt_iterate_hydrate_ms": round(max(0.0, iterate_hydrate_encode_ms - evidence_encode_ms), 3),
            "evidence_json_encode_ms": round(evidence_encode_ms, 3),
            "bolt_consume_ms": round(bolt_consume_ms, 3),
            "server_available_ms": float(summary.result_available_after or 0),
            "server_consumed_ms": float(summary.result_consumed_after or 0),
            "query_timeout_s": self.query_timeout_s,
            "rows": len(rows),
            "result_bytes": private_bytes,
            "truncated": truncated,
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        return GRBenchQueryResult(rows=tuple(rows), receipt=receipt)

    def profile_select(self, query: str) -> dict[str, Any]:
        """Replay one bounded read-only query under PROFILE outside the E2E path."""
        statement = validate_grbench_read_query(query, row_cap=self.row_cap)
        started = time.perf_counter()
        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            result = session.run(
                bolt_query(f"PROFILE {statement}", timeout_s=self.query_timeout_s),
                workspace_id=self.workspace_id,
            )
            summary = result.consume()
        if summary.profile is None:
            raise RuntimeError("DozerDB did not return a PROFILE plan")
        audit = audit_profile(summary.profile)
        return {
            "schema_version": "agentic_os.dozer_profile_replay.v1",
            "scope": "separate_read_only_diagnostic_replay",
            "query_digest": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "server_available_ms": float(summary.result_available_after or 0),
            "server_consumed_ms": float(summary.result_consumed_after or 0),
            "plan": {
                "fingerprint": audit.fingerprint,
                "operators": [
                    {
                        "operator": operator.operator,
                        "estimated_rows": operator.estimated_rows,
                        "actual_rows": operator.actual_rows,
                        "db_hits": operator.db_hits,
                        "page_cache_hits": operator.page_cache_hits,
                        "page_cache_misses": operator.page_cache_misses,
                    }
                    for operator in audit.operators
                ],
                "findings": list(audit.findings),
                "total_db_hits": audit.total_db_hits,
                "total_page_cache_hits": audit.total_page_cache_hits,
                "total_page_cache_misses": audit.total_page_cache_misses,
            },
        }


__all__ = ["GRBenchDozerExecutor", "GRBenchQueryResult", "validate_grbench_read_query"]
