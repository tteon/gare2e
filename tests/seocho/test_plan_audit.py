from seocho.eval.plan_audit import (
    audit_profile,
    compare_plans,
    emit_plan_comparison_metrics,
)
from seocho.metrics import ProductionMetrics


class _Instrument:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append((value, attributes))

    record = add
    set = add


class _Meter:
    def __init__(self):
        self.instruments = {}

    def _make(self, name, **_):
        self.instruments[name] = _Instrument()
        return self.instruments[name]

    create_counter = _make
    create_up_down_counter = _make
    create_histogram = _make
    create_gauge = _make


def test_profile_audit_attributes_scan_expand_and_cardinality_error() -> None:
    plan = {
        "operatorType": "ProduceResults",
        "args": {
            "Rows": 1,
            "EstimatedRows": 1,
            "DbHits": 0,
            "PageCacheHits": 4,
            "PageCacheMisses": 1,
        },
        "children": [
            {
                "operatorType": "EagerAggregation",
                "args": {"Rows": 1, "EstimatedRows": 1, "DbHits": 2},
                "children": [
                    {
                        "operatorType": "VarLengthExpand(All)",
                        "args": {"Rows": 500, "EstimatedRows": 5, "DbHits": 900},
                        "children": [
                            {
                                "operatorType": "AllNodesScan",
                                "args": {"Rows": 1000, "EstimatedRows": 1000, "DbHits": 1001},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    audit = audit_profile(plan)
    assert audit.findings == (
        "cardinality_misestimation",
        "eager_aggregation",
        "global_node_scan",
        "variable_length_expansion",
    )
    assert audit.total_db_hits == 1903
    assert audit.total_page_cache_hits is None  # child counters were not collected
    assert audit.total_page_cache_misses is None
    assert len(audit.fingerprint) == 16


def test_plan_comparison_requires_semantic_parity_before_promotion() -> None:
    audit = audit_profile(
        {"operatorType": "NodeUniqueIndexSeek", "args": {"Rows": 1, "DbHits": 2}, "children": []}
    )
    faster_but_wrong = compare_plans(
        audit,
        audit,
        baseline_p95_ms=60,
        candidate_p95_ms=2,
        baseline_result_hashes=("expected",),
        candidate_result_hashes=("different",),
    )
    assert faster_but_wrong.speedup == 30
    assert faster_but_wrong.semantic_parity is False
    assert faster_but_wrong.promotable is False


def test_plan_comparison_reports_db_hit_reduction() -> None:
    baseline = audit_profile(
        {"operatorType": "AllNodesScan", "args": {"Rows": 100, "DbHits": 100}, "children": []}
    )
    candidate = audit_profile(
        {"operatorType": "NodeUniqueIndexSeek", "args": {"Rows": 1, "DbHits": 5}, "children": []}
    )
    comparison = compare_plans(
        baseline,
        candidate,
        baseline_p95_ms=50,
        candidate_p95_ms=5,
        baseline_result_hashes=("same",),
        candidate_result_hashes=("same",),
    )
    assert comparison.db_hits_reduction == 0.95
    assert comparison.promotable is True


def test_plan_comparison_emits_bounded_aggregate_metrics(monkeypatch) -> None:
    import seocho.metrics as metrics_module

    baseline = audit_profile(
        {"operatorType": "AllNodesScan", "args": {"Rows": 100, "DbHits": 100}, "children": []}
    )
    candidate = audit_profile(
        {"operatorType": "NodeUniqueIndexSeek", "args": {"Rows": 1, "DbHits": 5}, "children": []}
    )
    comparison = compare_plans(
        baseline,
        candidate,
        baseline_p95_ms=50,
        candidate_p95_ms=5,
        baseline_result_hashes=("same",),
        candidate_result_hashes=("same",),
    )
    meter = _Meter()
    monkeypatch.setattr(metrics_module, "_metrics", ProductionMetrics(meter))
    emit_plan_comparison_metrics(comparison, cohort="observed_bitcoin")
    assert meter.instruments["seocho.query.plan.speedup"].calls == [
        (10.0, {"cohort": "observed_bitcoin"})
    ]
    assert meter.instruments["seocho.query.plan.db_hits_reduction"].calls == [
        (0.95, {"cohort": "observed_bitcoin"})
    ]
    assert meter.instruments["seocho.query.plan.finding.count"].calls == [
        (1, {"variant": "baseline", "finding": "global_node_scan"})
    ]


def test_driver_counters_topology_and_unknown_are_preserved() -> None:
    audit = audit_profile({"operatorType": "Filter", "args": {"EstimatedRows": 5},
        "rows": 0, "dbHits": 0, "pageCacheHits": 0, "pageCacheMisses": 0,
        "children": [{"operatorType": "Expand(All)", "args": {"EstimatedRows": 10},
                      "rows": 100000, "dbHits": 200000, "pageCacheHits": 15, "pageCacheMisses": 2}]})
    assert audit.total_db_hits == 200000
    assert audit.total_page_cache_misses == 2
    assert audit.operators[1].parent_id == audit.operators[0].operator_id
    assert audit.operators[1].cardinality_error > 9
    assert "cardinality_explosion" in audit.findings
    missing = audit_profile({"operatorType": "ProduceResults", "args": {}})
    assert missing.total_db_hits is None
    assert missing.to_dict()["counter_coverage"]["db_hits"] == {"known": 0, "operators": 1}


def test_zero_actual_cardinality_and_invalid_counters() -> None:
    audit = audit_profile({"operatorType": "Filter", "args": {"EstimatedRows": 100},
                          "rows": 0, "dbHits": float("nan"), "pageCacheHits": -1})
    assert "cardinality_misestimation" in audit.findings
    assert audit.total_db_hits is None
    assert audit.total_page_cache_hits is None
