---
id: 011
title: Observability improvements from the OTel live trace
state: approved
created: 2026-09-10
owner_intent: user
bead: seocho-98kt
---

## Intent (user)
"네 모두 beads 에 올리고 진행하세요" (2026-09-10) after the lab-s8 trace review: make the stage view a vLLM engineer can act on.

## Items (beads)
seocho-56r2 seocho-yrf1 seocho-8c7f seocho-9fds seocho-h3zo seocho-h25f seocho-c045
1. warmup/cold-request marking + appliance warmup request (first-request prefill 17.5 s).
2. Bolt/Oxigraph server-side stage timings on the graph-db span (38 s call: server vs transport).
3. validate_query span with policy code (query_policy_rejected loops visible).
4. live Agents SDK spans (manager thinking vs specialist prefill).
5. trace_id in runner stderr + gen_ai.request.id in receipts (log ↔ trace correlation).
6. samplers as OTel metrics via seocho.metrics.
7. production OTLP endpoint decision (replaces the in-instance file receiver).

## Approval (user)
approved: yes  # dev work; any paid validation pass reuses lab 010's one-pass queue (≈ $1.7)
budget_usd: 4

## Log (Claude)
- 2026-09-11 11:30 KST — item added from lab-s11: runner OTLP service.name must not inherit the engine's OTEL_SERVICE_NAME (fixed in run_lab_pass.sh, shell test; validate on the next live pass).
- 2026-09-10 22:30 KST — request + beads created; order 1 → 2 → 3, then 5, 4, 6, 7.
- 2026-09-10 23:30 KST — trace hand-off storage decided by the user: S3 bucket `seocho-agentic-os-traces` (prefix `seocho-agentic-os/traces/<run>`), CLI profile `seocho-traces`; AWS CLI v2 installed user-locally. Packer `scripts/benchmarks/pack_agentic_os_traces.py` written (allow-list → Parquet zstd → leak scan → manifest → `aws s3 cp --sse AES256`), not yet verified — this session lost shell access (auto-mode classifier). Next session: verify packer, upload lab-s8, commit manifest only; item 7 (shared OTLP collector) stays open.
