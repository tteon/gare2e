#!/usr/bin/env python3
"""Register, compare and inspect shared experiment results without paid calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from seocho.eval.experiment_platform import Registry, import_characterization


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Publish immutable reviewed characterization and optional CSV.gz")
    importer.add_argument("--source", type=Path, required=True)
    importer.add_argument("--run-id", required=True)
    importer.add_argument("--owner", required=True)
    importer.add_argument("--metadata", type=Path, required=True)
    importer.add_argument("--metrics", type=Path, help="JSON list of additive numeric metric definitions")
    importer.add_argument("--diagnostics", type=Path, help="Reviewed profiler artifact manifest (Nsight SQLite/report)")
    commands.add_parser("list")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "import":
        run = import_characterization(args.source, args.registry, run_id=args.run_id,
            workspace_id=args.workspace_id, owner=args.owner, metadata=json.loads(args.metadata.read_text()),
            extra_metrics=json.loads(args.metrics.read_text()) if args.metrics else None,
            diagnostics=json.loads(args.diagnostics.read_text()) if args.diagnostics else None)
        print(json.dumps({"run_id": run["id"], "conditions": len(run["conditions"]), "sha256": run["content_sha256"]}))
    elif args.command == "list":
        print(json.dumps(Registry(args.registry, args.workspace_id).runs(), indent=2))
    else:
        token = os.environ.get("GARE2E_PLATFORM_TOKEN")
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
            parser.error("Non-loopback binding requires GARE2E_PLATFORM_TOKEN; terminate TLS at your team proxy")
        import uvicorn
        # The runtime shell is checkout-owned; core import/analysis remains SDK-owned.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from runtime.experiment_platform import create_app
        uvicorn.run(create_app(args.registry, args.workspace_id, token=token), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
