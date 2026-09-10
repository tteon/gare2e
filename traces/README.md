# traces/ — where the shared run packages live

Only pointers are committed here: `index.json` (one row per uploaded run) and
`<run>/manifest.json` (file list with sha256, provenance = bundle sha, queue sha,
instance id, lab request). The data itself is in
`s3://seocho-agentic-os-traces/seocho-agentic-os/traces/<run>/` (private bucket,
90-day lifecycle). Procedure: `docs/agentic-os/TRACE_SHARING.md`; producer:
`scripts/benchmarks/pack_agentic_os_traces.py`.
