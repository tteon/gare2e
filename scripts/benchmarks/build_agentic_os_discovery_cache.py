#!/usr/bin/env python3
"""Freeze and validate B0/C-/S1 graph-schema discovery artifacts.

Input is JSONL with ``qid``, ``b0_response``, and ``s1_response`` objects.
Optional ``forbidden_terms`` must include terms the irrelevant C- control is
not permitted to expose.  The output is normally written below ignored
``.seocho/`` experiment artifacts, never into a public result directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from seocho.eval.discovery_cache import WhitespaceTokenCounter, VLLMTokenizer, build_discovery_cache, validate_discovery_cache, write_frozen_json


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--tool-schema", required=True, type=Path)
    parser.add_argument("--graph-digest", required=True)
    parser.add_argument("--taxonomy-digest")
    parser.add_argument("--base-url", help="vLLM endpoint used in the actual arm")
    parser.add_argument("--offline-staging", action="store_true", help="Use whitespace tokens; must be revalidated by vLLM before main run")
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--byte-cap", type=int, default=12_288)
    args = parser.parse_args()
    if not args.base_url and not args.offline_staging:
        parser.error("--base-url is required unless --offline-staging is set")
    tokenizer = WhitespaceTokenCounter() if args.offline_staging else VLLMTokenizer(args.base_url, args.model)
    cache = build_discovery_cache(
        records=_read_jsonl(args.records),
        graph_digest=args.graph_digest,
        taxonomy_digest=args.taxonomy_digest,
        tool_schema=json.loads(args.tool_schema.read_text(encoding="utf-8")),
        token_counter=tokenizer,
        byte_cap=args.byte_cap,
    )
    validate_discovery_cache(cache, token_counter=tokenizer)
    write_frozen_json(args.out, cache)
    print(json.dumps({"cache_digest": cache["cache_digest"], "cases": len(cache["cases"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
