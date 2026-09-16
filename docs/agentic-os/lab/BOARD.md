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

- Latest024 live checkpoint: lab-s23b created51011956, source42ed5bd1/v55,
  built-in CUDA forward compatibility. Loading; live GPU/service gates pending.
  Watchdog deadline1789392581.891499; no duplicate launch. Earlier no-live snapshot
  superseded. Same approved$5.40/90min extension; see HANDOFF§12.

### Latest: 2026-09-14 lab024 approved matrix executed

- 27 comparisons measured across22+5 passes. Reports: [initial22](../reports/lab024-characterization/README.md), [continuation5](../reports/lab024-continuation/README.md).
- Owned51011956 destroyed+absence verified, watchdog stopped. Additional$1.9208;
  cumulative estimate≈$116.84. No live owned instance or scheduled paid continuation.
- Explicit clocks/IDs/context bytes measured. Replica parent trace propagation
  gap identified and locally fixed; live revalidation remains a gap. Sticky
  replica load imbalance prevents a pure TP-topology conclusion.
- Essential2,409-file transfer hash/extraction verified; full bulk intentionally
  skipped. Old s22b partial provenance and3 missing GPU-normalized rates unchanged.
- See HANDOFF§13 and request024; preceding pending/live snapshots are historical.


### Latest follow-up: lab025 prepared (2026-09-15)

- User continuation: exact stage/router gates and task-rr reference implemented;
  54 focused tests and exact-image preflight passed. No new performance claim.
- New bounded TP2/sticky/task-rr validation planned within remaining approved
  $3.4792, capped$3.40/60min per new instance. No budget reset. Beadseocho-odhn.
- See request025 and HANDOFF§14. Owned-instance state must be read from lifecycle
  once launch begins; preparation checkpoint has no new instance.


### Latest: lab025 + chartbook complete (2026-09-15)

- Three K64 comparisons + three graph/trace gates exit0. TP2/sticky/task-rr
  426.09/322.63/378.24 output tok/s/GPU. All6332 comparison requests linked to
  complete server stages; replica router joins also complete.
- [Lab025 report](../reports/lab025-replicas/README.md) and
  [Runtime / Tool / Agent charts](../reports/characterization-charts/index.html)
  cover30 total comparison conditions. User chart request implemented without
  adding experimental factors or paid calls.
- lab-s24b instance51057078 destroyed+verified, watchdog stopped, no owned runs.
  Essential2853files remote/local SHA verified; bulk intentionally skipped.
  Additional cost≈$1.5726, extension total≈$3.4933/$5.40, cumulative≈$118.42.
- Request025/HANDOFF§15 supersede prepared/live snapshots. No next run scheduled.


## 2026-09-15 lab026 authorized preparation

Request026 / seocho-6niy: user authorized engine instrumentation, same-host
higher-K baseline/MTP and actual long-context/prefix-reuse pressure, sequentially
without further confirmation. Local implementation passed73 focused tests and
basic CI. Exact-image preflight pending; no new instance or measured result yet.
Operator bound$12.60 /12600s at<=3.60/h; prior spending remains separate.


### 2026-09-16 lab026 checkpoint

Lab026 stage1 complete; baselineK8/K64 measured; K128 client thread-capacity failure, not GPU saturation. Instance destroyed; recovery with child-scoped native-thread=1 preparing. Stages2/3 still pending.


## 2026-09-17 recovery checkpoint: s25b and bounded completion

s25b source b5ce2327: gate and baseline K8/K64/K128 passed exact trace and
engine admission. Rates121.459/436.707/485.151 output tok/s/GPU,
task/s0.54936/1.88381/2.09153. Native-thread=1 reduced K128 agent threads to513;
cgroup pids peak1434, limit7680, pids.max events0. K256 failed40/256 worker
starts with OSError before a valid window;216 tasks completed during draining.
Original coordinator catch omitted errno, so exact live errno is unknown.
K256 pids peak1838 and no pids/max/OOM events: this is not GPU saturation evidence.

Bounded local startup reproduction (96children, RLIMIT_NOFILE256): concurrent
creation yielded61EMFILE failures; serialized creation started96/96. Fix scopes
serialization to Popen startup; actual tasks remain concurrent. Coordinator
errors now record numeric errno and RLIMIT_NOFILE without private error text.
This is a verified possible mechanism, not proof of the missing live errno.

Instance51161376 destroyed/absent; watchdog parent_teardown_verified_absent.
Essential5219files/7280675bytes SHA256
6d07cc9904aefd0c46dc3e866e25697fe3cf3f686e026351bda931f78093f078.
Bulk remains intentionally partial. Runtime2547.817764s at3.229907407/h cost
2.285893186; s25+s25b total4.992317572. Historical cumulative approx123.4123.

Registered next delta: `--runtime-completion`, lab-s25c, same pinned stack and
worker thread policy plus serialized startup. One same-host paired sweep of
baseline/MTP at K8/64/128/256 (8cells +2gates), then all10actual context cells.
The second sweep is removed to prioritize completing the three authorized
stages within the original12.60 bound; no repeat variance or confidence interval
claim. Prior lowK controls repeat because a new host/startup policy needs a
matched control. No previously completed stage1 repeat. Hard lifetime7500s,
rate<=3.60/h =>max7.50; cumulative upper estimate12.492318. Remote6900s,
session6100s/reserve300s. No replacement while existing ownership is unresolved.
