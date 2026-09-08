"""Equivalent semantic-context serializations for the taxonomy ablation."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from seocho.taxonomy_catalog import TaxonomyCatalog


def render_taxonomy_formats(catalog: TaxonomyCatalog) -> dict[str, str]:
    payload = catalog.to_dict()
    concepts = payload["concepts"]
    rdf = []
    for concept in concepts:
        subject = f"<{concept['concept_id']}>"
        rdf.extend(
            (
                f"{subject} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2004/02/skos/core#Concept> .",
                f"{subject} <http://www.w3.org/2004/02/skos/core#prefLabel> {json.dumps(concept['pref_label'])} .",
            )
        )
    skos = {"@context": {"skos": "http://www.w3.org/2004/02/skos/core#"}, "@type": "skos:ConceptScheme", "concepts": concepts}
    rdfs = "\n".join(
        f"<{item['concept_id']}> a <http://www.w3.org/2000/01/rdf-schema#Class>; <http://www.w3.org/2000/01/rdf-schema#label> {json.dumps(item['pref_label'])}."
        for item in concepts
    )
    owl = "\n".join(
        f"<{item['concept_id']}> a <http://www.w3.org/2002/07/owl#Class>; <http://www.w3.org/2004/02/skos/core#prefLabel> {json.dumps(item['pref_label'])}."
        for item in concepts
    )
    filesystem = json.dumps(
        {"scheme_id": payload["scheme_id"], "index": [item["concept_id"] for item in concepts], "concepts": concepts},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "rdf": "\n".join(rdf) + "\n",
        "skos": json.dumps(skos, sort_keys=True, separators=(",", ":")),
        "rdfs": rdfs + "\n",
        "owl": owl + "\n",
        "filesystem": filesystem,
    }


def measure_filesystem_slice(root: str | Path, concept_ids: list[str]) -> dict[str, Any]:
    started = perf_counter()
    found = 0
    for concept_id in concept_ids:
        safe = concept_id.replace(":", "_").replace("/", "_")
        if (Path(root) / "concepts" / f"{safe}.json").is_file():
            found += 1
    return {"found": found, "requested": len(concept_ids), "wall_ms": round((perf_counter() - started) * 1000, 3)}


__all__ = ["measure_filesystem_slice", "render_taxonomy_formats"]
