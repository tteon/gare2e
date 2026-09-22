"""Import reviewed repository reports; no inference, credentials or DB calls."""
from __future__ import annotations

import argparse
from pathlib import Path

from seocho.eval.experiment_platform import import_characterization


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--workspace-id", default="gare2e")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    for name in ("lab024-characterization", "lab025-replicas", "lab026-instrumentation"):
        source = repo / "docs/agentic-os/reports" / name
        if not source.exists():
            continue
        run = import_characterization(source, args.registry, run_id=name, workspace_id=args.workspace_id,
            owner="historical-import", metadata={"source_commit": "unknown", "repetitions": 1,
                "contract": {}, "admission": {}})
        print(f"{run['id']}: {len(run['conditions'])} conditions; historical contracts/admission remain unverified")


if __name__ == "__main__":
    main()
