"""GOpt-inspired execution-plan audit contracts.

This module does not claim to embed GOpt. It closes the operational loop around
SEOCHO's existing GOPTS ranking/latency layers: preserve a PROFILE snapshot,
identify the operator where work or cardinality diverges, apply a schema-aware
candidate rewrite, and compare the plan and latency without changing semantics.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PlanOperator:
    operator: str
    details: str = ""
    estimated_rows: float | None = None
    actual_rows: float | None = None
    db_hits: float | None = None
    page_cache_hits: float | None = None
    page_cache_misses: float | None = None

    operator_id: str = ""
    parent_id: str | None = None
    pipeline: str | None = None
    memory_bytes: float | None = None
    time_ns: float | None = None

    @property
    def cardinality_error(self) -> float | None:
        if self.actual_rows is None or self.estimated_rows is None:
            return None
        return abs(math.log((self.actual_rows + 1) / (self.estimated_rows + 1)))

    @property
    def cardinality_ratio(self) -> float | None:
        if self.actual_rows is None or self.estimated_rows is None:
            return None
        return self.actual_rows / max(self.estimated_rows, 1e-9)


@dataclass(frozen=True, slots=True)
class PlanAudit:
    fingerprint: str
    operators: tuple[PlanOperator, ...]
    findings: tuple[str, ...]
    total_db_hits: float | None
    total_page_cache_hits: float | None
    total_page_cache_misses: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "operators": [{**asdict(operator), "cardinality_error": operator.cardinality_error} for operator in self.operators],
            "counter_coverage": {key: {"known": sum(getattr(op, key) is not None for op in self.operators), "operators": len(self.operators)} for key in ("db_hits", "page_cache_hits", "page_cache_misses")},
            "findings": list(self.findings),
            "total_db_hits": self.total_db_hits,
            "total_page_cache_hits": self.total_page_cache_hits,
            "total_page_cache_misses": self.total_page_cache_misses,
        }


@dataclass(frozen=True, slots=True)
class PlanComparison:
    baseline: PlanAudit
    candidate: PlanAudit
    baseline_p95_ms: float
    candidate_p95_ms: float
    semantic_parity: bool

    @property
    def speedup(self) -> float:
        if self.candidate_p95_ms <= 0:
            return 0.0
        return self.baseline_p95_ms / self.candidate_p95_ms

    @property
    def db_hits_reduction(self) -> float | None:
        if self.baseline.total_db_hits is None or self.candidate.total_db_hits is None:
            return None
        if self.baseline.total_db_hits <= 0:
            return 0.0
        return 1.0 - self.candidate.total_db_hits / self.baseline.total_db_hits

    @property
    def promotable(self) -> bool:
        return self.semantic_parity and self.speedup > 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "baseline_p95_ms": self.baseline_p95_ms,
            "candidate_p95_ms": self.candidate_p95_ms,
            "speedup": self.speedup,
            "db_hits_reduction": self.db_hits_reduction,
            "semantic_parity": self.semantic_parity,
            "promotable": self.promotable,
        }


def _number(arguments: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = arguments.get(key)
        if value is None:
            continue
        try:
            number = float(value)
            return number if math.isfinite(number) and number >= 0 and not isinstance(value, bool) else None
        except (TypeError, ValueError):
            return None
    return None


def _flatten(plan: Any, path: str = "0", parent_id: str | None = None) -> Iterable[PlanOperator]:
    if isinstance(plan, Mapping):
        operator = str(plan.get("operatorType", plan.get("operator_type", "unknown")))
        arguments = plan.get("args", plan.get("arguments", {})) or {}
        children = plan.get("children", ()) or ()
        counters = {**arguments, **{k: v for k, v in plan.items() if k not in {"args", "arguments", "children"}}}
    else:
        operator = str(plan.operator_type)
        arguments = plan.arguments or {}
        children = plan.children or ()
        counters = {**arguments, **{key: getattr(plan, key) for key in ("rows", "db_hits", "page_cache_hits", "page_cache_misses") if hasattr(plan, key)}}
    yield PlanOperator(
        operator=operator,
        operator_id=path, parent_id=parent_id,
        pipeline=str(arguments["PipelineInfo"]) if "PipelineInfo" in arguments else None,
        memory_bytes=_number(counters, "Memory", "memory"),
        time_ns=_number(counters, "Time", "time"),
        details=str(arguments.get("Details", ""))[:240],
        estimated_rows=_number(arguments, "EstimatedRows", "estimatedRows"),
        actual_rows=_number(counters, "rows", "Rows"),
        db_hits=_number(counters, "dbHits", "db_hits", "DbHits", "DB Hits"),
        page_cache_hits=_number(
            counters,
            "PageCacheHits",
            "Page Cache Hits",
            "pageCacheHits", "page_cache_hits",
        ),
        page_cache_misses=_number(
            counters,
            "PageCacheMisses",
            "Page Cache Misses",
            "pageCacheMisses", "page_cache_misses",
        ),
    )
    for index, child in enumerate(children):
        yield from _flatten(child, f"{path}.{index}", path)


def audit_profile(plan: Any, *, cardinality_ratio_threshold: float = 10.0) -> PlanAudit:
    """Convert a Neo4j/DozerDB PROFILE plan into stable diagnostic evidence."""

    operators = tuple(_flatten(plan))
    names = tuple(operator.operator for operator in operators)
    findings: set[str] = set()
    if any("AllNodesScan" in name for name in names):
        findings.add("global_node_scan")
    if any("VarLengthExpand" in name for name in names):
        findings.add("variable_length_expansion")
    if any("EagerAggregation" in name for name in names):
        findings.add("eager_aggregation")
    if any("Sort" in name or "Top" in name for name in names):
        findings.add("sort_or_top")
    if any(
        ratio is not None
        and (ratio >= cardinality_ratio_threshold or 0 < ratio <= 1 / cardinality_ratio_threshold)
        for ratio in (operator.cardinality_ratio for operator in operators)
    ):
        findings.add("cardinality_misestimation")
    if any("CartesianProduct" in name for name in names):
        findings.add("cartesian_product")
    by_id = {op.operator_id: op for op in operators}
    for op in operators:
        if op.actual_rows is not None and op.actual_rows == 0 and op.estimated_rows and op.estimated_rows >= cardinality_ratio_threshold:
            findings.add("cardinality_misestimation")
        parent = by_id.get(op.parent_id or "")
        if parent and "Filter" in parent.operator and op.actual_rows is not None and parent.actual_rows is not None and op.actual_rows >= 1000 and op.actual_rows >= 10 * (parent.actual_rows + 1):
            findings.add("cardinality_explosion")
    def complete_sum(key: str) -> float | None:
        values = [getattr(op, key) for op in operators]
        # Missing/fused metrics cannot safely be interpreted as per-operator zeros.
        return sum(values) if values and all(v is not None for v in values) else None
    fingerprint_input = "|".join(
        f"{operator.operator}:{operator.details}" for operator in operators
    )
    return PlanAudit(
        fingerprint=hashlib.sha256(fingerprint_input.encode()).hexdigest()[:16],
        operators=operators,
        findings=tuple(sorted(findings)),
        total_db_hits=complete_sum("db_hits"),
        total_page_cache_hits=complete_sum("page_cache_hits"),
        total_page_cache_misses=complete_sum("page_cache_misses"),
    )


def compare_plans(
    baseline: PlanAudit,
    candidate: PlanAudit,
    *,
    baseline_p95_ms: float,
    candidate_p95_ms: float,
    baseline_result_hashes: Sequence[str],
    candidate_result_hashes: Sequence[str],
) -> PlanComparison:
    """Require result parity before a faster candidate can be promoted."""

    return PlanComparison(
        baseline=baseline,
        candidate=candidate,
        baseline_p95_ms=baseline_p95_ms,
        candidate_p95_ms=candidate_p95_ms,
        semantic_parity=tuple(baseline_result_hashes) == tuple(candidate_result_hashes),
    )


def emit_plan_comparison_metrics(
    comparison: PlanComparison,
    *,
    cohort: str,
) -> None:
    """Emit only bounded aggregate dimensions; detailed plans belong in traces."""

    from ..metrics import get_metrics

    metrics = get_metrics()
    if comparison.semantic_parity:
        metrics.record(
            "seocho.query.plan.speedup", comparison.speedup, {"cohort": cohort}
        )
        if comparison.db_hits_reduction is not None:
            metrics.record(
                "seocho.query.plan.db_hits_reduction",
                comparison.db_hits_reduction,
                {"cohort": cohort},
            )
    for variant, audit in (
        ("baseline", comparison.baseline),
        ("candidate", comparison.candidate),
    ):
        for finding in audit.findings:
            metrics.add(
                "seocho.query.plan.finding.count",
                attributes={"variant": variant, "finding": finding},
            )


__all__ = [
    "PlanAudit",
    "PlanComparison",
    "PlanOperator",
    "audit_profile",
    "compare_plans",
    "emit_plan_comparison_metrics",
]
