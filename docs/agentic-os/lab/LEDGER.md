# Paid-run ledger (Vast.ai)

| date (KST) | run | instance | offer $/h | billed | approx USD | outcome |
|---|---|---|---|---|---|---|
| 2026-09-02 22:30 → 09-03 07:18 | V13 e2e | 49648917 | 1.62 | 8.8 h | 14.3 | graph load ate budget; orphaned after session exit; manual destroy |
| 2026-09-03 07:44 | V14 gate | 49693005 | 2.17 | 49 min | 1.8 | NCCL hang on PCIe host |
| 2026-09-03 09:03 | V15 gate | 49697610 | 1.59 | 24 min | 0.6 | stall-detector false positive |
| 2026-09-03 09:30 | V16 gate | 49699245 | 1.59 | 46 min | 1.2 | GTSQA missing `runtime/`; GRBench 1/4 validated |
| 2026-09-03 21:12 | V17 gate | 49747203 | 1.87 | 33 min | 1.0 | infra green, 8/8 cells ran, gate 3/8 validated |

| 2026-09-05 16:34 | lab-s1 hold (host 3497) | 49939537 | 1.65 | 36 min | 1.0 | post-capture NCCL spin; orderly teardown verified |

| 2026-09-05 17:12 → 20:50 | lab-s2 hold (host 445596) | 49941854 | 1.87 | 218 min | 6.8 | appliance ready in 7 min and held; **no pass ran** — the orchestrating Claude session died at 17:21 and nothing on the appliance had a queue to execute; SSH dropped at 3.5 h (exit 255), runner collected + destroyed, verified absent |

| 2026-09-08 22:24 → 09-09 02:03 | lab-s3 queue (host 224078) | 50270476 | 1.47 | 220 min | 5.4 | 4/4 passes ran unattended; teardown verified |
| 2026-09-09 07:42 → 10:59 | lab-s4 queue, 008 session A (host 639771, 2× A100-80GB NVLink) | 50320425 | 1.90 | 198 min | 6.27 | 8/8 passes unattended; wrapper exit 3 only (seocho-twoh); teardown verified |
| 2026-09-09 23:39 → 23:56 | lab-s5 try 1 (host 60897, 2× A100-80GB, CUDA 12.0 driver 525) | 50397116 | 1.90 | 17 min | 0.54 | vLLM failed at start: cudaGetDeviceCount error 803 (driver too old for cu130 image); destroyed + verified |
| 2026-09-10 00:10 → 02:39 | lab-s5 try 2 (host 639771, 2× A100-SXM4-80GB NVLink NV12, P2P on) | 50399651 | 1.96 | 149 min | 4.88 | mono k1/4/8 ok; 5 replica passes exit 3 (router dropped Content-Type on /tokenize); 2 LMCache passes failed at vLLM start (hybrid KV specs); destroyed + verified |
| 2026-09-10 02:46 → 05:22 | lab-s6 session B2 (host 639771, 2× A100-80GB NVLink) | 50414915 | 1.96 | 155 min | 5.06 | 5 replica passes (sticky k1/4/8, rr k4/8) all lanes complete; wrapper exit 3 on gpu_sampling_gaps only; destroyed + verified |
| 2026-09-10 07:42 → 10:07 | lab-s7 lab 009 session C (host 67231, 2× A100 PCIe 80GB, UK) | 50436838 | 2.06 | 145 min | 4.99 | 6 passes (easy/hard × K1/K4, easy/hard K1 spec) all exit 0; lifecycle state=complete (wrapper fix); destroyed + verified |
| 2026-09-10 21:42 → 22:17 | lab-s8 lab 010 OTel live validation (host 618816, 2× RTX PRO 6000 96GB, Oregon) | 50491173 | 2.84 | 36 min | 1.69 | 1 pass medium-k4-otel exit 0; vLLM OTLP + in-instance receiver; state=complete; destroyed + verified |
| 2026-09-10 22:18 → 23:12 | lab-s9 lab 010/012 one-pass validation (host 618816, 2× RTX PRO 6000 96GB, Oregon) | 50493699 | 2.88 | 54 min | 2.6 | appliance_fatal 3 at dozer_indexes: index step ran at service_start before DozerDB accepted Bolt (connection refused); no pass ran; destroyed + verified. Fix: block moved after DozerDB ready (bundle v40) |
| 2026-09-11 07:50 → 10:10 | lab-s11 lab 013 wider cohort (host 420778, 2× RTX PRO 6000 96GB, Taiwan) | 50532353 | 2.66 | 140 min | 6.2 | cache-serving + all-k4 (71 min) + all-k8 (43 min) all exit 0; dozer_indexes after dozer_ready ok; state=complete; destroyed + verified |
| 2026-09-11 23:34 → 23:59 | lab-s12 try 1 (host 258806, 2× RTX PRO 6000 Max-Q, Czechia) | 50601901 | 2.27 | 25 min | 0.95 | direct SSH endpoint never became ready (900 s startup timeout); no staging; destroyed + verified. Host excluded for try 2 |
| 2026-09-12 00:00 → 00:25 | lab-s12 try 2 (host 635607, 2× RTX PRO 6000 Max-Q, Romania) | 50604096 | 2.72 | 25 min | 1.1 | aborted by SIGINT before staging: new rule "NVLink-connected GPUs only" (PCIe pair); destroyed + verified |
| 2026-09-12 00:47 → 03:37 | lab-s12 try 3, lab 014 sweep (host 458442, 2× A100 SXM4 NVLink NV12, Japan) | 50607956 | 2.30 | 170 min | 6.5 | cache-serving + all-k16 (36 min) + all-k32 (28) + medium-k4-rowcap100 (30) + all-k16-noasync (37), all exit 0; P2P enabled (NVLink); state=complete; destroyed + verified |
| 2026-09-12 03:41 → 05:50 | lab-s13 lab 015 schema arms (host 458442, 2× A100 SXM4 NVLink, Japan) | 50622600 | 2.30 | 129 min | 4.9 | cache-serving + S1 (38 min) + S4 (30) + C- (32) — all lanes complete; passes S1/C- report exit 1 only because the skipped last lane (gtsqa-multi) set the matrix exit status (fixed); lifecycle state=failed (wrapper exit 4) but artifacts copied + verified; destroyed + verified |
| 2026-09-12 08:20 → 09:35 | lab-s15 session-I (host 598051, 2× H100 PCIe + NVLink bridge, UAE) | 50661840 | 4.27 | 75 min | 5.3 | queue ran (cache-serving 17 s, arms-o1-k8 619 s, graphcot-b0-k8 225 s, graphcot-o1 ?) but the artifact scp timed out after 1800 s and the runner destroyed the instance: **artifacts lost**; destroyed + verified |
| 2026-09-12 09:44 → 11:02 | lab-s16 session-J (host 598051, 2× H100 PCIe + NVLink, UAE) | 50672533 | 5.07 | 78 min | 6.6 | cache-serving + graphcot-o1 (277 s) + graphcot-b0 (215 s) exit 0; essential archive (2 020 files) copied + verified in minutes, bulk copy still failed on this slow uplink after 3600 s + retry (≈ 50 min of paid idle) → state artifacts_partial; destroyed + verified |
| 2026-09-12 14:17 → 14:47 | lab-s17 lab 021 PROFILE pass (host 458442, 2× A100 SXM4 NVLink, Japan) | 50699337 | 3.91 | 30 min | 2.0 | cache-serving + profile-s1-k8 (472 s, 90 cases) + profile-b0-medium-k8 (146 s, 30 cases), 317 plan replays, 0 errors; essential-only collection (1 420 files verified, bulk skipped by flag); destroyed + verified |
| 2026-09-13 08:35 → 09:05 | lab-s18 lab 021 step 2 zero-row hint A/B (host 458442, 2× A100 SXM4 NVLink, Japan) | 50820190 | 3.30 | 30 min | 1.7 | cache-serving + hint-off-s1-k8 (461 s) + hint-on-s1-k8 (308 s), 90 cases each, exit 0; essential-only collection (2 020 files verified); destroyed + verified |
| 2026-09-13 09:08 → 10:29 | lab-s19 lab 020 session A serving arms (host 458442, 2× A100 SXM4 NVLink, Japan) | 50823574 | 3.14 | 81 min | 4.2 | cache-serving + a0–a8 (8 arms ran 310–587 s each; a7 suffix and a9 batch-invariant failed to start), 90 cases each; essential-only collection (7 934 files verified); destroyed + verified |
| 2026-09-13 10:30 → 11:10 | lab-s20 lab 022 three models (host 458442, 2× A100 SXM4 NVLink, Japan) | 50831945 | 3.14 | 40 min | 2.1 | prefetch + 27B (474 s) + switch/cache/9B (115 s) + switch/cache/35B-A3B (205 s); draft arm failed at vLLM start (vLLM rewrites qwen3_5 drafts into MTP); essential-only collection (3 011 files verified); destroyed + verified |
| 2026-09-13 11:13 → 12:29 | lab-s21 labs 019 + 020 grammar (host 125728, 2× A100 SXM4 NVLink, Czechia) | 50836154 | 2.375 | 76 min | 3.0 | router V0/V0A (90), held-out S1/V0 (50), grammar none/xgr/onto ×27B, 9B none/onto, suffix + batch-invariant retries (both failed to start); essential-only collection (8 064 files verified); destroyed + verified |

Total ≈ **$102.8**. Live instances at last check (2026-09-13 12:35 KST): ours none (lab-s21 destroyed + verified); teammate instance gare2e-full-ondemand-a100-20260910-161803 still running on the shared key.

- 2026-09-13, lab 023: local harness implementation/contract tests only, **$0**.
  No new paid calls or Vast operations; cumulative reported spend unchanged.

- 2026-09-13 / lab024 preparation: \$0 paid usage so far. Owner approved ~\$13 total for the five pending comparisons; proposed bound \$3.60/h ×13000s = \$13. No owned instance created at this checkpoint.

### Lab024 execution (supersedes preparation checkpoint)

| date KST | run | instance | disk-inclusive $/h | elapsed | estimate USD | outcome |
|---|---|---|---:|---|---:|---|
| 2026-09-13 | lab-s22 | none | — | admission only | 0 | exact offer unavailable before create |
| 2026-09-13 17:56–21:32 | lab-s22b | 50866184 | 3.3562963 | 13002.9s | 12.1227 | 24 passes exit0; TP pair stopped at reserve; archive locally reconciled, remote digest checkpoint absent; watchdog destroyed+verified |

Current total ≈**$114.92** (prior$102.8 +$12.1227), hourly estimate rather than
final invoice. No owned live instance. Protected colleague unchanged. See
HANDOFF§12 for recovery and remaining work. No new paid resources launched.

- 2026-09-14 lab-s23: **$0**, failed admission before create (no compatible A100
  SXM pair for pinned CUDA>=13 image). Approved extra$5.40 remains unspent.
  No owned live instance; cumulative estimate stays≈$114.92.

### Latest: 2026-09-14 continuation settled (hourly estimate)

| run | owned instance | disk-inclusive $/h | billed-span estimate | estimate USD | result |
| --- | --- | ---: | ---: | ---: | --- |
| lab-s23b | 51011956 | 2.09037037 | 3307.95s | 1.9208 | gate+5 comparisons exit0; essential copy verified; parent destroyed+absence verified; watchdog stopped |

Additional extension usage≈$1.9208 within approved$5.40/90min. Lab024 combined
original and continuation≈$14.0435. Overall cumulative≈**$116.84** using the
previous approximate$102.8 base. These are hourly estimates, not final invoices.
No owned live instance. Lab-s23 admission-only failure remains$0. No new launch
scheduled; previous unspent/live ledger snapshots are superseded. Bulk archive
coverage is partial by explicit essential-only mode; see HANDOFF§13.


### Latest: 2026-09-15 lab025 settled (hourly estimate)

| run | owned instance | disk-inclusive $/h | elapsed | estimate USD | outcome |
| --- | --- | ---: | ---: | ---: | --- |
| lab-s24 | none | — | admission only | 0 | compat-only offer unavailable before create |
| lab-s24b | 51057078 | 2.38592593 | 2372.75s | 1.5726 | three graph/trace gates + three comparisons exit0; essential verified; destroyed/absent + watchdog stopped |

Prior approved extension$5.40: previous$1.920787 + current$1.572559 ≈**$3.4933**,
remaining≈$1.9067. Prior spend is retained; the3600s watchdog was per instance,
stricter than the previous5400s bound. No monetary reset or new scope inferred.
Overall cumulative≈**$118.42** (approximate$102.8 base +$12.122666 +$1.920787
+$1.572559), not a final invoice. Charts are free postprocessing. No owned live
instance, no replacement scheduled; colleagues untouched. Essential-only bulk
limitation preserved in original lifecycle. See HANDOFF§15 and request025 Result.


### 2026-09-16 lab026 checkpoint

Lab-s25 instance51135854 destroyed/absent:2902.9403s at3.3562963/h =2.706424USD. New scope used2.706424/12.60; historical cumulativeapprox121.12USD. Recoverymax9.80 retains totalbound12.506424. No currently owned live instance.


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

2026-09-17: s25c51261456:4342.928385s at1.902944444/h =2.295653179USD; destroyed/absent, essential checksum verified. Stage total7.287970750USD; next max5.00 =>12.287971 cumulative cap. Historical cumulative approx125.708USD. Hourly estimates, not invoices.
