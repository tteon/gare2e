---
id: 008
title: Serving-shape study — decode-bound single vs multi, concurrency, PD/KV transfer, shared prefix, speculative decoding
state: running
created: 2026-09-09
owner_intent: user
bead: seocho-6fqe
---

## Intent (user)
(draft by Claude — edit freely) Look at the workload through the KV-cache / serving lens: single-agent
CoT/Graph-CoT with reasoning is decode-heavy; multi-agent manager→specialist looks like a candidate
for prefill/decode disaggregation, P2P KV transfer, or KV sharing. Find where there is real headroom
and run the experiments.

## Acceptance (user; Claude may draft)
- [x] Composition table (prefill vs decode, prompt vs generated tokens, prefix hit) per topology — done from lab-s3.
- [ ] Concurrency sweep K = 1, 2, 4, 8 per topology on the TP=2 appliance: TTFT, ITL, throughput, GPU busy.
- [ ] Shared-prefix prompt layout (manager/specialist/tool schema/schema slice as one immutable prefix): prefix-hit and TTFT delta.
- [ ] n-gram speculative decoding on specialist structured output: decode s/req delta with funnel guard.
- [ ] Session B on 2×80 GB: TP=2 monolith vs 2× TP=1 replicas (round-robin) vs 2× TP=1 replicas + P2P KV sharing (LMCache/Mooncake, NIXL transport) at K = 1/4/8; PD (prefill/decode split) only as an optional fourth arm — on homogeneous GPUs its case is ITL isolation, not economics.

## Constraints (user)
- Model/revision fixed; compare with funnel guard (same retrieval outcome) before latency.

## Feasibility (Claude)
- verdict: the framing needs one correction, then it is a good study.
- what lab-s3 says (N≈30 per topology, reasoning off; gates for reasoning on):
  - prefill is 0.2–0.7 s/req everywhere; decode is 1.5–2.4 s/req (single, non-reasoning), 9.4–9.5 s/req (multi, non-reasoning), 16–22 s/req (any topology with reasoning). ITL is flat at ~25 ms. Every cell is decode-bound at batch 1.
  - multi is not prefill-heavy: it generates 5× more tokens per request than single (370 vs 70; 680–870 with reasoning) because the specialist emits structured queries/evidence and repairs. Its prefix hit is *lower* (0.36–0.50 vs 0.54–0.57) because manager and specialist carry different instruction prefixes.
  - so at the current batch-1, serialized design, PD disaggregation and P2P KV transfer have nothing to move: there is no prefill pressure and KV usage is ≤ 3 %. They become relevant only when cases/agents run concurrently and prefill bursts (1.5–2.6k tokens) interleave with decode streams.
- headroom that does exist now: (a) decode volume — speculative decoding (n-gram fits repetitive Cypher/SPARQL/JSON), thinking budget, finalisation rule; (b) prefix reuse — a shared immutable prefix across manager/specialist and across turns; (c) Graph-CoT hop tools would shift single-agent work from long decode to many short prefill-heavy hops, exactly where prefix caching helps — worth testing against single-shot Text2Cypher.
- hardware: PD needs each GPU to hold the whole model; Qwen3.6-27B BF16 (≈51 GB) does not fit a 40 GB A100, so PD experiments need 2×80 GB (A100-80GB SXM ≈ $2.5–3.3/h ×2, or H100) or an INT8 weight variant (a different model — must be labelled as such).
- estimated cost/time: concurrency + shared-prefix + spec-decode on the current appliance ≈ 3 h ≈ $5; PD vs monolith on 2×80 GB ≈ 3 h ≈ $10–15.

## Approval (user)
approved: yes  # chat 2026-09-09: A then B; B budget approved
budget_usd: 25  # A ≈ 10 (5.5 h ceiling on A100×2), B ≈ 15 (2×80 GB)

## Plan (Claude)
1. Free: publish the composition table (done) and design the concurrency runner (parallel coordinators over case shards; per-K vLLM `--max-num-seqs`).
2. Session A (current appliance): K sweep × {single, multi} × {baseline, shared-prefix, ngram-spec} via the serving-sweep YAML; funnel guard on.
3. Session B (2×80 GB): (a) TP=2 monolith, (b) 2× TP=1 replicas with a round-robin router (no sharing), (c) replicas + LMCache/Mooncake P2P KV sharing; K = 1/4/8; same cells. (d) PD split optional. Questions: does TP=2 allreduce over PCIe cost more than it gives at batch ≤ 8 (decode-bound)? how much prefill does cross-replica sharing of the common prefix (system + tools + schema slice ≈ 1k tokens) save? Note: requests within one case are sequential and naturally sticky to one replica, so sharing only helps the *cross-case* common prefix — the replica-level twin of the shared_prefix profile.
4. Add Graph-CoT hop lane as the single-agent "prefill-shifted" variant (ties to 006 CE-5).

## Log (Claude)
- 2026-09-10 05:45 KST — session B report published: https://claude.ai/code/artifact/f4514d49-739b-4104-baae-843505c3605b (tables in runs/lab-s5-try-001032 and runs/lab-s6-try-024640 session-summary/). Request 008 is complete except the spec-decoding parity study (deferred; separate request if wanted).
- 2026-09-10 05:30 KST — session B2 (lab-s6) done: 5 replica passes, all 40 attempts/pass ok, $5.06; instance destroyed. Session B total $10.48 (+$0.54 try 1). **Verdict: TP=2 monolith wins on this box at every K.** Pass wall mono vs rep-sticky vs rep-rr: K=1 2409 / 3501 / – s; K=4 831 / 1191 / 1107; K=8 647 / 885 / 776. Per-seq decode TP=1 ≈ 27 tok/s vs TP=2 45 (0.6×); request p50 2.7 s vs 1.5 s; GPU energy +25–35 %. Two TP=1 replicas never recover the halved per-token bandwidth at K ≤ 8 because both stay under-utilised (34–67 % util), while the NVLink allreduce of TP=2 costs almost nothing. Routing: rr beats sticky by 7–12 % throughput at K=4/8 despite prefix hit 0.35–0.40 vs 0.50 — load balance matters more than prefix locality when prefill is ≈0.2 s/request; TTFT +0.03–0.04 s. Funnel parity held (GRBench single 100 % validated/executed/non-empty in all 8 arms). Cross-replica KV sharing (LMCache) is unavailable for Qwen3.6 (hybrid KV specs) and, given the small rr-vs-sticky gap, would have bought ≤5 %. Serving recommendation for this workload/hardware: one TP=2 engine per 2-GPU box at K=4 (K=8 if latency budget allows); no replicas, no PD, no KV transfer.
- 2026-09-10 02:58 KST — session B2 launched (unit seocho-vast-lab-s6, bundle v31 with the router fix, queue session-B2 = rep-sticky k1/4/8 + rep-rr k4/8, 3 h ceiling ≈ $6 max, expected ≈ 1.2 h / $2.5). Remaining approved budget before B2 ≈ $13.3.
- 2026-09-10 02:50 KST — lab-s5 try 2 ran 2.5 h ($4.88) on host 639771 (the lab-s4 host; NV12, `nccl_env` auto → P2P **on**). Monitor watched a stale run dir (lexical `ls | tail -1`), so no live events — fixed to mtime order. Results:
  - **NVLink question closed:** TP=2 with P2P on ≈ P2P off. Pass walls 2409/831/647 s (K=1/4/8) vs 2426/805/619; ITL 21.2/23.9/28.0 ms vs 21.2/24.7/31.0; TTFT 0.18–0.41 vs 0.25–0.74 s. The K=8 per-sequence cost is intrinsic batch/prefill cost, not the allreduce path.
  - **Replica passes (2× TP=1 + router) failed 3 of 4 lanes:** router passthrough dropped `Content-Type` on `POST /tokenize` → vLLM 400 → discovery-cache validation aborted GTSQA + multi lanes. GRBench single completed on all 5 replica passes: per-seq decode 27.5 tok/s (TP=1) vs 45.5 (TP=2); request p50 2.5 s vs 1.5 s; lane wall 133/117 s (K=4/8) vs 124/109 s mono; funnel identical. rr vs sticky: cached share 0.40 vs 0.51, p50 +5 % — prefix loss across replicas is cheap here (prefill ≈ 0.2 s).
  - **LMCache arm infeasible for Qwen3.6-27B:** `LMCacheConnectorV1` makes vLLM unify hybrid KV-cache specs and fails (`Failed to promote local KV cache specs to one unified type`) — KV connectors need uniform specs; Qwen3.6's hybrid attention layers rule out cross-instance KV sharing in vLLM 0.27. Arm dropped; sticky routing is the only prefix-preservation mechanism available.
  - Router fixed (headers forwarded), strict mock rehearsal passes; relaunch of the 5 replica passes as session B2 (~1.2 h, ≈ $2.5).
- 2026-09-10 00:10 KST — relaunch s5c aborted on the ownership gate: a teammate's instance (label gare2e-qwen38-27b-tp2-20260910-r2, 2× A100-80GB DGX Station, host 608456, $1.70/h — the same shared Vast key) was live; it went offline within minutes. Gate made label-scoped (ours = seocho-agentic-os-*), raw JSON no longer printed. Relaunched as unit s5d at 00:09; still no A100-80GB cuda≥13 offer (the teammate took the one that appeared). Shared-key billing: the account will show their spend alongside ours.
- 2026-09-10 00:05 KST — lab-s5 try 1 failed at service_start in 17 min ($0.54): host 60897 runs driver 525 / CUDA 12.0, the vllm-openai:v0.27.1 image is cu130 → `Error 803: unsupported display driver / cuda driver combination`; runner destroyed the instance (verified). Topology there was PHB (no NVLink) anyway. Relaunched (unit s5c) with offer filter `cuda_max_good>=13.0` and host 60897 excluded. Market at 00:00 KST: no other 2× A100-80GB; alternatives if the wait is long — 2× RTX PRO 6000 96 GB (Blackwell, cuda 13.2, PCIe) at $2.94–3.47/h ≈ $15–18 for 5–6 h, or 2× H100 SXM NVLink at $3.47 but cuda 12.4 (would hit the same 803).
- 2026-09-09 23:39 KST — relaunch (unit seocho-vast-lab-s5b) admitted on its first attempt: instance 50397116, host 60897, 2× A100 SXM4 80 GB, Czechia, ≈$1.90–2.0/h, offer bw_nvlink=0 (topology to be confirmed from gpu-topology.private.txt). Run dir runs/lab-s5-try-233856; cost watchdog deadline 7 h.
- 2026-09-09 23:40 KST — 28 admission attempts found no 2×A100-80GB offer with bw_nvlink>0 (market: 4 A100 pairs, one 80 GB pair on host 60897 at $1.90 with bw_nvlink=0). Relaunched without the NVLink filter, keeping 2×80 GB. Consequence: if the host has no NVLink, mono runs TP=2 over PCIe with P2P off (same as session A) and the 'TP=2 with NVLink' question stays open; the appliance records the real topology and nccl_env auto still enables P2P if NV links exist.
- 2026-09-09 23:06 KST — user: "go session B". Launched lab-s5 (systemd unit seocho-vast-lab-s5, bundle v30 sha 7c7d…, queue session-B: mono k1/4/8 · rep-sticky k1/4/8 · rep-rr k4/8 · rep-lmc-rr k4/8; offer filter bw_nvlink>0, 2×80 GB, ≤$2.25/h, 7 h ceiling ≈ $15.75 max against $18.7 remaining of the approved $25). Plumbing: vllm_router.py + replica/LMCache restart mode (gare2e ade8c538, bead seocho-tg5s). Skipped: local Docker appliance preflight for v29/v30 (same as v28; import preflight runs on the appliance at bootstrap).
- 2026-09-09 12:10 KST — session A report published: https://claude.ai/code/artifact/1f03c31d-4f3a-4bf5-b9af-e18f3e251863 ; tables in runs/lab-s4-try-074131/session-summary/ (scripts/benchmarks/summarize_agentic_os_queue_session.py, gare2e a70c4e61). Session A items closed: concurrency ✔, spec ✔ (fails parity), shared_prefix ✔ (no serving effect). Session B ready to plan on the 2×80 GB NVLink host with nccl_env.sh auto.
- 2026-09-09 11:40 KST — lab-s4 (session A) done: 8/8 passes ok, $6.27, instance destroyed. Headline: concurrency K=4 gives 3.0× wall / 2.7× less GPU energy with the funnel intact and only −6% per-seq decode speed; K=8 hits genuine batch cost (per-seq −13…−50%, p90 latency 2.7×) on TP=2 without NVLink. ngram spec decoding is +47–70% decode at K=1 but changed greedy Cypher/SPARQL outputs (GRBench single validated 100→50%) and is slower than base at K=4 → not adoptable without a parity study. shared_prefix has no serving effect (prefix hit 0.50→0.53; single lanes identical) and its wall gain in grbench-multi is a behaviour change (empty answers). Analysis artefacts under runs/lab-s4-try-074131/{run-*/analysis,run-*/catalog}; summary script to follow.
- 2026-09-09 09:47 KST — lab-s4 pass walls: k1-base 2426 s, k2 1255, k4 805, k8 619 (k8 tail-bound by 10-case lanes); k1-spec 1377 s (1.76× vs k1-base) with ngram acceptance length ≈3.5/step. Spec decoding looks like the biggest single-request lever for this workload; confirm quality parity + per-request ITL in the analysis.
- 2026-09-09 08:30 KST — user asked to fold NVLink gating into session B. Implemented `docker/agentic-os-appliance/nccl_env.sh` (bead seocho-2e4i): auto mode enables P2P only when all GPU pairs are NV*; explicit `NCCL_P2P_DISABLE` / `SEOCHO_NCCL_P2P_MODE=disable|enable` override; decision recorded in `gpu-topology.private.txt`. Session A (lab-s4) stays on the old bundle (P2P off), so B must re-run the TP=2 K-sweep as its own baseline.
- 2026-09-09 07:59 KST — lab-s4 host 639771 is 2× A100-SXM4-80GB with NVLink (NV12), $1.90/h. Appliance sets NCCL_P2P_DISABLE=1 unconditionally (PCIe-hang guard from V14/lab-s1), so the TP=2 baseline here runs allreduce without NVLink. Session B caveat: gate NCCL_P2P_DISABLE on topology (enable P2P when `nvidia-smi topo -m` shows NV*), otherwise TP=2 vs 2×TP=1 replicas is biased against TP=2.
- 2026-09-09 — user: on homogeneous GPUs PD needs heterogeneity to pay; prefer P2P KV sharing since prefixes are shared anyway. Agreed and re-planned B as monolith vs replicas vs replicas+P2P sharing, PD optional.
- 2026-09-09 — approved in chat. Session A queued as lab-s4: medium stratum, 4 tool lanes, reasoning off, 10 cases/lane per pass; passes k1/k2/k4/k8 baseline, k1/k4 ngram speculative, k1/k4 shared_prefix; vLLM restarted per pass with --max-num-seqs 8 (identical cold state). Outer GPU sampler disabled (nested-sampler bug).
- 2026-09-09 — drafted from chat; composition table computed from lab-s3 receipts.

## Result (Claude)
### Composition (lab-s3, PE-0, per request medians)
| lane | topology | reasoning | req/attempt | prompt tok | gen tok | prefill s | decode s | ITL ms | prefix hit |
|---|---|---|---|---|---|---|---|---|---|
| GRBench | single | off | 6 | 974 | 69 | 0.19 | 1.71 | 25 | 0.54 |
| GRBench | multi | off | 7 | 939 | 369 | 0.25 | 9.49 | 26 | 0.36 |
| GTSQA | single | off | 6 | 1882 | 94 | 0.74 | 2.36 | 25 | 0.57 |
| GTSQA | multi | off | 7 | 1524 | 367 | 0.67 | 9.42 | 25 | 0.50 |
| GRBench | single | on | 5.5 | 959 | 644 | 0.20 | 16.4 | 25 | 0.54 |
| GRBench | multi | on | 7 | 1594 | 678 | 0.21 | 17.3 | 25 | 0.70 |
| GTSQA | single | on | 5.5 | 1727 | 822 | 0.49 | 20.9 | 26 | 0.58 |
| GTSQA | multi | on | 7 | 2581 | 873 | 0.30 | 22.3 | 26 | 0.73 |

## Decision needed (Claude -> user)
Approve the plan order (A then B) and whether Session B (2×80 GB) is in budget.
