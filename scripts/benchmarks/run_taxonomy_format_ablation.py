#!/usr/bin/env python3
"""Materialize equivalent RDF/SKOS/RDFS/OWL/filesystem taxonomy contexts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from seocho.eval.taxonomy_formats import render_taxonomy_formats
from seocho.taxonomy_catalog import TaxonomyCatalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    catalog = TaxonomyCatalog.from_dict(json.loads(args.catalog.read_text(encoding="utf-8")))
    formats = render_taxonomy_formats(catalog)
    root = args.out
    root.mkdir(parents=True, exist_ok=True)
    receipt = {"schema_version": "seocho.taxonomy_format_ablation.v1", "catalog_digest": catalog.digest, "formats": {}}
    for name, content in formats.items():
        started = time.perf_counter()
        if name == "filesystem":
            target = root / "filesystem" / "index.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.loads(content)
            for concept in payload["concepts"]:
                safe = concept["concept_id"].replace(":", "_").replace("/", "_")
                path = root / "filesystem" / "concepts" / f"{safe}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(concept, sort_keys=True) + "\n", encoding="utf-8")
            target.write_text(content + "\n", encoding="utf-8")
        else:
            target = root / f"taxonomy.{name}"
            target.write_text(content, encoding="utf-8")
        receipt["formats"][name] = {"path": str(target), "bytes": len(content.encode()), "write_ms": round((time.perf_counter() - started) * 1000, 3)}
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
