"""Content-free DozerDB execution boundary for GRBench agent experiments."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from collections.abc import Mapping
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


class QueryPolicyError(ValueError):
    """Content-free policy rejection; ``code`` is the agent's repair code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Properties Text2Cypher may anchor a pattern on.  Each has a lookup index
# (GRBENCH_INDEX_STATEMENTS), so a query filtering on one of them never
# degenerates into a NodeByLabelScan over 2.7 M nodes (lab-s3/s5/s8: 43–63 s
# calls that returned one row).  ``node_type`` is deliberately absent: three
# distinct values do not narrow a scan.
ANCHOR_PROPERTIES = ("id", "source_node_id", "feature_name", "feature_title")

# Index contract for the GRBench projection: created by load_grbench_dozerdb.py
# (import and verify-only modes), embedded in the exporter manifest for
# provenance, and asserted ONLINE by the admission gate.
GRBENCH_INDEX_STATEMENTS = (
    "CREATE INDEX grbench_node_lookup IF NOT EXISTS FOR (n:GRBenchNode) ON (n.id, n._workspace_id)",
    "CREATE INDEX grbench_node_name IF NOT EXISTS FOR (n:GRBenchNode) ON (n.feature_name)",
    "CREATE INDEX grbench_node_title IF NOT EXISTS FOR (n:GRBenchNode) ON (n.feature_title)",
    "CREATE INDEX grbench_node_source_id IF NOT EXISTS FOR (n:GRBenchNode) ON (n.source_node_id)",
    "CREATE INDEX grbench_node_type_ws IF NOT EXISTS FOR (n:GRBenchNode) ON (n.node_type, n._workspace_id)",
    "CREATE TEXT INDEX grbench_node_name_text IF NOT EXISTS FOR (n:GRBenchNode) ON (n.feature_name)",
    "CREATE TEXT INDEX grbench_node_title_text IF NOT EXISTS FOR (n:GRBenchNode) ON (n.feature_title)",
    "CREATE INDEX grbench_rel_name IF NOT EXISTS FOR ()-[r:GRBENCH_REL]-() ON (r.relation_name)",
)
GRBENCH_INDEX_NAMES = tuple(stmt.split()[2] if stmt.split()[1] == "INDEX" else stmt.split()[3] for stmt in GRBENCH_INDEX_STATEMENTS)

_ANCHOR_INLINE = re.compile(r"\{[^}]*\b(" + "|".join(ANCHOR_PROPERTIES) + r")\s*:", re.I)
_ANCHOR_WHERE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\.(" + "|".join(ANCHOR_PROPERTIES) + r")\s*(=|<>|IN\b|CONTAINS\b|STARTS\s+WITH\b|ENDS\s+WITH\b|=~)",
    re.I,
)


def has_anchor_predicate(query: str) -> bool:
    """True when some node pattern is narrowed by an indexed property."""
    return bool(_ANCHOR_INLINE.search(query) or _ANCHOR_WHERE.search(query))


_WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|REMOVE|LOAD\s+CSV|FOREACH)\b", re.I)
_UNSAFE_CALL = re.compile(r"\bCALL\s+(?!\{)", re.I)
_READ_START = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|CALL)\b", re.I)


def validate_grbench_read_query(
    query: str,
    *,
    row_cap: int,
    require_evidence_columns: bool = True,
    require_anchor: bool = True,
) -> str:
    """Validate and cap a workspace-scoped GRBench read query.

    Raises :class:`QueryPolicyError` (a ``ValueError``) whose ``code`` is the
    agent-facing repair code:

    ``unsupported_query_shape`` (not a read query) · ``write_clause_forbidden``
    · ``unsafe_call_forbidden`` · ``missing_return`` ·
    ``missing_grbench_node_label`` · ``missing_workspace_scope`` ·
    ``missing_evidence_columns`` (s, p, o) · ``unanchored_scan`` (no indexed
    property predicate — the shape that produced 40–60 s label scans).
    """
    cleaned = query.strip().rstrip(";")
    if not _READ_START.match(cleaned):
        raise QueryPolicyError("unsupported_query_shape")
    if _WRITE.search(cleaned):
        raise QueryPolicyError("write_clause_forbidden")
    if _UNSAFE_CALL.search(cleaned):
        raise QueryPolicyError("unsafe_call_forbidden")
    if not re.search(r"\bRETURN\b", cleaned, re.I):
        raise QueryPolicyError("missing_return")
    node_patterns = len(re.findall(r":GRBenchNode\b", cleaned, flags=re.I))
    scope_patterns = len(re.findall(r"_workspace_id\s*:\s*\$workspace_id\b", cleaned, flags=re.I))
    if node_patterns == 0:
        raise QueryPolicyError("missing_grbench_node_label")
    if scope_patterns < node_patterns:
        raise QueryPolicyError("missing_workspace_scope")
    if require_evidence_columns:
        for name in ("s", "p", "o"):
            if not re.search(rf"\bAS\s+{name}\b", cleaned, flags=re.I):
                raise QueryPolicyError("missing_evidence_columns")
    if require_anchor and not has_anchor_predicate(cleaned):
        raise QueryPolicyError("unanchored_scan")
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
        require_evidence_columns: bool = True,
        require_anchor: bool = True,
    ) -> None:
        if query_timeout_s <= 0:
            raise ValueError("query_timeout_s must be positive")
        self.driver = driver
        self.database = database
        self.workspace_id = workspace_id
        self.row_cap = row_cap
        self.byte_cap = byte_cap
        self.query_timeout_s = float(query_timeout_s)
        self.require_evidence_columns = require_evidence_columns
        self.require_anchor = require_anchor

    def validate(self, query: str) -> str:
        return validate_grbench_read_query(
            query,
            row_cap=self.row_cap,
            require_evidence_columns=self.require_evidence_columns,
            require_anchor=self.require_anchor,
        )

    def execute_rows(self, query: str, parameters: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        """Run a bounded read and return (rows as JSON text, content-free receipt).

        The single-agent ReAct runner feeds the raw rows back to the model;
        unlike :meth:`execute_select` it does not require s/p/o columns.  Same
        validator, Bolt transaction timeout, caps and receipt fields.
        ``parameters`` are extra bound values for templated queries (the
        Graph-CoT hop tools); ``workspace_id`` is always bound by the executor
        and cannot be overridden.
        """
        started = time.perf_counter()
        statement = self.validate(query)
        validator_ms = (time.perf_counter() - started) * 1000
        bound = {**(dict(parameters) if parameters else {}), "workspace_id": self.workspace_id}
        pool_started = time.perf_counter()
        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            pool_acquire_ms = (time.perf_counter() - pool_started) * 1000
            submit_started = time.perf_counter()
            result = session.run(bolt_query(statement, timeout_s=self.query_timeout_s), **bound)
            bolt_submit_ms = (time.perf_counter() - submit_started) * 1000
            iterate_started = time.perf_counter()
            rows: list[dict[str, Any]] = []
            first_record_ms: float | None = None
            for record in result:
                if first_record_ms is None:
                    first_record_ms = (time.perf_counter() - iterate_started) * 1000
                rows.append(record.data())
                if len(rows) >= self.row_cap:
                    break
            hydrate_ms = (time.perf_counter() - iterate_started) * 1000
            summary = result.consume()
        serialize_started = time.perf_counter()
        text = json.dumps(rows, ensure_ascii=False, default=str)
        truncated = len(text.encode("utf-8")) > self.byte_cap
        if truncated:
            text = text.encode("utf-8")[: self.byte_cap].decode("utf-8", errors="ignore") + "…[truncated]"
        receipt = {
            "schema_version": "seocho.grbench_dozer_query_receipt.v1",
            "query_digest": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "validator_ms": round(validator_ms, 3),
            "pool_acquire_ms": round(pool_acquire_ms, 3),
            "bolt_submit_ms": round(bolt_submit_ms, 3),
            "first_record_ms": round(first_record_ms, 3) if first_record_ms is not None else None,
            "hydrate_ms": round(hydrate_ms, 3),
            "serialize_ms": round((time.perf_counter() - serialize_started) * 1000, 3),
            "server_available_ms": float(summary.result_available_after or 0),
            "server_consumed_ms": float(summary.result_consumed_after or 0),
            "query_timeout_s": self.query_timeout_s,
            "rows": len(rows),
            "result_bytes": len(text.encode("utf-8")),
            "truncated": truncated,
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        return text, receipt

    def execute_select(self, query: str) -> GRBenchQueryResult:
        started = time.perf_counter()
        validate_started = time.perf_counter()
        statement = self.validate(query)
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
        statement = self.validate(query)
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


__all__ = [
    "ANCHOR_PROPERTIES",
    "GRBENCH_INDEX_NAMES",
    "GRBENCH_INDEX_STATEMENTS",
    "GRBenchDozerExecutor",
    "GRBenchQueryResult",
    "QueryPolicyError",
    "bolt_query",
    "has_anchor_predicate",
    "validate_grbench_read_query",
]
