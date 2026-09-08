from __future__ import annotations

import json

import pytest

from seocho.eval.agentic_os_artifacts import canonical_digest
from seocho.eval.agentic_os_treatments import (
    PreparedQuery,
    SemanticBundleAdmission,
    SeochoTreatmentExecutor,
    TreatmentAdmissionError,
    build_treatment_manifest,
)
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.operating_layer import SeochoOS


WORKSPACE = "grbench-physics"
CYPHER = (
    "MATCH (n:GRBenchNode {_workspace_id: $workspace_id}) "
    "RETURN n.id AS id LIMIT $limit"
)


def _query() -> PreparedQuery:
    return PreparedQuery(
        query_id="q1",
        cypher=CYPHER,
        params={"workspace_id": WORKSPACE, "limit": 2},
        workspace_id=WORKSPACE,
    )


def _manifest(arm: str, **kwargs):
    return build_treatment_manifest(
        arm=arm,
        workspace_id=WORKSPACE,
        graph_digest="graph-sha",
        prepared_queries=[_query()],
        tool_schema_digest="tool-sha",
        policy={"row_cap": 2, "token_budget": 10},
        **kwargs,
    )


class _Store:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def query(self, cypher, *, params=None, database=None, workspace_id=None,
              enforce_workspace_filter=False):
        self.calls.append(
            {
                "cypher": cypher,
                "params": dict(params or {}),
                "database": database,
                "workspace_id": workspace_id,
                "enforced": enforce_workspace_filter,
            }
        )
        return [{"id": "n1"}]


def _ontology() -> Ontology:
    return Ontology(
        name="grbench_projection",
        nodes={
            "GRBenchNode": NodeDef(
                properties={"id": P(str, unique=True), "node_type": P(str)}
            )
        },
        relationships={
            "GRBENCH_REL": RelDef(
                source="GRBenchNode",
                target="GRBenchNode",
                properties={"relation_name": P(str)},
            )
        },
    )


def _semantic(**overrides) -> SemanticBundleAdmission:
    values = {
        "workspace_id": WORKSPACE,
        "graph_digest": "graph-sha",
        "taxonomy_digest": "taxonomy-sha",
        "taxonomy_version": "1.0.0",
        "active_taxonomy_digest": "taxonomy-sha",
        "ontology_fingerprint": "ontology-sha",
        "discovery_cache_digest": "discovery-sha",
        "mapping_status": "curated",
        "mapped_node_labels": ("GRBenchNode",),
        "mapped_relationship_types": ("GRBENCH_REL",),
    }
    values.update(overrides)
    return SemanticBundleAdmission(**values)


def test_prepared_query_requires_real_workspace_scope() -> None:
    with pytest.raises(ValueError, match="prepared params"):
        PreparedQuery("q", CYPHER, {"workspace_id": "other"}, WORKSPACE)
    with pytest.raises(ValueError, match="workspace_id"):
        PreparedQuery("q", "MATCH (n) RETURN n LIMIT 1", {"workspace_id": WORKSPACE}, WORKSPACE)


def test_s2a_is_observation_only_and_preserves_direct_inputs() -> None:
    query = _query()
    seen: dict = {}

    def direct(cypher, params, database):
        seen.update({"cypher": cypher, "params": params, "database": database})
        return [{"id": "n1"}]

    run = SeochoTreatmentExecutor(
        manifest=_manifest("S2a"), database="neo4j", direct_executor=direct
    ).execute(query, prompt_tokens=5, completion_tokens=3)
    assert seen == {"cypher": query.cypher, "params": query.params, "database": "neo4j"}
    assert run.rows == ({"id": "n1"},)
    assert run.receipt["prompt_tokens_charged"] == 0
    assert run.receipt["completion_tokens_charged"] == 0
    assert "cypher" not in run.receipt and "params" not in run.receipt


def test_s2b_applies_policy_workspace_pin_and_budget() -> None:
    store = _Store()
    os_layer = SeochoOS(
        ontology=_ontology(), graph_store=store, database="neo4j",
        workspace_id=WORKSPACE, token_budget=10, row_cap=2, max_inflight=1,
    )
    session = os_layer.session("case-q1")
    decisions: list[tuple[str, str]] = []

    def policy_check(*, action: str, workspace_id: str) -> None:
        decisions.append((action, workspace_id))

    executor = SeochoTreatmentExecutor(
        manifest=_manifest("S2b"), database="neo4j", os_layer=os_layer,
        os_session=session, policy_check=policy_check,
    )
    accepted = executor.execute(_query(), prompt_tokens=4, completion_tokens=4)
    assert accepted.receipt["terminal_status"] == "ok"
    assert decisions == [("run_agent", WORKSPACE), ("run_agent", WORKSPACE)]
    assert store.calls[0]["params"]["workspace_id"] == WORKSPACE
    denied = executor.execute(_query(), prompt_tokens=3)
    assert denied.receipt["terminal_status"] == "rejected"
    assert denied.receipt["error_class"] == "BudgetExceededError"


def test_governed_arm_fails_before_cohort_when_policy_rejects_workspace() -> None:
    store = _Store()
    os_layer = SeochoOS(
        ontology=_ontology(), graph_store=store, database="neo4j",
        workspace_id=WORKSPACE, token_budget=10, row_cap=2,
    )

    def deny(**_):
        raise PermissionError("not admitted")

    with pytest.raises(TreatmentAdmissionError, match="policy admission failed"):
        SeochoTreatmentExecutor(
            manifest=_manifest("S2b"), database="neo4j", os_layer=os_layer,
            os_session=os_layer.session("denied"), policy_check=deny,
        )


def test_s3_replays_exact_prepared_query_through_seocho_store() -> None:
    store = _Store()
    query = _query()
    run = SeochoTreatmentExecutor(
        manifest=_manifest("S3"), database="neo4j", graph_store=store
    ).execute(query)
    assert run.receipt["terminal_status"] == "ok"
    assert store.calls == [
        {
            "cypher": query.cypher,
            "params": dict(query.params),
            "database": "neo4j",
            "workspace_id": None,
            "enforced": True,
        }
    ]


def test_s4_fails_closed_without_curated_active_semantic_bundle() -> None:
    with pytest.raises(TreatmentAdmissionError, match="semantic bundle"):
        _manifest("S4", ontology_fingerprint="ontology-sha")
    with pytest.raises(TreatmentAdmissionError, match="activation is stale"):
        _manifest(
            "S4",
            ontology_fingerprint="ontology-sha",
            semantic_admission=_semantic(active_taxonomy_digest="old-sha"),
        )


def test_s4_executes_only_with_digest_bound_bundle() -> None:
    semantic = _semantic()
    manifest = _manifest(
        "S4", ontology_fingerprint="ontology-sha", semantic_admission=semantic
    )
    store = _Store()
    os_layer = SeochoOS(
        ontology=_ontology(), graph_store=store, database="neo4j",
        workspace_id=WORKSPACE, token_budget=100, row_cap=2, max_inflight=1,
    )
    run = SeochoTreatmentExecutor(
        manifest=manifest,
        database="neo4j",
        os_layer=os_layer,
        os_session=os_layer.session("s4-q1"),
        policy_check=lambda **_: None,
        semantic_admission=semantic,
    ).execute(_query(), prompt_tokens=2, completion_tokens=2)
    assert run.receipt["terminal_status"] == "ok"
    assert run.receipt["semantic_bundle_digest"] == semantic.bundle_digest

    tampered = {**manifest, "graph_digest": "different"}
    with pytest.raises(TreatmentAdmissionError, match="manifest digest mismatch"):
        SeochoTreatmentExecutor(
            manifest=tampered,
            database="neo4j",
            os_layer=os_layer,
            os_session=os_layer.session("tampered"),
            policy_check=lambda **_: None,
            semantic_admission=semantic,
        )


def test_receipts_carry_digests_not_prepared_content() -> None:
    run = SeochoTreatmentExecutor(
        manifest=_manifest("S2a"),
        database="neo4j",
        direct_executor=lambda *_: [{"private": "answer"}],
    ).execute(_query())
    encoded = json.dumps(run.receipt, sort_keys=True)
    assert CYPHER not in encoded
    assert "answer" not in encoded
    assert run.receipt["result_digest"] == canonical_digest(({"private": "answer"},))
