#!/usr/bin/env python3
"""Promote a reviewed, digest-bound GRBench projection taxonomy bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from seocho.eval.agentic_os_artifacts import canonical_digest
from seocho.eval.agentic_os_treatments import grbench_projection_ontology
from seocho.ontology.versioning import ontology_schema_fingerprint
from seocho.taxonomy_catalog import TaxonomyCatalog, TaxonomyConcept


def _catalog(import_receipt: dict, *, version: str, include_all: bool) -> TaxonomyCatalog:
    topology = import_receipt["schema"]["topology"]
    concepts: list[TaxonomyConcept] = []
    node_types = sorted(str(value) for value in topology["node_types"])
    relation_names = sorted(str(value) for value in topology["relation_names"])
    for node_type in node_types:
        concepts.append(
            TaxonomyConcept(
                concept_id=f"urn:seocho:grbench:node-type:{node_type}",
                pref_label=node_type,
                broader=("urn:seocho:grbench:node",),
                facets={
                    "graph_label": ("GRBenchNode",),
                    "graph_node_type": (node_type,),
                },
            )
        )
    for relation in relation_names if include_all else relation_names[:1]:
        concepts.append(
            TaxonomyConcept(
                concept_id=f"urn:seocho:grbench:relation:{relation}",
                pref_label=relation,
                broader=("urn:seocho:grbench:relation",),
                facets={
                    "graph_label": ("GRBenchNode",),
                    "graph_relationship_type": ("GRBENCH_REL",),
                    "graph_relation_name": (relation,),
                },
            )
        )
    concepts.extend(
        (
            TaxonomyConcept("urn:seocho:grbench:node", "graph node", facets={"graph_label": ("GRBenchNode",)}),
            TaxonomyConcept("urn:seocho:grbench:relation", "graph relation", facets={"graph_relationship_type": ("GRBENCH_REL",)}),
        )
    )
    return TaxonomyCatalog(concepts, scheme_id=f"urn:seocho:grbench:taxonomy:{version}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-receipt", required=True, type=Path)
    parser.add_argument("--discovery-cache", required=True, type=Path)
    parser.add_argument("--out-catalog", required=True, type=Path)
    parser.add_argument("--out-previous", required=True, type=Path)
    parser.add_argument("--out-bundle", required=True, type=Path)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    receipt = json.loads(args.import_receipt.read_text(encoding="utf-8"))
    discovery = json.loads(args.discovery_cache.read_text(encoding="utf-8"))
    current = _catalog(receipt, version=args.version, include_all=True)
    previous = _catalog(receipt, version="0.9.0", include_all=False)
    ontology_fingerprint = ontology_schema_fingerprint(grbench_projection_ontology())
    bundle = {
        "schema_version": "seocho.grbench.semantic_bundle.v1",
        "workspace_id": receipt["workspace_id"],
        "graph_digest": receipt["schema"]["schema_digest"],
        "taxonomy_digest": current.digest,
        "taxonomy_version": args.version,
        "active_taxonomy_digest": current.digest,
        "ontology_fingerprint": ontology_fingerprint,
        "discovery_cache_digest": discovery["cache_digest"],
        "mapping_status": "curated",
        "mapped_node_labels": ["GRBenchNode"],
        "mapped_relationship_types": ["GRBENCH_REL"],
        "mapped_relation_names": sorted(receipt["schema"]["topology"]["relation_names"]),
        "source_receipt_sha256": canonical_digest(receipt),
    }
    for path, payload in (
        (args.out_catalog, current.to_dict()),
        (args.out_previous, previous.to_dict()),
        (args.out_bundle, bundle),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"taxonomy_digest": current.digest, "ontology_fingerprint": ontology_fingerprint}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
