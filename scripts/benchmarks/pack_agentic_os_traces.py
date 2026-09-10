#!/usr/bin/env python3
"""Package one lab run's content-free traces and telemetry for hand-off.

Selects only allow-listed artifacts from a lifecycle run directory (receipt-
derived OTel spans, collector spans, stage/lane tables, session summaries,
catalogs, queue and pass receipts, sampler CSV/JSONL), converts the JSONL span
and metrics streams to zstd-compressed Parquet (5-10x smaller, DuckDB/Polars
friendly), scans every included file for the things that must never leave the
machine (``*.private.*`` content and instance payload secrets), and writes
``manifest.json`` (sha256 per file, schema versions, source bundle digest).

With ``--s3-uri s3://bucket/prefix`` the package is uploaded with the AWS CLI
(server-side encryption on); the manifest is what gets committed to git, the
blobs live in object storage.  Credentials are never read or written here:
the CLI uses the caller's ``~/.aws`` profile or environment.

    python3 scripts/benchmarks/pack_agentic_os_traces.py <run-root> --output traces/<run-id> \\
        [--s3-uri s3://<bucket>/seocho-agentic-os/traces/<run-id>] [--aws-profile NAME]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ALLOW_GLOBS = (
    "queue-receipt.jsonl",
    "session-summary/*.csv",
    "session-summary/*.md",
    "otel/collector-spans.jsonl",
    "run-*/pass-receipt.json",
    "run-*/otel-export-receipt.json",
    "run-*/otel/spans.jsonl",
    "run-*/otel/*.csv",
    "run-*/otel/*.md",
    "run-*/catalog/*.csv",
    "run-*/catalog/*.json",
    "run-*/catalog/*.md",
    "run-*/analysis/*.csv",
    "run-*/analysis/*.md",
    "run-*/workload/gpu.csv",
    "run-*/workload/host-process.csv",
    "run-*/workload/vllm-metrics.jsonl",
    "run-*/workload/hardware.json",
    "run-*/workload/colocated-workload-receipt.json",
    "run-*/vllm-restart/vllm-restart-receipt.json",
    "run-*/vllm-restart/router-receipts.jsonl",
    "run-*/e2e/*-tool-*/coordinator-receipts.jsonl",
    "run-*/e2e/*-tool-*/shard-*/coordinator-receipts.jsonl",
    "run-*/e2e/*-tool-*/attempts/*/run-receipt.json",
    "run-*/e2e/*-tool-*/attempts/*/receipts.jsonl",
    "run-*/e2e/*-tool-*/shard-*/attempts/*/run-receipt.json",
    "run-*/e2e/*-tool-*/shard-*/attempts/*/receipts.jsonl",
    "appliance-phases.private.tsv",  # timestamps + phase names only; renamed on export
)
PARQUET_STREAMS = ("otel/collector-spans.jsonl", "otel/spans.jsonl", "workload/vllm-metrics.jsonl")
LEAK_PATTERNS = {
    "instance_token_field": re.compile(r"jupyter_token"),
    "public_ipv4": re.compile(
        r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)(?!10\.)(?!192\.168\.)(?!172\.(1[6-9]|2\d|3[01])\.)"
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
    ),
    "ssh_public_key": re.compile(r"ssh-(rsa|ed25519) AAAA"),
    "provider_api_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_parquet(src: Path, dst: Path) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return {"rows": 0, "skipped": True}
    # Nested dicts (attributes, metrics, histogram buckets) become JSON strings:
    # a stable schema across passes, still queryable via json_extract in
    # DuckDB / Polars.
    flat = [{k: (json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v) for k, v in r.items()} for r in rows]
    table = pa.Table.from_pylist(flat)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dst, compression="zstd")
    return {"rows": len(rows), "columns": table.num_columns, "jsonl_bytes": src.stat().st_size, "parquet_bytes": dst.stat().st_size}


def _scan(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["unreadable"]
    return [name for name, pat in LEAK_PATTERNS.items() if pat.search(text)]


def index_entry(manifest: dict[str, Any], s3_uri: str, lab_request: str | None, keep: bool) -> dict[str, Any]:
    """One bucket-root index row: where a run's package is and which code made it."""
    prov = manifest.get("provenance", {})
    return {
        "run": manifest["run_root"],
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lab_request": lab_request,
        "source_bundle_sha256": prov.get("source_bundle_sha256"),
        "instance_id": prov.get("instance_id"),
        "state": prov.get("state"),
        "files": manifest["file_count"],
        "bytes": manifest["bytes"],
        "prefix": s3_uri.rstrip("/"),
        "manifest": s3_uri.rstrip("/") + "/manifest.json",
        "keep": bool(keep),
    }


def append_index(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append-only run list; a re-upload of the same run replaces its row."""
    if path.exists():
        index = json.loads(path.read_text(encoding="utf-8"))
    else:
        index = {"schema_version": "seocho.agentic_os_trace_index.v1", "runs": []}
    index["runs"] = [r for r in index["runs"] if r.get("run") != entry["run"]] + [entry]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_root", type=Path)
    ap.add_argument("--output", type=Path, required=True, help="package directory (commit only its manifest.json)")
    ap.add_argument("--s3-uri", help="s3://bucket/prefix to upload the package to (aws cli, SSE AES256)")
    ap.add_argument("--aws-profile")
    ap.add_argument("--keep-jsonl", action="store_true", help="keep JSONL next to the Parquet copies")
    ap.add_argument("--lab-request", help="lab request id this run belongs to (recorded in the manifest and index)")
    ap.add_argument("--index", type=Path, help="local copy of the bucket-root traces/index.json; the run is appended and the file re-uploaded next to the run prefix")
    ap.add_argument("--keep", action="store_true", help="mark the index entry keep=true (exempt from the 90-day lifecycle by the bucket owner)")
    args = ap.parse_args()
    run = args.run_root.resolve()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        print(json.dumps({"error": "output directory is not empty", "output": str(out)}), file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    leaks: list[dict[str, Any]] = []
    for pattern in ALLOW_GLOBS:
        for src in sorted(run.glob(pattern)):
            if not src.is_file():
                continue
            rel = src.relative_to(run)
            if ".private." in src.name and src.name != "appliance-phases.private.tsv":
                continue  # belt and braces: the allow list must never pick private content
            dst_rel = Path(str(rel).replace(".private", ""))
            entry: dict[str, Any] = {"path": str(dst_rel), "source": str(rel)}
            if any(str(rel).endswith(s) for s in PARQUET_STREAMS):
                pq_rel = dst_rel.with_suffix(".parquet")
                entry.update(_to_parquet(src, out / pq_rel))
                entry["path"] = str(pq_rel)
                if args.keep_jsonl:
                    (out / dst_rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, out / dst_rel)
            else:
                (out / dst_rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out / dst_rel)
            hits = _scan(src)
            if hits:
                leaks.append({"path": str(rel), "patterns": hits})
            target = out / entry["path"]
            entry.update({"bytes": target.stat().st_size, "sha256": _sha256(target)})
            files.append(entry)
    lifecycle = run / "vast-colocated-lifecycle.json"
    provenance: dict[str, Any] = {}
    if lifecycle.exists():
        d = json.loads(lifecycle.read_text(encoding="utf-8"))
        # Only identifiers and digests from the lifecycle receipt: the raw
        # provider payload inside it carries a session token and addresses.
        uploads = d.get("verified_uploads", [])
        provenance = {
            "instance_id": d.get("instance_id"),
            "state": d.get("state"),
            "max_runtime_s": d.get("max_runtime_s"),
            "source_bundle_sha256": next((u["sha256"] for u in uploads if u["remote"].endswith("seocho-source.tar.gz")), None),
            "queue_sha256": next((u["sha256"] for u in uploads if u["remote"].endswith("lab-queue.tsv")), None),
        }
    if args.lab_request:
        provenance["lab_request"] = args.lab_request
    manifest = {
        "schema_version": "seocho.agentic_os_trace_package.v1",
        "run_root": run.name,
        "provenance": provenance,
        "files": files,
        "file_count": len(files),
        "bytes": sum(f["bytes"] for f in files),
        "leak_scan": {"patterns": sorted(LEAK_PATTERNS), "hits": leaks},
        "content_policy": "content-free: timings, counts, digests, outcome codes; no prompts, queries, answers, secrets",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if leaks:
        print(json.dumps({"error": "leak scan hit; package NOT uploaded", "hits": leaks}, sort_keys=True), file=sys.stderr)
        return 3
    result: dict[str, Any] = {"output": str(out), "files": len(files), "bytes": manifest["bytes"]}
    if args.s3_uri:
        cmd = ["aws", "s3", "cp", str(out), args.s3_uri.rstrip("/") + "/", "--recursive", "--sse", "AES256", "--only-show-errors"]
        if args.aws_profile:
            cmd += ["--profile", args.aws_profile]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result["s3"] = {"uri": args.s3_uri, "exit_code": proc.returncode, "stderr_tail": proc.stderr[-400:]}
        if proc.returncode != 0:
            print(json.dumps(result, sort_keys=True), file=sys.stderr)
            return 4
        if args.index:
            index_uri = args.s3_uri.rstrip("/").rsplit("/", 1)[0] + "/index.json"
            entry = index_entry(manifest, args.s3_uri, args.lab_request, args.keep)
            append_index(args.index, entry)
            shutil.copy2(args.index, out / "index.json")
            cmd = ["aws", "s3", "cp", str(args.index), index_uri, "--sse", "AES256", "--only-show-errors"]
            if args.aws_profile:
                cmd += ["--profile", args.aws_profile]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            result["index"] = {"uri": index_uri, "exit_code": proc.returncode, "runs": len(json.loads(args.index.read_text(encoding="utf-8"))["runs"])}
            if proc.returncode != 0:
                result["index"]["stderr_tail"] = proc.stderr[-400:]
                print(json.dumps(result, sort_keys=True), file=sys.stderr)
                return 5
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
