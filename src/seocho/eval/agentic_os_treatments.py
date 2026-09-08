"""Executable SEOCHO treatment contracts for the AgenticOS graph study.

The module deliberately stops at the query boundary.  Agent/model loops feed
usage into :class:`SeochoTreatmentExecutor`, while prepared-query replay calls
the same executor with zero usage.  This keeps the causal meaning of the arms
visible in code:

``S2a``
    content-free provenance only; the direct executor receives the exact query
    and parameter mapping supplied by the caller.
``S2b``
    runtime policy, workspace pinning, admission and token budget intervention
    through :class:`seocho.operating_layer.SeochoOS`.
``S3``
    no-LLM replay through :class:`seocho.store.graph.Neo4jGraphStore`, with no
    taxonomy or operating-layer intervention.
``S4``
    the S2b governed path plus an admitted, immutable semantic bundle.  Missing
    or stale semantic metadata fails closed rather than silently degrading to
    a schema-only treatment.

Receipts are content-free: query, parameters, rows, prompts and answers are
represented only by digests/counts.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence

from seocho.eval.agentic_os_artifacts import canonical_digest
from seocho.ontology import NodeDef, Ontology, P, RelDef
from seocho.store.graph import enforce_read_workspace_scope


ARM_SCHEMA_VERSION = "seocho.agentic_os.treatment_arm.v1"
RECEIPT_SCHEMA_VERSION = "seocho.agentic_os.treatment_receipt.v1"
SUPPORTED_ARMS = ("S2a", "S2b", "S3", "S4")


def grbench_projection_ontology() -> Ontology:
    """Lossless GRBench LPG projection contract used by every treatment."""
    return Ontology(
        name="grbench_projection",
        package_id="urn:seocho:grbench:projection",
        version="1.0.0",
        graph_model="lpg",
        nodes={
            "GRBenchNode": NodeDef(
                description="One source GRBench graph node.",
                properties={
                    "id": P(str, unique=True),
                    "node_type": P(str),
                    "source_node_id": P(str),
                    "feature_abstract": P(str),
                    "feature_label": P(str),
                    "feature_name": P(str),
                    "feature_title": P(str),
                    "feature_year": P(str),
                    "_workspace_id": P(str),
                },
            )
        },
        relationships={
            "GRBENCH_REL": RelDef(
                source="GRBenchNode",
                target="GRBenchNode",
                description="Source edge semantics are stored in relation_name.",
                properties={
                    "relation_name": P(str),
                    "source_node_type": P(str),
                    "_workspace_id": P(str),
                },
            )
        },
    )


class TreatmentAdmissionError(RuntimeError):
    """An arm cannot run without changing its registered interpretation."""


class DirectQueryExecutor(Protocol):
    """Stock-driver adapter used by the observation-only S2a arm."""

    def __call__(
        self, cypher: str, params: Mapping[str, Any], database: str
    ) -> Sequence[Mapping[str, Any]]: ...


class PolicyCheck(Protocol):
    """Runtime policy enforcement callback injected by the deployment shell."""

    def __call__(self, *, action: str, workspace_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    """One immutable, workspace-scoped query used for transport replay."""

    query_id: str
    cypher: str
    params: Mapping[str, Any]
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.cypher.strip() or not self.workspace_id.strip():
            raise ValueError("query_id, cypher, and workspace_id are required")
        if self.params.get("workspace_id") != self.workspace_id:
            raise ValueError("prepared params must pin the declared workspace_id")
        enforce_read_workspace_scope(self.cypher)
        limit = re.search(r"\bLIMIT\s+(\d+|\$[A-Za-z_][A-Za-z0-9_]*)\b", self.cypher, flags=re.I)
        if limit is None:
            raise ValueError("prepared transport queries must carry an explicit LIMIT")
        if limit.group(1).startswith("$"):
            parameter = limit.group(1)[1:]
            value = self.params.get(parameter)
            if not isinstance(value, int) or value < 1:
                raise ValueError("a parameterized LIMIT requires a positive integer parameter")

    @property
    def query_digest(self) -> str:
        return canonical_digest({"cypher": self.cypher})

    @property
    def params_digest(self) -> str:
        return canonical_digest(dict(self.params))

    @property
    def prepared_digest(self) -> str:
        return canonical_digest(
            {
                "query_id": self.query_id,
                "query_digest": self.query_digest,
                "params_digest": self.params_digest,
                "workspace_id": self.workspace_id,
            }
        )


def prepared_query_set_digest(queries: Sequence[PreparedQuery]) -> str:
    """Digest an ordered prepared-query cohort; duplicate IDs fail closed."""
    if not queries:
        raise ValueError("at least one prepared query is required")
    ids = [query.query_id for query in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("prepared query IDs must be unique")
    return canonical_digest([query.prepared_digest for query in queries])


def _result_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """Digest private driver values without placing their text in telemetry."""
    encoded = json.dumps(
        list(rows),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=lambda value: str(value),
    ).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticBundleAdmission:
    """S4 admission receipt binding graph, taxonomy and ontology versions."""

    workspace_id: str
    graph_digest: str
    taxonomy_digest: str
    taxonomy_version: str
    active_taxonomy_digest: str
    ontology_fingerprint: str
    discovery_cache_digest: str
    mapping_status: str
    mapped_node_labels: tuple[str, ...]
    mapped_relationship_types: tuple[str, ...]
    mapped_relation_names: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SemanticBundleAdmission":
        return cls(
            workspace_id=str(payload.get("workspace_id", "")),
            graph_digest=str(payload.get("graph_digest", "")),
            taxonomy_digest=str(payload.get("taxonomy_digest", "")),
            taxonomy_version=str(payload.get("taxonomy_version", "")),
            active_taxonomy_digest=str(payload.get("active_taxonomy_digest", "")),
            ontology_fingerprint=str(payload.get("ontology_fingerprint", "")),
            discovery_cache_digest=str(payload.get("discovery_cache_digest", "")),
            mapping_status=str(payload.get("mapping_status", "")),
            mapped_node_labels=tuple(str(v) for v in payload.get("mapped_node_labels", ())),
            mapped_relationship_types=tuple(
                str(v) for v in payload.get("mapped_relationship_types", ())
            ),
            mapped_relation_names=tuple(
                str(v) for v in payload.get("mapped_relation_names", ())
            ),
        )

    def validate(
        self,
        *,
        workspace_id: str,
        graph_digest: str,
        ontology_fingerprint: str,
        required_node_labels: Sequence[str] = ("GRBenchNode",),
        required_relationship_types: Sequence[str] = ("GRBENCH_REL",),
        required_relation_names: Sequence[str] = (),
    ) -> None:
        missing = [
            name
            for name in (
                "workspace_id",
                "graph_digest",
                "taxonomy_digest",
                "taxonomy_version",
                "active_taxonomy_digest",
                "ontology_fingerprint",
                "discovery_cache_digest",
            )
            if not str(getattr(self, name)).strip()
        ]
        reasons: list[str] = []
        if missing:
            reasons.append("missing fields: " + ", ".join(missing))
        if self.workspace_id != workspace_id:
            reasons.append("workspace mismatch")
        if self.graph_digest != graph_digest:
            reasons.append("graph digest mismatch")
        if self.taxonomy_digest != self.active_taxonomy_digest:
            reasons.append("taxonomy activation is stale")
        if self.ontology_fingerprint != ontology_fingerprint:
            reasons.append("ontology fingerprint mismatch")
        if self.mapping_status != "curated":
            reasons.append("semantic mapping is not curated")
        if not set(required_node_labels).issubset(self.mapped_node_labels):
            reasons.append("required node mapping is absent")
        if not set(required_relationship_types).issubset(self.mapped_relationship_types):
            reasons.append("required relationship mapping is absent")
        if not set(required_relation_names).issubset(self.mapped_relation_names):
            reasons.append("source relation-name mapping is incomplete")
        if reasons:
            raise TreatmentAdmissionError("S4 semantic admission failed: " + "; ".join(reasons))

    @property
    def bundle_digest(self) -> str:
        return canonical_digest(asdict(self))


_ARM_MECHANISMS: dict[str, dict[str, Any]] = {
    "S2a": {
        "context": "direct",
        "observability": "content_free_receipt",
        "policy": False,
        "budget": False,
        "workspace_intervention": False,
        "transport": "stock_driver",
        "semantic_bundle": False,
    },
    "S2b": {
        "context": "direct",
        "observability": "content_free_receipt",
        "policy": True,
        "budget": True,
        "workspace_intervention": True,
        "transport": "stock_driver_via_operating_layer",
        "semantic_bundle": False,
    },
    "S3": {
        "context": "none_prepared_query_replay",
        "observability": "seocho_query_spans",
        "policy": False,
        "budget": False,
        "workspace_intervention": False,
        "transport": "seocho_graph_store",
        "semantic_bundle": False,
    },
    "S4": {
        "context": "seocho_semantic_bundle",
        "observability": "content_free_receipt_and_seocho_query_spans",
        "policy": True,
        "budget": True,
        "workspace_intervention": True,
        "transport": "seocho_graph_store_via_operating_layer",
        "semantic_bundle": True,
    },
}


def build_treatment_manifest(
    *,
    arm: str,
    workspace_id: str,
    graph_digest: str,
    prepared_queries: Sequence[PreparedQuery],
    tool_schema_digest: str,
    policy: Mapping[str, Any],
    ontology_fingerprint: str = "",
    semantic_admission: SemanticBundleAdmission | None = None,
) -> dict[str, Any]:
    """Build the immutable arm identity recorded before execution."""
    if arm not in SUPPORTED_ARMS:
        raise ValueError(f"unsupported SEOCHO treatment arm: {arm}")
    if not workspace_id or not graph_digest or not tool_schema_digest:
        raise ValueError("workspace, graph, and tool schema digests are required")
    if any(query.workspace_id != workspace_id for query in prepared_queries):
        raise ValueError("every prepared query must use the arm workspace")
    if arm == "S4" and semantic_admission is None:
        raise TreatmentAdmissionError("S4 requires an admitted semantic bundle")
    if arm != "S4" and semantic_admission is not None:
        raise ValueError("semantic admission belongs only to S4")
    if arm == "S4":
        if not ontology_fingerprint:
            raise TreatmentAdmissionError("S4 requires an ontology fingerprint")
        assert semantic_admission is not None
        semantic_admission.validate(
            workspace_id=workspace_id,
            graph_digest=graph_digest,
            ontology_fingerprint=ontology_fingerprint,
        )
    manifest: dict[str, Any] = {
        "schema_version": ARM_SCHEMA_VERSION,
        "arm": arm,
        "workspace_id": workspace_id,
        "graph_digest": graph_digest,
        "ontology_fingerprint": ontology_fingerprint or None,
        "tool_schema_digest": tool_schema_digest,
        "prepared_query_set_digest": prepared_query_set_digest(prepared_queries),
        "prepared_query_digests": [query.prepared_digest for query in prepared_queries],
        "mechanisms": _ARM_MECHANISMS[arm],
        "policy": dict(policy),
        "policy_digest": canonical_digest(dict(policy)),
        "semantic_bundle_digest": semantic_admission.bundle_digest if semantic_admission else None,
    }
    manifest["arm_digest"] = canonical_digest(manifest)
    return manifest


@dataclass(frozen=True, slots=True)
class TreatmentExecution:
    """Private rows plus a content-free receipt suitable for JSONL telemetry."""

    rows: tuple[Mapping[str, Any], ...]
    receipt: Mapping[str, Any]


class SeochoTreatmentExecutor:
    """Execute one frozen arm without silently changing its mechanisms."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        database: str,
        direct_executor: DirectQueryExecutor | None = None,
        graph_store: Any | None = None,
        os_layer: Any | None = None,
        os_session: Any | None = None,
        policy_check: PolicyCheck | None = None,
        semantic_admission: SemanticBundleAdmission | None = None,
    ) -> None:
        self.manifest = dict(manifest)
        self.arm = str(self.manifest.get("arm", ""))
        if self.arm not in SUPPORTED_ARMS:
            raise ValueError("manifest has an unsupported arm")
        expected = self.manifest.get("arm_digest")
        unsigned = {key: value for key, value in self.manifest.items() if key != "arm_digest"}
        if expected != canonical_digest(unsigned):
            raise TreatmentAdmissionError("arm manifest digest mismatch")
        self.database = database
        self.direct_executor = direct_executor
        self.graph_store = graph_store
        self.os_layer = os_layer
        self.os_session = os_session
        self.policy_check = policy_check
        self.semantic_admission = semantic_admission
        self._allowed = set(self.manifest.get("prepared_query_digests") or ())
        self._admit_components()

    def _admit_components(self) -> None:
        if self.arm == "S2a" and self.direct_executor is None:
            raise TreatmentAdmissionError("S2a requires a stock-driver executor")
        if self.arm == "S3" and self.graph_store is None:
            raise TreatmentAdmissionError("S3 requires the SEOCHO graph store")
        if self.arm in {"S2b", "S4"}:
            if self.os_layer is None or self.os_session is None or self.policy_check is None:
                raise TreatmentAdmissionError("governed arms require OS layer, session, and policy check")
            if self.os_layer.workspace_id != self.manifest["workspace_id"]:
                raise TreatmentAdmissionError("OS layer workspace differs from arm manifest")
            try:
                self.policy_check(
                    action="run_agent", workspace_id=str(self.manifest["workspace_id"])
                )
            except Exception as exc:
                raise TreatmentAdmissionError(
                    f"runtime policy admission failed: {type(exc).__name__}"
                ) from exc
        if self.arm == "S4":
            if self.semantic_admission is None:
                raise TreatmentAdmissionError("S4 semantic admission is missing")
            if self.semantic_admission.bundle_digest != self.manifest.get("semantic_bundle_digest"):
                raise TreatmentAdmissionError("S4 semantic bundle digest mismatch")

    def _check_query(self, query: PreparedQuery) -> None:
        if query.prepared_digest not in self._allowed:
            raise TreatmentAdmissionError("query is outside the frozen prepared-query cohort")
        if query.workspace_id != self.manifest["workspace_id"]:
            raise TreatmentAdmissionError("query workspace differs from arm manifest")
        row_cap = int(self.manifest.get("policy", {}).get("row_cap", 0))
        match = re.search(r"\bLIMIT\s+(\d+|\$[A-Za-z_][A-Za-z0-9_]*)\b", query.cypher, flags=re.I)
        if match is None or row_cap < 1:
            raise TreatmentAdmissionError("bounded query and positive row cap are required")
        value = int(match.group(1)) if match.group(1).isdigit() else int(query.params[match.group(1)[1:]])
        if value > row_cap:
            raise TreatmentAdmissionError("prepared query LIMIT exceeds the arm row cap")

    def execute(
        self,
        query: PreparedQuery,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> TreatmentExecution:
        """Execute one query and return rows separately from safe telemetry."""
        self._check_query(query)
        started = time.perf_counter()
        status = "ok"
        error_class: str | None = None
        rows: Sequence[Mapping[str, Any]] = ()
        policy_ms: float | None = None
        budget_ms: float | None = None
        try:
            if self.arm in {"S2b", "S4"}:
                policy_started = time.perf_counter()
                assert self.policy_check is not None
                self.policy_check(action="run_agent", workspace_id=query.workspace_id)
                policy_ms = (time.perf_counter() - policy_started) * 1000.0
                budget_started = time.perf_counter()
                self.os_session.budget.charge(
                    prompt=int(prompt_tokens), completion=int(completion_tokens)
                )
                budget_ms = (time.perf_counter() - budget_started) * 1000.0
                raw = self.os_layer.execute_query(
                    self.os_session,
                    query.cypher,
                    json.dumps(dict(query.params), sort_keys=True),
                )
                payload = json.loads(raw)
                if "error" in payload:
                    status = "execution_error"
                    error_class = str(payload.get("error"))
                rows = tuple(payload.get("rows") or ())
            elif self.arm == "S3":
                # The prepared params already contain workspace_id.  Passing
                # workspace_id=None prevents the adapter from adding/changing a
                # parameter, while enforce_workspace_filter keeps read safety.
                rows = self.graph_store.query(
                    query.cypher,
                    params=dict(query.params),
                    database=self.database,
                    workspace_id=None,
                    enforce_workspace_filter=True,
                )
            else:
                assert self.direct_executor is not None
                rows = self.direct_executor(query.cypher, query.params, self.database)
        except Exception as exc:  # preserve failures as all-attempt receipts
            status = "rejected" if type(exc).__name__ in {
                "PermissionError",
                "BudgetExceededError",
                "QueryAdmissionRejected",
            } else "execution_error"
            error_class = type(exc).__name__
            rows = ()
        wall_ms = (time.perf_counter() - started) * 1000.0
        try:
            row_cap = int(self.manifest.get("policy", {}).get("row_cap", 0))
            if row_cap < 1:
                raise TreatmentAdmissionError("treatment manifest requires a positive row cap")
            normalized_rows = tuple(dict(row) for row in rows[:row_cap])
            result_digest = _result_digest(normalized_rows)
        except Exception as exc:
            status = "execution_error"
            error_class = type(exc).__name__
            normalized_rows = ()
            result_digest = _result_digest(())
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "arm": self.arm,
            "arm_digest": self.manifest["arm_digest"],
            "query_id": query.query_id,
            "query_digest": query.query_digest,
            "params_digest": query.params_digest,
            "prepared_digest": query.prepared_digest,
            "workspace_id": query.workspace_id,
            "database": self.database,
            "terminal_status": status,
            "error_class": error_class,
            "row_count": len(normalized_rows),
            "result_digest": result_digest,
            "wall_ms": round(wall_ms, 3),
            "policy_ms": round(policy_ms, 3) if policy_ms is not None else None,
            "budget_ms": round(budget_ms, 3) if budget_ms is not None else None,
            "prompt_tokens_charged": int(prompt_tokens) if self.arm in {"S2b", "S4"} else 0,
            "completion_tokens_charged": int(completion_tokens) if self.arm in {"S2b", "S4"} else 0,
            "semantic_bundle_digest": self.manifest.get("semantic_bundle_digest"),
        }
        return TreatmentExecution(rows=normalized_rows, receipt=receipt)


__all__ = [
    "ARM_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "SUPPORTED_ARMS",
    "PreparedQuery",
    "SemanticBundleAdmission",
    "SeochoTreatmentExecutor",
    "TreatmentAdmissionError",
    "TreatmentExecution",
    "build_treatment_manifest",
    "grbench_projection_ontology",
    "prepared_query_set_digest",
]
