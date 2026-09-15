---
id: 020
title: Text2Cypher serving axes — prefix caching, chunked prefill, block size, speculative decoding variants, grammar-constrained Cypher
state: running (lab-s19)
created: 2026-09-12
owner_intent: user
bead: seocho-9jc7
---

## Intent (user)
Chat 2026-09-12: park GTSQA; take Text2Cypher and work the vLLM side — prefix caching, chunked prefill, block size, speculative decoding (several algorithms), guided decoding (xgrammar-constrained Cypher).

## Where we stand
Serving profile of the Text2Cypher lane (labs 013–015): decode 0.82–0.93 of wall, prefill 0.04–0.09, prefix-cache hit 0.50–0.72, ITL 23–25 ms at K=4–8 on A100/H100 pairs, 330 output tokens per case over ~5 requests; ngram speculative decoding (lab-s4/s7) gave +47–70 % decode speed but changed greedy Cypher (validated 100 → 50 %) — parity failed.

## Preflight (2026-09-12, free)
- vLLM 0.27.1 speculative methods: ngram, suffix, eagle, eagle3, medusa, mlp_speculator, draft_model, mtp family incl. **qwen3_5_mtp** (Qwen3.6-27B is model_type qwen3_5). **The checkpoint ships MTP weights** (`text_config.mtp_num_hidden_layers`, 15 `mtp.*` tensors) → `--speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":1..3}'` is available natively. Community EAGLE3 heads exist (e.g. `Dogacel/specdrift-qwen3.6-27b-eagle3`, `VirVen/Qwen3.6-27B-EAGLE3`); unverified quality, treat as a third-party artifact.
- Structured outputs backends available: xgrammar, guidance, outlines, lm-format-enforcer (auto). Grammar-constrained generation is possible for a plain-text response (EBNF); inside a JSON tool-call argument only the JSON schema is enforced, so a "grammar Cypher" arm means the query is emitted as a constrained plain response, not a tool call.
- Qwen3.6 family: 27B, 27B-FP8, 35B-A3B, 35B-A3B-FP8 — no small dense draft; 35B-A3B (3 B active) is the only same-family draft candidate (memory: 27B bf16 54 GB + 35B-A3B FP8 ≈ 35 GB fits on 2× 80 GB).

## Axes and what each must show
| axis | arms | judged on |
|---|---|---|
| prefix caching | on (default) vs off | prefill share, TTFT, tok/s; funnel must be identical |
| chunked prefill | default on vs `--max-num-batched-tokens` 2048 / 8192 | TTFT vs ITL trade at K=8 |
| block size | 16 (default) / 32 / 128 | prefix-hit ratio (hit granularity), KV usage, tok/s |
| speculative decoding | ngram (re-run with parity check), **suffix**, **qwen3_5_mtp** if weights exist, draft_model (35B-A3B-FP8), eagle3 if a head exists | decode tok/s, TPOT, acceptance rate / mean accepted length, **and** validated / non-empty / answered parity vs baseline; report per L-class as the colleague's table proposes |
| guided decoding | xgrammar Cypher grammar (read-only subset: MATCH … WHERE … RETURN … LIMIT, our scope pattern mandatory) vs free-form tool call | validated rate (should approach 1.0), unanchored_scan / missing_workspace_scope counts (should be 0 by construction), answered, output tokens, decode speed (constrained decoding masks logits — measure the cost) |
Parity rule (from lab-s4/s7): an arm that changes greedy outputs must be shown with its funnel; a speedup that costs validated queries is not a speedup. Also check `VLLM_BATCH_INVARIANT` / seed determinism as the control for "spec decoding changed the query" (it may have been batch nondeterminism).

## Feasibility (Claude)
- verdict: two sessions. Session A (≈ 1.5 h, ≈ $6): prefix off, chunked-prefill 2 settings, block size 3 settings, ngram + suffix spec — all restart-only flags (`VLLM_EXTRA_ARGS_OVERRIDE`), medium stratum × grbench-single at K=8 (30 cases, ≈ 4 min per arm). Session B (≈ 1.5 h, ≈ $6): MTP / draft / eagle3 as available (needs bundle work: draft model download, `--speculative-config`), grammar-Cypher arm (needs a new runner mode + grammar file + tests, ≈ 1 day).
- risk: MTP weights absent → drop; grammar arm may reject valid Cypher shapes → grammar iteration on the 40 lab-s13 S1 queries first (offline, we have their digests only — regenerate from the private predictions if needed).

## Approval (user)
approved: yes   # chat 2026-09-13 "네 모두 진행해주세요" (sessions A and B)
budget_usd:          # A ≈ 6, B ≈ 6

## Plan (Claude)
1. Preflight: MTP weights, eagle3 head, xgrammar EBNF for the read-only Cypher subset (offline). 2. Session A queue (restart-only arms) on strata-v2 medium, grbench-single. 3. Grammar runner mode + tests; Session B. 4. Report per arm × class with parity.

## Log (Claude)
- 2026-09-13 12:50 KST — report page "Faster Tokens, Fewer Tokens" (labs 019–022, Korean): https://claude.ai/code/artifact/30a1e812-1e31-4426-8fd9-ba71bfd82189
- 2026-09-13 12:29 KST — **grammar A/B/C + retries done in lab-s21** (see 019 for the session). grammar-xgr 456 s, grammar-none 217 s, grammar-onto 391 s (27B, 90 each); m9b-grammar-none 153 s, m9b-grammar-onto 471 s. `pip install arctic-inference==0.1.1` succeeded (99 s) but the suffix arm still failed to start in 20 s (cause not captured — its stderr was not in the a9 tail cmd); batch-invariant failed again in 52 s with `WorkerProc initialization failed` in the engine core (stderr tail captured; root cause line not in the grep window — likely an attention-backend restriction under `VLLM_BATCH_INVARIANT=1` with TP=2). Both parked.
- 2026-09-13 10:30 KST — **lab-s19 done** (instance 50823574, host 458442 A100 SXM4 NVLink, $3.14/h, 09:08 → 10:29 ≈ 81 min ≈ $4.2). 8 / 10 arms ran (a7 suffix: vLLM refused to start — `arctic-inference` package missing from the image; a9 batch-invariant: start failed in 70 s, cause not in the essential set). Essential-only collection 7 934 files verified; destroyed + verified. Package `s3://…/traces/lab-s19-try-090808` (861 files, 12 MB; index 9 runs). Tables: `runs/lab-s19-try-090808/session-summary/{serving-arms,scheduler-occupancy}.md`.
- 2026-09-13 10:40 KST — **guided-decoding design widened to A/B/C (gare2e 54c5ae22, bundle v50)** after the user's question on ontology-derived constraints: A = query-first protocol unconstrained (`none`), B = read-only Cypher grammar (`xgrammar`: scoped `GRBenchNode` label, `GRBENCH_REL`, no writes/CALL/UNION), C = the same grammar with property / relation_name / node_type names closed over the GRBench vocabulary from the O1 ontology (`xgrammar-ontology`; typed comparisons only admit in-schema literals; domain/range stays with the executor's zero-row diagnostic — a CFG cannot type variables). All three run in lab-s21 (session-O) on S1 × 90 × K=8. Judged on: validated rate and rejection codes, answered by stratum, repair / empty-retry turns, output tokens per query turn, TPOT (mask cost), first-compile vs cached requests, and "grammar refused a needed shape" (terminal / rejection audit). Also added `summarize_agentic_os_scheduler_occupancy.py` (running / waiting / KV / GPU util per pass) for the "how full is the decode batch" reading the user asked for: lab-s18 K=8 shows running mean 5.0, waiting 0, KV 1.4 % — the batch is ≈ 63 % full because agents are between turns; headroom is in the agent loop, not the scheduler.
- 2026-09-13 10:05 KST — **grammar-Cypher arm built (gare2e 8db567be, bundle v48)**: `src/seocho/eval/cypher_grammar.py` (read-only Cypher EBNF for xgrammar; every labelled node forced to the scoped `GRBenchNode` form; no writes / CALL / UNION; validated offline against the Qwen3.6 tokenizer, 11/11 agent shapes accepted, 6/6 violations rejected) + `scripts/benchmarks/run_grbench_grammar_cypher.py` (query-first plain-response protocol; `--constrained xgrammar` sends `structured_outputs.grammar` on query turns, `none` is the protocol-only control; first non-empty result → one unconstrained answer turn) + lane `grbench-grammar` (schedule + e2e `SEOCHO_GRAMMAR_CONSTRAINED`). Rehearsed on a mock vLLM + throwaway DozerDB. **Session B re-scoped:** MTP moved into session A (a8), draft_model moved to lab 022 (session-N), EAGLE3 dropped (no EAGLE3 head for Qwen3.6-27B on HF — only MTP re-packagings), grammar arm runs in lab-s21 (session-O: grammar-xgr-s1-k8 vs grammar-none-s1-k8, S1 × 90 × K=8, hint off) alongside the 019 router passes.
- 2026-09-13 09:08 KST — **lab-s19 launched** (bead seocho-9jc7, unit seocho-vast-lab-s19, bundle v46, instance 50823574 on host 458442 A100 NVLink $3.14/h). Queue session-M, all grbench-single × S1 × 90 × K=8, hint off: a0 baseline · a1 `--no-enable-prefix-caching` · a2/a3 `--max-num-batched-tokens` 2048/8192 · a4/a5 `--block-size` 32/128 · a6 ngram (k=4, lookup 2–4, as lab-s4) · a7 suffix (defaults) · a8 `qwen3_5_mtp` k=2 (checkpoint mtp_num_hidden_layers=1) · a9 baseline + `VLLM_BATCH_INVARIANT=1`. Each arm restarts vLLM (≈ 1.5–4 min). Judged per arm: funnel parity vs a0, tok/s, ITL, prefill share, prefix hit, KV usage, acceptance where spec.
- 2026-09-12 12:10 KST — drafted from chat; vLLM/HF preflight run: MTP weights present in Qwen3.6-27B; vLLM method qwen3_5_mtp; suffix decoding available; xgrammar present; community EAGLE3 heads exist.

## Result (Claude)
### Session A (lab-s19, 2026-09-13): serving arms on S1 × 90 × K=8, one pass each, hint off
| arm | answered e/m/h (total) | rejections (policy + DB) | zero-row | per-case wall p50 s | TPOT p50 ms | gen tok/s | prefix hit | running mean /8 | GPU util | KV max |
|---|---|---|---|---|---|---|---|---|---|---|
| a0 baseline (`--max-num-seqs 8`) | 20/27/5 (52) | 39 | 62 | 8.6 | 27.7 | 170 | 0.48 | 4.5 | 93 % | 2.7 % |
| a1 prefix caching off | 18/28/7 (53) | 40 | 58 | 10.3 | 31.8 | 170 | 0.00 | 5.1 | 96 % | 2.5 % |
| a2 chunked prefill 2048 | 18/29/5 (52) | 42 | 61 | 9.0 | 28.0 | 190 | 0.48 | 5.1 | 95 % | 2.6 % |
| a3 chunked prefill 8192 | 20/29/7 (56) | 38 | 57 | 9.4 | 29.0 | 190 | 0.47 | 5.3 | 94 % | 2.7 % |
| a4 block size 32 | 18/29/5 (52) | 42 | 59 | 9.4 | 28.1 | 179 | 0.48 | 4.9 | 93 % | 3.2 % |
| a5 block size 128 | 19/29/5 (53) | 49 | 61 | 10.0 | 28.3 | 199 | 0.50 | 5.4 | 94 % | 2.8 % |
| a6 ngram spec (k=4, lookup 2–4) | 5/12/1 (**18**) | **186** (45 unsupported_query_shape, 25 missing_return, 82 DB errors) | 30 | 18.8 | 36.8 | 159 | 0.51 | 5.5 | **62 %** | 7.9 % |
| a7 suffix spec | — did not start (arctic-inference missing) | | | | | | | | | |
| **a8 qwen3_5_mtp (k=2)** | 18/28/5 (51) | 40 | 63 | **7.0** | **19.5** | **234** | 0.06 | 4.2 | 91 % | 5.6 % |
| a9 batch-invariant control | — did not start | | | | | | | | | |

Output tokens per pass 39–44 k for every working arm except a6 (55 k); input tokens 483–502 k (a6 583 k). Pass wall is not comparable across arms (the first restart after a cold start compiles for ≈ 230 s, later ones ≈ 80 s); per-case wall and TPOT are.

**Readings**
- **MTP is the only arm that moves decode.** Per-turn TPOT 27.7 → 19.5 ms (−30 %), per-case wall 8.6 → 7.0 s (−18 %), generation throughput +37 % at the same K, with the funnel intact (51 vs 52 answered, same rejection mix, same zero-row count). That is ≈ 1.4 effective tokens per step from one MTP layer used twice — the "faster tokens" lever the talk needs, and it comes from weights already in the checkpoint. Side effect to explain: the prefix-cache hit ratio fell to 0.06 under MTP (vLLM does not count speculative-verified prefixes the same way, or the draft path bypasses the cache); the speed-up survives it.
- **ngram is not a speed-up here; it is a failure.** At K=8 with 1–3 k-token contexts the CPU-side lookup stalls the GPU (util 93 → 62 %) and the accepted drafts corrupt the Cypher: 45 responses were not even a query shape, 25 lacked RETURN, 82 failed in DozerDB — answered 18 / 90. This confirms and explains lab-s4/s7's parity failure: it was not batch nondeterminism (a9 could not run, but the magnitude here rules that out). Do not ship ngram for tool-calling agents on this stack.
- **Prefix caching is worth more than its prefill share suggests.** Turning it off costs +15 % TPOT and +20 % per-case wall although prefill is only 0.04–0.11 of wall: the recomputed prefill chunks are interleaved into everyone's decode steps at K=8, so the price shows up as ITL, not as prefill time. Keep it on; the KV footprint (≤ 3 %) is negligible.
- **Chunked-prefill size and block size are not levers** at this context length and K: TPOT 28–29 ms in every setting, answered within ±4 (run-to-run band). 8192 batched tokens gave the best answered count (56) but that is one run.
- **Scheduler headroom (the user's question):** running averages 4.2–5.5 of 8 with waiting ≈ 0 and KV ≤ 8 % in every arm — the batch is 55–70 % full and never queued. The empty slots are agents between turns (tool call, DozerDB, prompt assembly), not vLLM capacity. Raising K fills the batch at the cost of per-request latency (lab 014); the other route is shortening the agent gap (fewer turns via routing / hints, or overlapping the next prefill).

### Guided decoding A / B / C (lab-s21; query-first plain-response protocol, S1 context in the system prompt, 90 questions, K=8)
| pass | model | answered e/m/h (total) | policy + DB rejections | zero-row events | query-turn TPOT p50 | answer-turn TPOT | per-case wall p50 | out tokens | GPU util | gen tok/s |
|---|---|---|---|---|---|---|---|---|---|---|
| tool-calling S1 (reference, lab-s20) | 27B | 19/29/8 (56) | 48 | 57 | 27.9 ms | — | 9.0 s | 41 k | 95 % | 180 |
| A none (protocol only) | 27B | 13/22/11 (46) | 38 (scope 2, CALL 3, unanchored 5, DB 28) | 150 | 26.1 ms | 39.9 ms | 10.4 s | 35 k | 75 % | 165 |
| B xgrammar | 27B | 17/20/11 (48) | 16 (**scope 0, label 0**; unanchored 6, missing_return 4 = truncated at 512, DB 6) | 154 | **67.7 ms** | 79.6 ms | 14.2 s | 41 k | 57 % | 91 |
| C xgrammar-ontology | 27B | 18/21/11 (50) | 16 (scope 0, label 0; unanchored 5, missing_return 4, DB 7) | 148 | **49.4 ms** | 54.6 ms | 11.6 s | 41 k | 63 % | 106 |
| tool-calling S1 (reference, lab-s20) | 9B | 5/12/3 (20) | 267 (scope 171, missing_return 50) | 61 | 10.5 ms | — | 6.4 s | 56 k | 85 % | 517 |
| A none | 9B | 14/20/11 (**45**) | 66 (scope 11, unanchored 5, DB 49) | 127 | 9.7 ms | 17.5 ms | **3.6 s** | 38 k | 49 % | 258 |
| C xgrammar-ontology | 9B | 17/20/11 (48) | 31 (scope 0; missing_return 24 = truncated, DB 7) | 140 | **35.8 ms** | 38.4 ms | 13.1 s | **92 k** | 27 % | 198 |

First constrained request per pass waited 84–141 s for the grammar compile (later requests 0.8–1.4 s: cached).

**Readings**
- **The grammar does exactly what it promises and it is not the win.** Scope and label rejections go to zero by construction (27B: 2 → 0; 9B: 11 → 0 under the same protocol, 171 → 0 vs tool calling), DB errors fall 28 → 6–7 because malformed Cypher never reaches the driver, and answered rises +2 to +4. But the per-token cost is 1.9–2.6× for the 27B (26 → 49 / 68 ms) and 3.7× for the 9B (9.7 → 36 ms); GPU utilisation falls to 57–63 % (27B) and 27 % (9B), and the *unconstrained* answer turns in the same batch slow down too (40 → 55–80 ms) — the token-mask work is CPU-side and stalls the whole engine step at K=8. Net per-case wall +11 % (C) to +37 % (B) for the 27B and +260 % for the 9B. Under vLLM 0.27's xgrammar backend at this vocabulary (248 k) and concurrency, guided decoding buys correctness of form and pays for it in decode time; it is a fit for low-K or CPU-rich serving, not for the K=8 appliance.
- **C beats B**: closing the vocabulary makes the masks cheaper (fewer live tokens per state) and adds +2 answered; if a grammar is used at all, derive it from the ontology.
- **The protocol change matters more than the grammar for the small model.** Query-first plain response lifts the 9B from 20 → 45 answered at 3.6 s per case — 2.5× faster than the 27B on tool calling at 80 % of its answers — because the tool-calling protocol, not Cypher, was what the 9B could not follow (171 dropped scope maps). With the grammar the 9B answers 48 but rambles inside the grammar (31 truncated turns at 512 tokens, 92 k output tokens) and loses the speed.
- **Query-first costs the 27B answers** (56 → 46–50) and triples zero-row events (57 → 150): without the schema tool call the model guesses relation names more often. The hint was off in these passes; the router's Text2Cypher lane (hint on) is the right place to combine them.
- **For the talk:** "constrained decoding removes a class of repair turns, but on this stack each remaining token costs 2–4× more; the cheaper way to remove those turns was to change the protocol (small model) or the schema context (large model)."


### Re-read for throughput (2026-09-13 13:30, after review feedback) — server-side stage timings and request supply
vLLM `llm_request` spans (collector-spans.jsonl, 3 455 requests) give per-request queue / TTFT / prefill / decode; the coordinator receipts give runner start/finish per case.

| arm | queue p50 | TTFT p50 | prefill p50 | **decode-only TPOT p50** (decode time ÷ (completion−1)) | p90 | completion tok per decode-second | active runners mean /8 | seconds with < 8 active | dead time per case |
|---|---|---|---|---|---|---|---|---|---|
| a0 baseline | 0 | 155 ms | 133 ms | **25.3 ms** | 29.4 | 39.3 | 5.9 | 56 % | 1.93 s |
| a1 prefix off | 0 | 214 | 180 | **28.8** | 34.5 | 34.8 | 6.7 | 48 % | 1.93 |
| a2 chunked 2048 | 0 | 153 | 133 | 25.5 | 30.4 | 38.9 | 6.5 | 49 % | 1.93 |
| a3 chunked 8192 | 0 | 156 | 133 | 26.4 | 30.9 | 38.1 | 6.5 | 41 % | 1.92 |
| a4 block 32 | 0 | 155 | 133 | 25.5 | 30.7 | 38.9 | 6.3 | 50 % | 1.93 |
| a5 block 128 | 0 | 153 | 133 | 26.0 | 31.4 | 38.6 | 6.8 | 32 % | 1.93 |
| a6 ngram | 0 | 184 | 114 | 32.3 | 57.0 | 28.9 | 6.6 | 36 % | 1.92 |
| a8 MTP k=2 | 0 | 258 | 223 | **15.7** | 21.8 | 62.4 | 6.5 | 47 % | 1.93 |

- **MTP holds up on pure decode:** server-side decode TPOT 25.3 → 15.7 ms (−38 %), p90 29.4 → 21.8; the cost shows in prefill (+90 ms per request for the draft pass). The client-side 27.7 → 19.5 ms figure was diluted by TTFT, not inflated.
- **Prefix caching off costs decode too** (25.3 → 28.8 ms) because the recomputed prefill chunks are interleaved into every decode step at K=8 — it is not only a TTFT effect.
- **Block size 128's +17 % aggregate tok/s is a supply artefact:** decode TPOT is unchanged (26.0 vs 25.3); the pass simply kept more runners active (6.8 vs 5.9 of 8; the under-8 tail began at 162 s vs 114 s). The aggregate tok/s column must not be compared across arms until supply is held constant.
- **Request supply is the bottleneck of the measurement, not of the server:** queue time is 0 in every arm; each case carries 1.93 s of dead time (1.27 s coordinator gap between runner processes + 0.66 s runner start-up) against ≈ 9 s of case wall, and the static 8-shard split leaves the pass with fewer than 8 active runners for 32–56 % of its seconds (shards finish at different times). That, not DozerDB (≤ 0.1 of wall), explains running ≈ 5 / 8. Fixes are free: a shared work queue (dynamic assignment), a persistent runner per shard, and a steady-state measurement window with a backlog.

**Open:** suffix decoding needs `pip install arctic-inference==0.1.1` (added to session-O as a retry); the batch-invariant control's start-up error is re-tried in session-O with its stderr tail captured. Session B is folded into lab-s21 (grammar A/B/C) and lab-s20 (draft model vs MTP).

## Decision needed (Claude -> user)
Approve Session A (restart-only serving arms, ≈ $6) now? Grammar-Cypher and MTP/draft after the preflight says they are possible.
