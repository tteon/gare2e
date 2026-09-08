"""Read-only, SKOS-shaped taxonomy catalog for bounded agent retrieval hints."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


@dataclass(frozen=True, slots=True)
class TaxonomyConcept:
    """One SKOS-compatible concept with scoped, non-authoritative facets."""

    concept_id: str
    pref_label: str
    alt_labels: tuple[str, ...] = ()
    broader: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    facets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaxonomyConcept":
        facets = {
            str(key): tuple(str(item) for item in value)
            for key, value in dict(payload.get("facets") or {}).items()
            if isinstance(value, Sequence) and not isinstance(value, str)
        }
        return cls(
            concept_id=str(payload["concept_id"]),
            pref_label=str(payload["pref_label"]),
            alt_labels=tuple(str(item) for item in payload.get("alt_labels", ())),
            broader=tuple(str(item) for item in payload.get("broader", ())),
            related=tuple(str(item) for item in payload.get("related", ())),
            facets=facets,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "pref_label": self.pref_label,
            "alt_labels": list(self.alt_labels),
            "broader": list(self.broader),
            "related": list(self.related),
            "facets": {key: list(value) for key, value in sorted(self.facets.items())},
        }


class TaxonomyCatalog:
    """Immutable catalog whose resolution never picks an ambiguous sense."""

    def __init__(self, concepts: Sequence[TaxonomyConcept], *, scheme_id: str) -> None:
        self.scheme_id = str(scheme_id).strip()
        self._concepts = tuple(concepts)
        if not self.scheme_id or not self._concepts:
            raise ValueError("scheme_id and at least one concept are required")
        if len({item.concept_id for item in self._concepts}) != len(self._concepts):
            raise ValueError("concept_id values must be unique")
        self.digest = hashlib.sha256(
            json.dumps(
                {"scheme_id": self.scheme_id, "concepts": [item.to_dict() for item in self._concepts]},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaxonomyCatalog":
        return cls(
            [TaxonomyConcept.from_dict(item) for item in payload.get("concepts", ()) if isinstance(item, Mapping)],
            scheme_id=str(payload.get("scheme_id", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme_id": self.scheme_id,
            "concepts": [concept.to_dict() for concept in self._concepts],
            "catalog_sha256": self.digest,
        }

    def resolve(self, term: str, *, facets: Mapping[str, str] | None = None) -> dict[str, Any]:
        needle = _key(term)
        candidates = [
            concept for concept in self._concepts
            if needle and needle in {_key(concept.pref_label), *(_key(item) for item in concept.alt_labels)}
        ]
        for name, value in (facets or {}).items():
            if str(value).strip():
                candidates = [c for c in candidates if _key(str(value)) in {_key(item) for item in c.facets.get(str(name), ())}]
        receipt = {"scheme_id": self.scheme_id, "catalog_sha256": self.digest, "term": term}
        if not candidates:
            return {**receipt, "status": "not_found", "candidates": []}
        if len(candidates) != 1:
            return {**receipt, "status": "ambiguous", "candidates": [c.to_dict() for c in candidates]}
        return {**receipt, "status": "resolved", "concept": candidates[0].to_dict()}

    def slice(self, terms: str, *, max_concepts: int = 12) -> dict[str, Any]:
        selected: list[TaxonomyConcept] = []
        for term in (item.strip() for item in terms.split(",")):
            result = self.resolve(term)
            if result["status"] == "resolved":
                concept = TaxonomyConcept.from_dict(result["concept"])
                if concept.concept_id not in {item.concept_id for item in selected}:
                    selected.append(concept)
            if len(selected) >= max(1, min(max_concepts, 50)):
                break
        return {"scheme_id": self.scheme_id, "catalog_sha256": self.digest, "concepts": [c.to_dict() for c in selected]}

    def build_query_intent(
        self, terms: str, *, facets: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """Produce a small, compiler-safe retrieval intent from catalog terms.

        This is deliberately not Text2Cypher.  A downstream deterministic
        compiler may use ``resolved_anchor_ids`` only when ``disposition`` is
        ``ready``.  An ambiguous or unknown anchor therefore cannot become an
        unbounded graph scan merely because a model continued reasoning.
        """
        resolutions = [
            self.resolve(term, facets=facets)
            for term in (item.strip() for item in terms.split(","))
            if term
        ]
        anchors = [item["concept"]["concept_id"] for item in resolutions if item["status"] == "resolved"]
        unresolved = [
            {"term": item["term"], "status": item["status"]}
            for item in resolutions if item["status"] != "resolved"
        ]
        return {
            "scheme_id": self.scheme_id,
            "catalog_sha256": self.digest,
            "resolved_anchor_ids": anchors,
            "unresolved_terms": unresolved,
            "disposition": "ready" if anchors and not unresolved else "abstain",
            "reason": "all anchors resolved" if anchors and not unresolved else "taxonomy resolution is incomplete or ambiguous",
        }

    def graph_mapping_coverage(self) -> dict[str, list[str]]:
        """Return normalized graph facets for offline promotion/admission."""
        values: dict[str, set[str]] = {
            "graph_label": set(),
            "graph_relationship_type": set(),
            "graph_relation_name": set(),
            "graph_node_type": set(),
        }
        for concept in self._concepts:
            for key in values:
                values[key].update(concept.facets.get(key, ()))
        return {key: sorted(items) for key, items in values.items()}

    def text2cypher_arguments(self, query_intent: Mapping[str, Any]) -> dict[str, Any]:
        """Translate one resolved catalog anchor into deterministic-tool arguments.

        A promoted entity concept supplies ``graph_label`` and optional
        ``graph_identity`` facets. Topic-only catalogs deliberately lack them
        and therefore abstain instead of inventing a graph label.
        """
        if query_intent.get("disposition") != "ready":
            return {"disposition": "abstain", "reason": "query intent is not ready"}
        anchors = list(query_intent.get("resolved_anchor_ids") or [])
        if len(anchors) != 1:
            return {"disposition": "abstain", "reason": "exactly one resolved graph anchor is required"}
        concept = next((item for item in self._concepts if item.concept_id == anchors[0]), None)
        if concept is None:
            return {"disposition": "abstain", "reason": "catalog anchor is unavailable"}
        labels = concept.facets.get("graph_label", ())
        identities = concept.facets.get("graph_identity", ())
        if len(labels) != 1:
            return {"disposition": "abstain", "reason": "resolved concept has no unique graph label"}
        return {"disposition": "ready", "intent": "entity_lookup", "anchor_entity": identities[0] if identities else concept.pref_label, "anchor_label": labels[0]}
