#!/usr/bin/env python3
"""Validate local FP8 evidence and emit a plan. Never launch a server or download."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seocho.eval.sf1_fp8 import (
    MODEL_PATH,
    OFFLINE_ENV,
    SF1FP8Profile,
    file_sha256,
    verify_model,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path(MODEL_PATH))
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--gpu-count", type=int, required=True)
    parser.add_argument("--tp", type=int, required=True)
    parser.add_argument("--thinking", choices=("high", "off"), default="high")
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--deadline-ms", type=int, required=True)
    parser.add_argument("--resolved-settings", type=Path)
    parser.add_argument("--startup-log", type=Path)
    args = parser.parse_args()
    try:
        if str(args.model_dir) != MODEL_PATH:
            raise ValueError("campaign_model_path_mismatch")
        if file_sha256(args.model_manifest) != args.expected_manifest_sha256:
            raise ValueError("manifest_sha256_mismatch")
        profile = SF1FP8Profile(args.tp, args.gpu_count, args.thinking)
        model = verify_model(
            args.model_dir, json.loads(args.model_manifest.read_text())
        )
        result = {
            "status": "STATIC_PASS_RUNTIME_PENDING",
            "model": model,
            "server_argv": profile.server_argv(),
            "environment": OFFLINE_ENV,
            "pi_profile": profile.pi_profile(
                max_requests=args.max_requests, deadline_ms=args.deadline_ms
            ),
            "gaps": [
                "RESOURCE_OWNERSHIP_NOT_CHECKED",
                "LIVE_MODEL_TOOL_NOT_RUN",
                "TRACE_AND_STORAGE_READBACK_NOT_RUN",
            ],
        }
        if bool(args.resolved_settings) != bool(args.startup_log):
            raise ValueError("both_runtime_evidence_files_required")
        if args.resolved_settings:
            result["runtime"] = profile.verify_runtime(
                json.loads(args.resolved_settings.read_text()),
                args.startup_log.read_text(),
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "INVALID", "error_code": "FP8_PREFLIGHT_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
