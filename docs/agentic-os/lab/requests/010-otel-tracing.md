---
id: 010
title: OpenTelemetry for the agentic e2e (receipts → OTLP span trees, traceparent to vLLM)
state: approved
created: 2026-09-10
owner_intent: user
bead: seocho-hnve
---

## Intent (user)
- 2026-09-10 21:40: "지금 이 Log 를 뽑는건 각 스테이지마다 어떻게 시간이 걸렸는지 확인하기 위함입니다. vllm 엔지니어가 이를 확인하고 개선할 부분을 찾는게 핵심이자 목적" — the trace must show per-request stage timings (queue → prefill → decode, and the gaps between requests) in a form a vLLM engineer can act on.
"OTel 로 한다고 말을 했기에 이렇게 해야할 거 같네요 해주세요" (2026-09-10) — the e2e wrote custom receipts only;
SEOCHO's stated contract is OTel-compatible tracing, so the experiment path must emit OTel too.

## Acceptance (user; Claude may draft)
- [x] One trace per case; spans for LLM requests (gen_ai.*), tool calls (gen_ai.tool.*), graph-DB calls (db.*), specialist invocation (gen_ai.agent.*).
- [x] W3C traceparent on every chat-completion request (both clients), span id kept in receipts; vLLM OTLP tracing switchable so server spans join.
- [x] Content-free: digests/counts/timings only; JSONL artifact always, OTLP push optional (SEOCHO_OTLP_ENDPOINT).
- [ ] Per-request stage table for the vLLM engineer: client latency, server e2e / queue / TTFT / prefill / decode (vLLM detailed traces), tokens in/out/cached, gap before the request (tool + DB time).
- [ ] Live validation on an appliance run with a collector (needs a reachable OTLP endpoint; not done — no collector available in this session).

## Constraints (user)
- No prompt/answer text in telemetry (existing rule).

## Feasibility (Claude)
- verdict: feasible; implemented post-hoc (receipt → spans) to keep tracing out of the hot path; live propagation via headers only.

## Approval (user)
approved: yes  # dev work + live validation pass; chat 2026-09-10 "네 실제로 해봐야합니다"
budget_usd: 5  # max-dph raised to 2.9 (cheapest admissible RTX PRO 6000 pair is $2.84, host 618816); offer filter widened to RTX PRO 6000 96 GB pairs (≤$2.7/h) after 68 misses on A100-80GB; validation pass, GPU class irrelevant

## Log (Claude)
- 2026-09-11 11:30 KST — lab-s11: 14 996 server spans in the in-instance receiver, 4221 vLLM chat spans, 2085/2099 client requests joined through traceparent (99.3 %); stage tables in run-all-k*/otel-stages. Defect found: runner spans inherited OTEL_SERVICE_NAME=vllm from the entrypoint → all client spans read service.name=vllm; fixed in run_lab_pass.sh (SEOCHO_OTEL_SERVICE_NAME) + shell test, unvalidated live (next pass).
- 2026-09-11 08:50 KST — lab-s8 trace package shared: s3://seocho-agentic-os-traces/seocho-agentic-os/traces/lab-s8-try-214132 (87 files, 1.4 MB, content-free, leak scan 0), bucket index.json started; manifest/index copies in gare2e traces/. Colleague access (read-only IAM or presigned URL) still to be granted by the bucket owner.
- 2026-09-11 07:40 KST — lab-s9 (session-D medium-k4-otel, bundle v37) died at dozer_indexes before the pass (see 012 log); the vLLM span naming / 15 s Bolt timeout validation moves to lab 013. Packer scripts/benchmarks/pack_agentic_os_traces.py lint-clean; dry-run on lab-s8: 87 files, 1.4 MB, leak scan 0 hits.
- 2026-09-10 22:20 KST — lab-s8 complete ($1.69, 36 min, state=complete). Collector file: 1220 spans = 633 receipt-derived (seocho-agentic-os) + 587 vLLM (260 Compile graph startup spans, 249 llm_request, weights/load spans). 248/250 LLM requests matched to their vLLM server span via traceparent. Stage tables written: run-medium-k4-otel/otel/{request-stages,lane-stages}.{csv,md}. Findings: prefill p50 0.116 s / p90 0.382 s, one 17.5 s cold first request; queue max 0.98 s at K=4 (5 requests > 0.1 s); client−server ≈ 4 ms. lab-s9 launched (v37) at 22:18 for indexes/timeout/service-name validation.
- 2026-09-10 22:15 KST — lab-s8 mid-run: **propagation verified live** — vLLM `llm_request` spans (SERVER) carry gen_ai.latency.{e2e,time_in_queue,time_to_first_token,time_in_model_prefill,time_in_model_decode,time_in_model_inference} and gen_ai.usage.*; 55/55 GRBench-single client chat spans have their vLLM span as child in the same trace. vLLM also exports startup spans (Compile graph ×260, Worker init, Load weights). Two defects found: vLLM service.name = unknown_service (fixed: OTEL_SERVICE_NAME=vllm), and the DB fix was ineffective in queue passes — the direct GRBench runner has its own Bolt executor with the same timeout bug and indexes were only created in the e2e gate phase. Both fixed (gare2e, bundle v37); lab-s9 (same one-pass queue) will validate after lab-s8 ends.
- 2026-09-10 21:42 KST — lab-s8 admitted on the first attempt after the query fix: instance 50491173, $2.84/h (host 618816, 2× RTX PRO 6000 96 GB, cuda 13.x). Added scripts/benchmarks/summarize_agentic_os_otel_stages.py (client chat spans ⋈ vLLM server spans by traceparent → request-stages / lane-stages tables with queue/TTFT/prefill/decode and gap-before).
- 2026-09-10 21:45 KST — root cause of the admission misses: `vastai search offers` returns one ~64-row page sorted by price; with a loose query the page is all consumer GPUs and the runner never sees the A100/RTX PRO offers. Query now includes `gpu_ram>=79` (GB units; `>=79000` returns nothing); relaunched as unit s8e. Skill updated.
- 2026-09-10 21:40 KST — added `--collect-detailed-traces all` (env SEOCHO_VLLM_DETAILED_TRACES) whenever OTLP is on, bundle v36; relaunched lab-s8 with gpu regex `A100|RTX PRO 6000`, max $2.7/h (68 A100 misses; only offer was the excluded host 458442 at $3.64).
- 2026-09-10 20:23 KST — lab-s8 launched (unit seocho-vast-lab-s8). Gate CLEAR for our labels; a teammate's instance `gare2e-full-ondemand-a100-20260910-161803` is running on the shared key (their spend, same bill). First attempt found no 2×A100-80GB cuda≥13 offer; retrying every 60 s for up to 2 h.
- 2026-09-10 11:10 KST — local live check: lab-s7 hard-k1 spans pushed to the local collector (aisummit stack, 127.0.0.1:4317) → Tempo returns the trace (root agentic_os.case, nested chat/execute_tool/invoke_agent), search by service.name works. Appliance live check prepared: in-instance OTLP file receiver (otlp_file_receiver.py) on 127.0.0.1:4317, vLLM --otlp-traces-endpoint, exporter push; queue session-D = one pass medium-k4-otel (also first live pass with the DB timeout+index fix); lab-s8 launcher, bundle v35, 1.5 h ceiling ≈ $3.
- 2026-09-10 — added seocho.eval.agentic_os_trace (trace ids, traceparent), seocho.eval.agentic_os_otel (span builder, JSONL + OTLP exporters), scripts/benchmarks/export_agentic_os_otel.py; instrumented OpenAICompatibleNativeToolModel, LLMRequestTimer, AgentExecutionRecorder, and the four runners (case_trace + receipt.trace); appliance hooks (run_lab_pass.sh export, vLLM --otlp-traces-endpoint). Smoke: lab-s7 hard-k1 → 680 spans / 40 traces, 0 orphans, no long string attributes; OTLP in-memory export preserves ids, parents, timestamps. Tests: tests/seocho/test_agentic_os_otel.py (6) + appliance shell assert; regression suites green.

## Result (Claude)
Receipt-derived OTel span trees for every pass (`otel/spans.jsonl`), OTLP push + vLLM tracing behind `SEOCHO_OTLP_ENDPOINT`. Not yet validated against a real collector.

## Decision needed (Claude -> user)
Where should spans go? Options: a Tempo/Grafana collector you run, or an OTLP endpoint reachable from Vast instances (the appliance must reach it over the public network).
