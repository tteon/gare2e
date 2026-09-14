# Lab board

| id | bead | title | state | waiting on | next |
|---|---|---|---|---|---|
| 001 | seocho-q174 | One-case gate criterion for raw B0 | approved (A) | — | close after first attempted-criterion run |
| 002 | seocho-hijp | Persistent Vast instance (queue mode) | done | — | queue mode is the default for every session |
| 003 | seocho-gevz | SEOCHO-on arm vs one-case gate | feasibility | user: S1 or S4 first | gate validator `--arm` option |
| 004 | seocho-gj7d | vLLM serving-configuration matrix (V-0..V-8) | superseded by 008 | — | — |
| 005 | seocho-6evc | Attempt-window telemetry analysis pipeline | done | — | rerun on 013 data |
| 006 | seocho-he9d | Long-context / context-engineering view (Graph-CoT vs Text2Cypher) | running | — | per-turn context + cache-hit panel |
| 007 | seocho-nxv6 | Difficulty-stratified cohort (easy/medium/hard) | done | — | strata-v2 frozen under 013 |
| 008 | seocho-6fqe | Serving-shape study (A: concurrency/spec/prefix; B: NVLink, replicas, LMCache) | done (lab-s4/s5/s6) | — | recommendation: TP=2 monolith, K=4 |
| 009 | seocho-ds17 | Stratified serving profile (lab-s7) | done | — | bottleneck study → 011/012 |
| 010 | seocho-hnve | OTel tracing for the agentic e2e | done (lab-s8 + lab-s11) | — | 99.3 % client↔server span join; service.name leak fixed (validate next pass, 011) |
| 011 | seocho-98kt | Observability improvements from the OTel live trace | approved | — | service-name fix done (unvalidated live); items 1–3 next |
| 012 | seocho-pe6s | SEOCHO product fixes (DB boundary, indexes, anchor guard, repair loop, retrieval trigger) | running | — | items 1–3 validated on lab-s11 (close on landing); item 4 repair loop (seocho-7w0m) next, then item 5 (seocho-xp47) |
| 013 | seocho-sudv | Wider cohort — strata-v2, all-k4 + all-k8, bundle v40 | done (lab-s11, $6.2) | — | 012 items 4–5 next; report https://claude.ai/code/artifact/04719482-b362-477d-b154-769afb6bb263 |
| 014 | seocho-ezdh | Experiment 0 — observability baseline (4-layer shares, K=4…32 sweep, async, row-cap, CPU) | done (lab-s11 + lab-s12, $12.7) | — | report https://claude.ai/code/artifact/408db85a-26ac-4341-ac61-985310a48b70; feeds 011 (per-process CPU) and 012 (DB cost guard) |
| 015 | seocho-wqt1 | Schema context axis — B0 / S1 / S4 / C- on strata-v2 | done (lab-s13, $4.9) | — | report https://claude.ai/code/artifact/d9af04dd-cc57-409e-8d9e-9a35f11f8441; S1 triples GRBench answers, control collapses them, SPARQL unmoved |
| 016 | seocho-ijkj | O1 ontology-enriched arm (single lanes) | done (lab-s15 salvage) | — | O1 ≈ S1 on Cypher (50 vs 56 / 90), hurts SPARQL; schema axis closed |
| 017 | seocho-1i7q | Graph-CoT hop-action lane vs iterative Text2Cypher (B0, O1) | done (lab-s16, $6.6) | — | hops 47 / 90 at half the wall, 0 DB risk; budget accounting follow-up |
| 018 | seocho-f592 | Entity-centric profile → routing input | done (offline) | user: run a routing experiment? | router memo in the request; next = route the 90 questions by family |
| 019 | — | Request-analysis metrics + router policy design (oracle 69/90, S1 56, family-routed 59) | feasibility | user: approve build + eval ≈ $4; budget fairness first? | request_analysis module → router runner → 90 + 50 held-out |
| 020 | — | Text2Cypher serving axes (prefix cache, chunked prefill, block size, spec decoding variants, grammar Cypher) | feasibility | user: approve session A ≈ $6 | preflight MTP/eagle3/grammar → session A restart-only arms |
| 021 | seocho-qenu, seocho-5epj | Agent ↔ DozerDB interface | done (lab-s17 $2.0 + lab-s18 $1.7) | — | no scans; zero-row = pattern errors; hint A/B: hard 5 → 9, total 52 → 54 (flat), ≈ 10 ms/hint; ship in router lane, off in 020 |
| 020 | seocho-9jc7 | Text2Cypher serving axes | done (lab-s19 $4.2 + grammar in lab-s21) | Claude: serving report page | MTP −30 % TPOT at parity; ngram corrupts; prefix off +15 %; grammar removes scope/label errors (+2–4 answered) but TPOT ×1.9–3.7 on this stack; query-first protocol lifts 9B 20 → 45 |
| 022 | seocho-s79g | Three-model comparison | done (lab-s20, $2.1); 9B grammar passes in lab-s21 | Claude: fold into serving report | 27B 56 vs 9B 20 vs 35B-A3B 20 answered; small models 2–3× faster tokens but fail the scope protocol (137–171 rejections); draft_model infeasible (vLLM turns qwen3_5 drafts into MTP) |
| 019 | seocho-d3ks | Router policy + request-analysis evaluation | done (lab-s21, $3.0) | user: policy V1 re-run on held-out (≈ $1)? | V0 = 56/90 in-sample (hard 12), 31/50 held-out vs S1 56 / 34; rejections −80 %, tokens −22 %, wall −21 %; O1+rowcap50 fixes the argmax family (5/7 vs 0/7); count turn cap and hop lookups are the misroutes |

Updated: 2026-09-12 14:55 by Claude. Ledger ≈ $91.8. Live: ours none.

Landing/bug beads outside the board: seocho-56wk (land V13-V17 fixes), seocho-e0ew (workspace mismatch), seocho-ffl4 (receipt token leak), seocho-npyq (multi-agent llm_ms attribution), seocho-twoh (wrapper exit 3), seocho-xvrr (trace packer / S3 sharing — lab-s8 + lab-s11 uploaded; colleague read access still to grant), seocho-h0yv (lab-resume/trace-share skills + secret-guard hook).

### 2026-09-13 continuation

| id | bead | title | state | waiting on | next |
|---|---|---|---|---|---|
| 023 | seocho-65n1 | Free throughput harness repair | implemented; local tests passed | paid measurement approval | opt-in dynamic/persistent runner, backlog window and exact request stages; replay/stub preparation remains |

No paid execution or Vast operation in lab 023. See its Result for the pre-existing
identity-check gap in basic CI; implementation is not throughput evidence.

- 024: owner approved five ~\$13 throughput comparisons; free preparation and explanatory memory/cache observability implemented. Paid launch pending final staging. Bead seocho-7yaz; see request024 and HANDOFF§11.

### Latest: lab024 measured partial (2026-09-13 evening)

- 024 / seocho-7yaz: 22 comparisons + gate/capture finished. Three-view report in
  reports/lab024-characterization; runtime/agent/tool records linked. No owned
  instance running; watchdog teardown verified. $12.1227 estimated, ~$114.92 cumulative.
- TP2 versus replicas2 and extra grammar K8 remain unrun; separate budget extension
  needed beyond original ~$13. Latest task/tool instrumentation is implemented-only.
- Collection partial provenance reconciled; exact archive selection/checkpoint fix
  locally validated. See HANDOFF§12 and request024 Result; earlier snapshots above
  describe their checkpoint only.

- 2026-09-14 latest024: extension$5.40/90min APPROVED; source2f99840c and v54
  preflight ready. lab-s23 admission found no compatible A100 pair; no instance
  created,$0 additional spend. Five remaining cells unrun. Inventory blocked,
  not approval pending; no automatic polling watcher. See latest HANDOFF§12.
