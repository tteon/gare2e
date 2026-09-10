---
name: trace-share
description: Package a finished agentic-os appliance run into a content-free trace bundle (timings, counts, digests, OTel spans; no prompts, answers, secrets), upload it to the team S3 bucket, and commit the manifest + index copies to the gare2e repo so a colleague can find and analyse it. Use when the user asks to share traces, upload a run, "동료에게 trace 공유", or after any lab session whose acceptance lists "package uploaded to S3".
---

# Trace share (run → S3 → manifest in repo)

Procedure and rationale live in `docs/agentic-os/TRACE_SHARING.md` (Korean,
colleague-facing). This skill is the operator checklist.

## Names (fixed)

| thing | value |
|---|---|
| bucket | `s3://seocho-agentic-os-traces` (private, SSE-S3, 90-day lifecycle) |
| prefix | `seocho-agentic-os/traces/<run-id>` where `<run-id>` = local run dir name (`lab-sN-try-HHMMSS`) |
| index | `seocho-agentic-os/traces/index.json` (append one entry per run) |
| CLI profile | `seocho-traces` (IAM user `seocho-traces-uploader`: PutObject only, no ListBucket — `aws s3 ls` failing is normal) |
| local runs | `.seocho/benchmarks/agentic-os/colocated-appliance/runs/<run-id>` |
| repo copies | gare2e `traces/<run-id>/manifest.json`, `traces/index.json` (never the data) |
| packer | `scripts/benchmarks/pack_agentic_os_traces.py` |

## Steps

1. **Identity check, no secrets in chat**
   `timeout 60 aws sts get-caller-identity --profile seocho-traces --output json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])'`.
   If the profile is missing, the user configures it themselves (`!` prefix or a
   separate session). Never ask for or echo keys.
2. **Dry-run pack** into the scratchpad first:
   `timeout 300 python3 scripts/benchmarks/pack_agentic_os_traces.py <run-dir> --output $SCRATCH/traces/<run-id>`
   Read `manifest.json`: `leak_scan.hits` must be `[]`, `content_policy`
   present, `provenance.source_bundle_sha256` set. Exit 3 = leak hit, stop and
   fix the allowlist, never upload by hand.
3. **Upload** (adds `--s3-uri … --aws-profile seocho-traces --lab-request NNN`);
   the packer uses `aws s3 cp --recursive --sse AES256`. Check `result.s3.exit_code == 0`.
4. **Index**: append the run's entry (run id, date, lab request, bundle sha,
   files/bytes, manifest path, `keep`) to `index.json` and upload it to the
   bucket root prefix; keep a copy next to the manifest.
5. **Commit copies** to gare2e via the worktree recipe in `lab-resume` §4:
   `traces/<run-id>/manifest.json` and `traces/index.json` only.
6. **Hand-off to the colleague**: read-only IAM user on the prefix, or
   `aws s3 presign s3://…/<run-id>/manifest.json --expires-in 604800 --profile seocho-traces`
   for the manifest and the files they need. Point them at
   `docs/agentic-os/TRACE_SHARING.md` §4–5 for download and analysis (DuckDB
   over Parquet, Tempo import of `otel/spans.jsonl`).
7. **Record**: `bd update <bead> --append-notes`, lab request `## Log` line
   with the S3 prefix and manifest sha, and the LEDGER row if it was a paid run.

## Never upload

`*.private.*` files, `vast-colocated-lifecycle.json` (instance payload carries
token/IP), raw model output, prompts, gold answers, `.env`, anything under
`~/.aws`. The packer's allowlist enforces this; manual `aws s3 cp` bypasses it,
so do not use it for run data.
