# AgenticOS graph-agent e2e — handoff package

What another engineer needs to run the same e2e (co-located Vast appliance:
vLLM + DozerDB + embedded Oxigraph, 8 one-case gate cells, telemetry
decomposition, serving sweeps). Everything in this repo is code and procedure;
the data and credentials below travel separately.

## 1. Access

| item | how |
|---|---|
| this repo (`tteon/gare2e`, private) | GitHub invite; clone, then `uv sync --extra local --extra ci` |
| Vast.ai account | an API key (`vastai set api-key …`) and an SSH public key registered in the Vast console — the runner uses direct SSH/SCP, never `vastai copy`. A shared key means shared billing and shared visibility of instances; the runner's §0 ownership gate assumes the account has no other live instances, so prefer a per-person key or a Vast team sub-account |
| Hugging Face | anonymous download of `Qwen/Qwen3.6-27B` works; a token (`HF_TOKEN`) only speeds it up |
| local tools | `uv`, `docker` (only for the local preflight, which boots the exact vLLM image once), `vastai` CLI, a systemd user session with lingering (`loginctl enable-linger`) so paid runs outlive the shell |

## 2. Data (mostly online; only two derived files)

The five frozen gate-10 files are **in this repo** (`examples/agentic-os/frozen/gate-10/`).
The two benchmark datasets are public downloads. The remaining two inputs are
derived locally by scripts in this repo. Digests are the first 16 hex of sha256
of the copies used for V13–V17 / lab-s2.

| path under `.seocho/` | size | sha256[:16] | provenance / how to regenerate |
|---|---|---|---|
| `datasets/graph-cot/graph-environments/maple/Physics/graph.json` | 4.5 GB | `e188b3b88af63c10` | GRBench MAPLE/Physics official graph (HF `PeterJinGo/GRBench`, Graph-CoT repo) |
| `datasets/graph-cot/data/processed_data/maple/Physics/data.json` | 36 KB | `f6ccb8217d533f7c` | GRBench MAPLE/Physics questions (same source) |
| `datasets/gtsqa/gtsqa-with-graphs/test-00000-of-00001.parquet` | 794 MB | `07e50a155267f46d` | GTSQA "with graphs" test split (HF) |
| `benchmarks/agentic-os/colocated-appliance/bootstrap/dozerdb-runtime.tar.gz` | 327 MB | `b15a6e6c9dacac1a` | curated rootfs of `graphstack/dozerdb:5.26.3.0` (`/var/lib/neo4j`, `/opt/java/openjdk`, `/startup`); regenerate with `scripts/benchmarks/export_dozerdb_appliance_rootfs.py` |
| `…/bootstrap/grbench-import-v1/grbench-nodes.csv.gz` | 461 MB | `cc3f0585bff06c7d` | regenerate from graph.json: `scripts/benchmarks/export_grbench_import_csv.py --workspace-id grbench-maple-physics` |
| `…/bootstrap/grbench-import-v1/grbench-rels.csv.gz` | 455 MB | `10932fe1c86517fd` | same exporter run |
| `…/bootstrap/grbench-import-v1/grbench-import-manifest.json` | 4 KB | `8a7ca303e90f677a` | same exporter run (carries the workspace id the runners query) |
| `examples/agentic-os/frozen/gate-10/grbench-discovery-serving.json` (in repo) | 140 KB | `4d72bf1e567ee269` | frozen discovery cache, serving tokens validated by vLLM 0.27.1 for the pinned revision |
| `examples/agentic-os/frozen/gate-10/grbench-single-tool-schedule.json` (in repo) | 12 KB | `501d9061627fbe5b` | frozen paired schedule |
| `examples/agentic-os/frozen/gate-10/gtsqa-discovery-serving.json` (in repo) | 876 KB | `b3aca1e543f321ca` | frozen discovery cache |
| `examples/agentic-os/frozen/gate-10/gtsqa-single-tool-schedule.json` (in repo) | 24 KB | `6fad984bfe7aff51` | frozen paired schedule |
| `examples/agentic-os/frozen/gate-10/gtsqa-multi-tool-schedule.json` (in repo) | 8 KB | `c065d4a19e0e439c` | frozen paired schedule |

So the minimal handoff is: repo access + a Vast API key. Download the two
public datasets, run the two derivation scripts (DozerDB rootfs export needs
Docker; the CSV export needs ~10 GB RAM and a few minutes), and the frozen
files come with the clone.

## 3. Fixed configuration (do not change silently)

- Model `Qwen/Qwen3.6-27B` at revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, served by `vllm/vllm-openai:v0.27.1`, TP=2 on 2× A100 (80 GB total), `--max-model-len 32768`, native tool calling (`--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3`), prefix caching on.
- GRBench workspace id `grbench-maple-physics` end to end.
- Admission floors: 2 GPUs, ≥ 81,920 MiB GPU RAM, ≥ 16 effective CPUs, ≥ 64 GiB host RAM, reliability ≥ 0.98, ≤ $2.25/h; excluded hosts 458442 and 3497 (NCCL hangs).

## 4. Runbook

Follow `.claude/skills/vast-agentic-e2e/SKILL.md` in order: §0 ownership gate → §1 inputs and bundle (`prepare_agentic_os_appliance_bundle.py`, `preflight_agentic_os_appliance.py`) → §2 pin the model revision → §3 read-only offer check → §4 launch as a `systemd-run --user` unit → §5 wait on the receipt → §6 teardown truth first, then evidence. Gate-only first (`SEOCHO_E2E_PHASE=gate`, ~25–35 min, ≈ $1–2); the matrix only after the gate verdict is understood. For iteration use §4b (persistent instance with an uploaded pass queue) and §4c (declarative serving sweep, `examples/agentic-os/serving-sweep.yaml`).

Analysis after any run: `join_agentic_os_gpu_samples.py` → `analyze_agentic_os_attempt_windows.py` (tables + per-cell timelines), `catalog_agentic_os_results.py` for funnel statistics.

## 5. What to expect

- Cold appliance to services-ready: 7–20 min (bulk import ≈ 2.5 min, vLLM weights + compile 4–17 min depending on host cache).
- Eight gate cells: ≈ 15 min. Raw B0 typically triggers the graph tool 8/8 and passes validation in ~3/8; that is the baseline the treatments are measured against, not a bug.
- Every failure class seen so far and its fix is in `.claude/skills/vast-agentic-e2e/references/incidents.md`; read it before changing bootstrap, image, transport, or teardown.
- Cost discipline: create only right before a validated run; the runner destroys and verifies absence itself; a session whose controller may die must upload a pass queue instead of driving the appliance interactively.

## 6. Handoff checklist

- [ ] repo invite accepted, `uv sync` done, lane tests green (`pytest tests/seocho/test_agentic_os_*.py tests/seocho/test_vast_*.py …`)
- [ ] data package copied to the paths above; digests verified
- [ ] Vast API key + SSH key set; `vastai show instances --raw` returns `[]`
- [ ] local preflight passed on the built bundle
- [ ] first gate-only run completed and torn down; `one-case-gate.json` read


## 9. State of the lab as of 2026-09-13 13:40 KST (labs 013–022) — read this to continue

**Nothing is running.** Paid spend to date ≈ $102.8 (`docs/agentic-os/lab/LEDGER.md`). Every session's
content-free package is in `s3://seocho-agentic-os-traces/seocho-agentic-os/traces/<run>/` (index: 11 runs;
copies of `index.json` and each `manifest.json` under `traces/`; session tables under `traces/<run>/session-summary/`).

### Where things live
| what | where |
|---|---|
| lab board, ledger, decisions, request files with `## Result` sections | `docs/agentic-os/lab/` (copy of the private `.agents/lab/` zone; the request numbers 001–022 are the units of work) |
| report pages | `docs/agentic-os/reports/` (+ artifact URLs in its README) |
| queue files per session | `.seocho/benchmarks/agentic-os/strata-v2/session-{E..O}/lab-queue.tsv` (force-added; `.seocho` is otherwise ignored) |
| launchers / watchers / scrape / rehearsal | `scripts/benchmarks/lab/` |
| appliance bundles | built locally with `prepare_agentic_os_appliance_bundle.py` (v46 … v50 used today; sha in each launcher's run receipt) |
| lane code (this branch) | `src/seocho/eval/{request_analysis,request_router,cypher_grammar,grbench_graphcot,grbench_runtime,discovery_cache}.py`, `scripts/benchmarks/run_{direct_react_text2cypher,grbench_graphcot_react,grbench_router,grbench_grammar_cypher}.py`, `run_agentic_os_schedule.py`, `run_agentic_os_colocated_e2e.sh`, `summarize_agentic_os_{stratum_funnel,turns,query_plans,queue_session,zero_row_ab,serving_arms,scheduler_occupancy,router,grammar}.py`, `pack_agentic_os_traces.py` |

### Rules in force (from `docs/agentic-os/lab/DECISIONS.md`)
Paid instances: ≥ 2 GPUs joined by NVLink (`--require-nvlink`, `bw_nvlink>0`), A100/H100/H200 pairs, ≤ $5.5/h, host 458442 admitted. Single-agent lanes only. GTSQA parked; Text2Cypher first. Receipts and reports are content-free (no prompts, answers, keys); `*.private.*` never leaves the run dir. The teammate instance `gare2e-full-ondemand-a100-20260910-161803` on the shared Vast key is never touched.

### Findings in one screen (details in the request files' `## Result`)
- 013/014: decode owns 0.82–0.98 of case wall at every K and difficulty; host ≤ 0.09; DB ≤ 0.1; K raises tok/s 3× but per-request wall too; funnel invariant to serving shape.
- 015/016: SEOCHO schema slice S1 lifts GRBench answered 7/16/3 → 20/29/7; size-matched noise collapses to 0/2/1; relation semantics (O1) add nothing for Cypher, hurt SPARQL.
- 017/018: Graph-CoT hop actions answer 47/90 with zero repairs but exhaust cumulative budgets; anchors are 100 % resolvable, no reuse across questions; hard = low-degree anchors + argmax.
- 021: every query is index-anchored (0 scans, 0 page-cache misses, 11–19 ms server); 52/58 zero-row S1 queries are pattern errors after a matched anchor → zero-row diagnostic hint (≈ 10 ms) moves hard 5 → 9, aggregate flat.
- 020: MTP (`qwen3_5_mtp`, k=2) decode-only TPOT 25.3 → 15.7 ms at funnel parity; ngram corrupts Cypher (18/90); prefix caching off +14 % decode TPOT; chunked prefill / block size no per-token effect; grammar-constrained Cypher (xgrammar) removes scope/label rejections but costs ×1.9–3.7 per token on this stack; the ontology-closed grammar (C) beats the plain one (B).
- 022: 27B 56 vs 9B 20 vs 35B-A3B 20 answered under tool calling; the small models fail the protocol (171 dropped scope maps), and the query-first protocol alone lifts the 9B to 45/90 at 3.6 s/case; draft_model speculation is infeasible on vLLM 0.27 for Qwen3.6 (drafts are rewritten to MTP).
- 019: request-analysis router V0 (5 rules) = 56/90 in-sample with −80 % rejections, −22 % tokens, −21 % wall; held-out 31/50 vs S1 34/50 (argmax → O1 + row cap 50 is right; count turn cap and hop lookups are the misroutes).
- Throughput re-read (13:30): server queue time is 0 in every arm; the harness itself starves the batch — 1.93 s dead time per case (coordinator gap 1.27 s + runner start 0.66 s) and a static 8-shard tail leaves < 8 runners active for 32–56 % of a pass. Aggregate tok/s across arms is therefore not comparable yet.

### Next steps (proposed, awaiting the owner's go on the paid part)
Free: dynamic work queue + persistent runner in `run_agentic_os_schedule.py`, steady-state measurement window with backlog, per-request stage timings (queue/TTFT/prefill/decode from vLLM spans) and `vllm:iteration_tokens_total` in receipts, task/s vs output-tok/s split, synthetic replay dataset (prompt/completion length pairs from receipts) for `vllm bench serve`, tool-stub executor. Paid (≈ $13): K sweep 8/16/32/64 × {baseline, MTP} under backlog; serving-only replay vs tool-stub vs real DB; block/chunk at saturation; grammar constrained-ratio / CPU-core sweep; TP=2 vs 2×TP=1 at saturation. Also open: router policy V1 held-out re-run (≈ $1), landing this lane to seocho `main` (bead seocho-56wk), suffix decoding and `VLLM_BATCH_INVARIANT` start-up failures (parked).

### How to resume with Claude Code
Say "이어서" / `/lab-resume`: the skill restores the handoff memo, board and beads, checks our Vast instances by label prefix only, and continues the recorded order. Commit lane work only through a temporary worktree of `agentic-os-e2e` (the seocho working tree is shared live with another agent); push to `gare2e`, never to public `origin` without the owner's say.

## 10. Free harness continuation — 2026-09-13 (lab 023)

The four core harness items from §9 are implemented with local contract tests:
dynamic FIFO, persistent GRBench single-agent runner processes, a warmup/backlog
window, and request-stage / aligned task-versus-token measurement. See
[lab 023](lab/requests/023-throughput-harness.md) for flags, exact measurement
semantics, validation and limitations. Opt in with `SEOCHO_SCHEDULE_MODE=dynamic`
in a main pass; the default launcher remains static. No paid calls or Vast
operations occurred in this continuation; the previously reported spend remains
≈ $102.8. GPU performance and live parity are still unmeasured for this code.

Basic CI's tests passed (1112, 3 skipped); its final identity check failed on five
pre-existing Vast SSH fixture addresses. Focused worker/measurement/appliance tests: 91 passed.
Synthetic replay and tool-stub preparation remain free follow-up work. The ~$13
paid plan still awaits an explicit go. Continue committing only from a temporary
worktree and pushing only to private `gare2e/agentic-os-e2e`.

## 11. Approved continuation and observability (2026-09-13)

Owner said “네 모두 진행하시죠”: the five ~\$13 comparisons in §9–10 and free
replay preparation are now approved. Lab [024](lab/requests/024-approved-throughput-and-observability.md)
tracks this work (bead seocho-7yaz). The later memory/cache discussion adds the
[observability factor contract](OBSERVABILITY_FACTORS.md), without adding paid
DB/KV allocation or offloading sweeps. Primary sequence remains supply/K then MTP.

Preparation includes duration-bounded FIFO windows, private fixed-request capture,
three replay substrates, grammar query ratio/CPU affinity, and a full appliance
pass queue (`run_agentic_os_throughput_session.py --print-plan`). Existing plus
new focused tests: 100 passed. Basic CI1112 passed3 skipped; final existing SSH
fixture identity check remains failing. Exact-image incremental preflight passed;
unchanged Dozer/image combined live evidence is reused by hash. No paid instance
created yet at this checkpoint. Keep temporary-worktree commits/private gare2e
branch and protected colleague-instance rules from §9. §9's separate router V1
and public SEOCHO landing are not included in this authorization.

## 12. Lab024 live results and reconciliation — 2026-09-13 evening KST

**Current state: no owned instance running.** Latest result supersedes the
pre-launch snapshots in §10–11. Owner approval was used for lab-s22b; 24 passes
exited0 (22 comparisons + gate/capture). Budget reserve stopped before TP2-control
and replicas2. The additional grammar K8 amendment and explicit new task/tool
instrumentation were committed locally but never uploaded after the interrupted
amendment; do not claim them measured.

[Three-view interactive report](reports/lab024-characterization/index.html) and
[findings/provenance](reports/lab024-characterization/README.md) link actual tasks,
LLM stages and graph tool events. 7,834 comparison tasks, 33,633 requests (33,616
unique server matches; all7,156 contained-cohort requests matched), 26,014 tools.
Missing telemetry remains unknown. Primary output tok/s/GPU is 130.3→441.5 for
baseline K8→64, versus163.3→289.2 for MTP. No baseline saturation established.
KV max44.3%, zero preemptions; no long-context/offload evidence. Three block/chunk
passes lack window GPU samples, so their primary per-GPU rate stays null.

Owned50866184 / lab-s22b ran 17:56→21:32 KST, disk-inclusive $3.3562963/h;
watchdog destroyed and verified absence at unix1789302775.0586078. Estimated
hourly cost **$12.1227**, cumulative ≈**$114.92**. Protected colleague unchanged.
The controller's collection timed out after a missing-glob archive bug included
the whole artifact tree. The complete77.65MB archive was recovered and all51,590
unique regular files matched local extraction (zero missing/mismatch). Independent
remote digest checkpoint absent: lifecycle `artifacts_partial_reconciled`,
`artifact_copy_verified=false`. Preserve original archive and reconciliation
receipt. Collector now selects exact regular-file matches and checkpoints the
verified digest before extraction; this fix is local-tested, not remotely rerun.

Source deployed: bdfb4877 / source-v53 SHA256
09c3a7a7c126d1724c45c060894d6f9aec8358d72e800080b0e8fb10c718b9cf.
Local run: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s22b`;
derived directory `characterization-v1`; private copies stay local. Repository
report and compressed content-free tables are under reports/lab024-characterization;
compact CSV and manifest under `traces/lab-s22b/session-summary/`.

Continuation remains bead seocho-7yaz. Remaining live gates: TP2-control versus
2×TP1 K64 and grammar K8 ratio0/.5/1. Proposed separate continuation ceiling:
90min ×$3.60/h = $5.40 including startup/collection; no new paid launch has been
made. This exceeds the nearly consumed original ~$13 allocation, so obtain the
owner's budget extension before creating another instance. No DB/KV allocation
or offloading matrix is included. Commit through the temporary worktree; push
private gare2e/agentic-os-e2e only. Do not touch colleague instances.

### §12 update: continuation budget approved

Owner approved the proposed additional max$5.40/90min (“네 모두 하시죠”). Lab-s23
will run only gate105, TP2/2×TP1 at K64, grammar K8 CPU16 ratios0/.5/1; same A100
SXM80GB class with NVLink, same model/data/image. `launch_lab_s22.py --continuation`
enforces5400s lifetime and <=$3.60/h. Earlier budget-pending text is superseded;
no further approval within this extension. Results and lifecycle pending launch.

### §12 update: 2026-09-14 admission blocked, no additional spend

Continuation source **2f99840c**, v54 SHA256
`61e0a357c4691bea0047eae0a00c9180a6260d58c2f24519830596af912fa447`.
Exact-image/source/unchanged-runtime/entrypoint preflight all passed.49 focused
checks and Ruff passed. Basic CI1112 passed3skipped; same five pre-existing identity
fixture errors remain. Source and budget extension are pushed to private branch.

Detached lab-s23 controller attempted admission and exited before create:
`selected offer is absent or ambiguous`. No instance ID, no new billed usage.
A fresh provider query returned no A100 SXM80GB pair satisfying CUDA>=13.0,
NVLink, <=$3.60/h and the registered capacities. A separate read-only query found
A100 SXM4 offer36994173 / host109790, $2.0543/h, NVLink300,38 CPUs, but driver
570.172.08 / cuda_max_good12.8; it fails the pinned-image admission requirement.
No image/driver substitution was attempted. This is inventory/admission blockage,
not a model/runtime experiment failure. Other provider instances were not modified.

**Current state: no owned live instance; approved extension$5.40 remains unspent.**
Artifacts: local `.../runs/lab-s23/vast-colocated-lifecycle.json` records failed
admission without creation. No watcher is polling or scheduling a later launch.
Resume by querying fresh compatible A100 inventory; once admitted, use a NEW run
root (lab-s23b) and fresh detached unit with the existing continuation flags.
Approval persists; do not ask again for the same $5.40/90min scope. Existing lab-s23
failure receipt must remain intact. The five remaining comparisons are still unrun.

### §12 update: CUDA compatibility path validated locally (2026-09-14)

The previous CUDA>=13 provider filter was overly restrictive for this pinned
image. Official NVIDIA forward-compatibility documentation supports CUDA13
compatibility libraries on data-center R570 GPUs; the exact vLLM0.27.1 image
already contains `/usr/local/cuda-13.0/compat/libcuda.so.580.95.05`, admits R570 in
NVIDIA_REQUIRE_CUDA, and implements VLLM_ENABLE_CUDA_COMPATIBILITY. No image,
model or host driver replacement is needed. This corrects the earlier admission
interpretation; live compatibility is still a required gate, not assumed.

`--continuation --cuda-forward-compat` selects only A100 SXM / CUDA12.8 offers,
enables built-in compatibility with explicit library path, and requires the new
exact-image library preflight. After source extraction and before model/DB
startup, a live probe checks exactly two R570 A100s, CUDA13 BF16 matmul on both,
bidirectional peer access, actual compat libcuda mapping and driver API>=13000.
Failures stop bootstrap, preserve the receipt and trigger normal teardown.
The subsequent service readiness, live graph/trace gate and six-pass queue remain.

Source-v55 SHA25648d05226e7a8533a2fa562f6aecd811f5568a532349b1330f73932cc28871504.
Additional budget remains max$5.40/90min and currently unspent. Use new lab-s23b
root/unit; preserve original zero-cost admission failure. This is a recorded
runtime-environment difference from lab-s22b; compare TP controls within the new
host/session and label cross-session grammar comparisons accordingly.

Sources: https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html
and https://docs.vllm.ai/en/stable/getting_started/installation/gpu/ plus exact
local image inspection.71 focused tests passed; no local GPU was exercised.

### §12 live checkpoint: lab-s23b created

Owned instance51011956 created at unix1789387181.891499 on offer36994173 /
host109790, A100 SXM4x2 NVLink300,38 logical CPUs and515966MB host RAM. Source
42ed5bd1/v55, official CUDA forward compatibility enabled. Independent watchdog
deadline1789392581.891499 (5400s) is armed. Unitgare2e-lab-s23b-20260914 owns the
controller; local run `.../colocated-appliance/runs/lab-s23b`. Do not create a
replacement while this instance may exist. Original lab-s23 admission remains$0.

Provider currently loading the container; SSH and live CUDA probe are not yet
complete. Do not claim a successful runtime/throughput gate. Remaining queue is
fully pre-staged and does not need agent interaction once readiness/gate pass.
The same max$5.40 budget is active. This supersedes earlier no-instance/unspent
snapshots. Keep checking lifecycle/watchdog; collect+verify+destroy at completion.

### §13 final: lab024 continuation measured and owned instance destroyed (2026-09-14)

This is the latest state; the earlier pending/admission/live checkpoints are
historical. The approved matrix has now executed27 comparison conditions across
lab-s22b (22) and lab-s23b (5), plus their gates/capture. No owned instance or
active watchdog remains. No additional paid launch is scheduled.

[Continuation three-view report](reports/lab024-continuation/index.html),
[findings/conditions/gaps](reports/lab024-continuation/README.md), and
[compact CSV](../../traces/lab-s23b/session-summary/characterization.csv).
TP2 / sticky2xTP1 K64:439.59 /273.73 output tok/s/GPU and1.873 /1.240 task/s.
Grammar K8 CPU16 ratios0/.5/1:139.43 /91.73 /70.64 output tok/s/GPU.
Same-window primary rates observe both physical GPUs. Single ordered passes;
no confidence interval, balanced-topology ranking, saturation or accuracy claim.

1,156 comparison tasks,5,077 client requests,4,285 tools. All task clocks/IDs,
request step IDs/context bytes and tool/parent IDs are explicitly recorded.
Single-engine contained-cohort859 requests all match server stages. Replica1,918
client requests lack exact server parent links because the deployed router drops
W3C headers; report timings remain null. Header forwarding is fixed and locally
tested after teardown, not live revalidated. Future essential collection includes
router receipts. Replica metrics cover both full windows: mean running47.60 vs
13.33 and KV fractions0.648 vs0.169, exposing sticky routing imbalance. Fraction
sums are labeled as sums, not overall utilization. Do not infer pure TP effects.

Source42ed5bd1/v55, same model/image/data/Dozer; R570 A100 built-in CUDA13 compat
probe/readiness/gate105 passed. Five comparisons + gate all exit0. Added observer
source SHA256db21bba777c78249ea2eeaad438d71b3c1e2abc404bafcf2411a9151478aca3a
was staged read-only during replicas2; complete window coverage verified.

Essential transfer3,974,580bytes/2,409files verified remote=local SHA256
`dd5aa025f879acddd79f78074c6f11f37fc2fd39e6facb195e9692d35219bac7`, extraction
complete. The successful collector deletes the transfer archive and retains
extracted originals/checkpoint. Bulk was intentionally skipped by essential-only;
raw lifecycle stays failed/_PartialArtifacts and artifact_copy_verified=false.
This is a verified essential subset, not a fully archived bulk run. Do not rewrite
this receipt or copy private payloads into Git. Manifest records the distinction.

Instance51011956 created1789387181.891499, destroyed/absence verified by parent;
watchdog terminalparent_teardown_verified_absent, finished1789390489.8383522.
$2.09037037/h ×3307.95s ≈$1.9208 additional, within$5.40/90min. Lab024 total
≈$14.0435 across original+extension; cumulative prior estimate≈$116.84. Not an
invoice. Provider list independently confirmed no owned run; peers untouched.

Raw root `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s23b`, derived
`characterization-v1`; private originals stay local. Report+CSV.gz under
reports/lab024-continuation; compact table+manifest in traces/lab-s23b/session-summary.

Validation:36 focused appliance/report tests, Ruff, JS node check, TP2 and replica
headless rendering (11 tables/1 SVG each) passed. Basic CI1112 passed3skipped;
final identity check still rejects the same five pre-existing SSH fixture entries.
No full-green CI claim. Primary rates remain missing for the three older block/
chunk windows without GPU samples. Engine decode-sequence/grammar CPU/kernel-gap
instrumentation and long-context/offload/DB-KV allocation interventions remain
outside measured evidence. Any next run must specify the incremental hypothesis,
fix deployment and live gates; do not rerun automatically or reuse consumed budget.

Keep subsequent commits in a temporary worktree and push only private
`gare2e/agentic-os-e2e`. Update these state/evidence copies at each session end.


### §14 lab025 follow-up prepared (2026-09-15)

User requested continuation after lab024. Follow-up beadseocho-odhn and request025
implement exact W3C/router/stage admission, automatic full-window per-backend
sampling and bounded task-rr affinity. Default sticky unchanged. Same-host plan:
TP2 gate/control, sticky2TP1 gate/control, task-rr2TP1 gate/control. Gates require
a real graph call and complete request-stage joins; timed replica arms additionally
require both backends and complete metric windows. Missing evidence stops queue.

54 focused tests (including local HTTP) and Ruff pass. Historical original TP2
2278/2278 joins pass; old replica0/1918 fails. This is an audit of old evidence,
not a new performance result. Basic CI1112pass3skip; same five identity-fixture
violations remain. Exact-image/source/unchanged-Dozer/entrypoint/compat preflight
all pass; no local GPU exercised. Bundle lab025-20260915-v1 SHA256
8a4c7aad21acc988cac8311df1b730585072ec7d45091eafda079bb69d073884.

Prior approved extension$5.40 used$1.9208, remaining~$3.4792. User continuation is
bounded to <=$3.40/h and NEW3600s instance watchdog (stricter than former5400s),
maximum$3.40, combined extension~$5.3208. Do not reset spent money or widen scope.
New labelseocho-agentic-os-lab-s24/rootlab-s24/unitgare2e-lab-s24-20260915; no
instance created at this preparation checkpoint. Once created, lifecycle and
watchdog are authoritative; never create a replacement while one may exist.

### §15 lab025 complete + paper-inspired charts (2026-09-15)

Latest state: no owned live instance. Lab-s24 admission-only$0, then lab-s24b
instance51057078 on native driver595.71.05 A100 SXM4 80GBx2/NVLink300/host458442,
32 effective CPUs. Source1e0608de / bundle SHA8a4c7aad21acc988cac8311df1b730585072ec7d45091eafda079bb69d073884.
Same vLLM0.27.1/model revision/Dozer/data; no CUDA forward-compat override.
Three graph gates and three K64 comparisons all exit0; remote command exit0.

| condition | output tok/s/GPU | task/s | exact server/stage requests |
| --- | ---: | ---: | ---: |
| TP2 | 426.09 | 1.887 | 2251/2251 |
| sticky2TP1 | 322.63 | 1.385 | 2065/2065 |
| task-rr2TP1 | 378.24 | 1.720 | 2016/2016 |

Replica router joins are also complete. Per-backend89 samples cover each whole
window. Mean running41.46/18.72 (sticky) vs30.26/29.11 (task-rr); mean KV fractions
0.568/0.256 vs0.384/0.365. Task assignments232/212 vs219/219, no affinity violations.
Task-rr improved tok/s/GPU17.24% and task/s24.19% relative to sticky, still below
TP2 in this single same-host sequential run. Token demand/cache reuse/cohort mix
also differ; no pure kernel/accuracy-equivalence or universal layout conclusion.
Observed preemptions0; max contained input3062. Engine decode-sequence/kernel-gap,
grammar CPU, long-context/offload and DB/KV allocation effects remain unmeasured.

Raw root `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s24b`, derived
`characterization-v1`:1371 tasks/6332 requests/5491 tools. Included cohort260 tasks,
1056 requests. [Report](reports/lab025-replicas/README.md), compact CSV/manifest,
trace-admission and router-balance receipts under `traces/lab-s24b/session-summary`.
Old missing replica stages are not backfilled.

User's paper-inspired chart request is delivered at
[characterization-charts](reports/characterization-charts/index.html):30 selectable
conditions,90 Runtime/Tool/Agent SVGs plus concurrency overview, input hashes and
numerical data. Interval unions distinguish overlap/unattributed wall time;
prefill/decode uses exact request joins; cache denominators and per-backend KV are
explicit. Tool activity is a rule-based call-progress proxy; final is not judged
success. No new experimental factor or paid call was needed for chart generation.

Essential2,853files/5,324,095bytes verified remote=local SHA256
e4ea6040b582b495964566a2cd099b50904138ddb6a7a2a06cf88a2c64b601f9; extraction complete.
Bulk intentionally skipped; preserve lifecycle failed/_PartialArtifacts and
artifact_copy_verified=false. Parent destroyed+verified; watchdog stopped with
parent_teardown_verified_absent; independent provider query shows no owned runs.
Create1789421771.2381241→finish1789424143.9911594 (2372.75s) at$2.38592593/h≈$1.5726.
Approved extension cumulative≈$3.4933/$5.40; overall cumulative≈$118.42 from prior
approximate$102.8 base, not an invoice. No automatic new run or budget reset.

Chart numeric contracts10 tests and existing router4 tests pass; Ruff, all30
condition conservation checks and Chrome rendering/condition selection checked.
Basic CI1112pass3skip and structural contracts pass; same5 existing SSH fixture
identity violations remain. Private raw inputs/credentials are not published.
Continue using temporary worktrees and private gare2e/agentic-os-e2e only.


### §16 main publication and portable harness (2026-09-15)

User explicitly requested merging the experiment branch into private
`tteon/gare2e:main`, including agent instructions and the harness. This changes
the landing target for this publication from the historical development branch.
The separate public SEOCHO origin and repository visibility are not changed.

Start at [the experiment index](README.md), which renders representative SVGs
on GitHub and links all30 conditions. [AGENTS.md](AGENTS.md) and
[HARNESS.md](HARNESS.md) document metric invariants, source/receipt locations,
cloud-free chart reproduction and private prerequisites for live reruns. Root
AGENTS/README link them. The packaged lab-resume skill now reads tracked state
first, without requiring the original operator's home directory or local tracker.

The previous basic-CI address violations were synthetic SSH fixture hostnames;
all five now use the reserved .example domain, preserving parser/copy contracts.
Full basic CI passes, including identity and structural checks;31 focused tests,
docs/link checks, the six-entry print-plan and secret-guard30/30 self-test pass.
No new benchmark or paid resource was launched for publication. Lab025 outcomes
and original source/artifact hashes in §15 remain unchanged.


## 17. Lab026 sequential validation — live checkpoint (2026-09-16 KST)

User explicitly authorized engine instrumentation, higher-K baseline/MTP, then
actual long-context/prefix-reuse pressure without further confirmation. Source
50731235 on private gare2e/lab026-runtime-pressure; final landing targetmain.
Request026 / beadseocho-6niy.73focusedtests and basic1112pass3skip/allcontracts
passed. Exact pinned-image source/observer/profiler preflight passed; this is
not GPU performance evidence. The initial probe omitted a required config
constructor argument; its failed receipt is preserved, corrected check2passed.

New owned instance51135854, labelseocho-agentic-os-lab-s25, A100 SXM80GB pair
NVLink300, host195410,32effectiveCPUs, driver580.65.06/nativeCUDA. Actual initial
disk-inclusive rate$3.3562963/h. Create1789489330.799, independent12600swatchdog
deadline1789501930.799. Operator max$12.60 at<=3.60/h; no prior spend reset.
At this checkpoint directSSH passed and input staging is running. No new
comparison results yet. Do not launch a replacement while this instance exists.

Source bundleSHA256
43ee747ef962adc160073fab6ebd8f02f544a440d793a349c014571de1503f71.
Raw receipts live under the operator's ignored colocated-appliance/runs/lab-s25.
Runtime/Tool/Agent charts remain based on the previous completed30conditions
until the new live gates and measured windows finish. Preserve peers and
content-free publication; raw profiler traces remain private.


## 18. Lab026 stage1 passed; client-capacity recovery prepared (2026-09-16 KST)

Supersedes live section17: owned51135854 is destroyed and independently absent.
Watchdog terminalparent_teardown_verified_absent; lifetime2902.9403s, cost
$2.706424, historical cumulativeapprox$121.12. Essential3433files/12025743bytes
verified SHA80be72fc5cafea23d29548db1ad08e6510c9b9ef9e71c077fb6c003edb8c801d;
bulk remains partial. Stage1 observer, two-rank kernel profiler and grammar CPU
allpassed. Five measured windows: OFFK64 428.24, ONK64 432.44, grammarK8 53.49,
base-r1K8 123.64, base-r1K64 436.40 outputtok/s/GPU.

K128worker failures106/128 preceded any valid window. Agent thread count grew
from4289(K64) to6745(K128); sampler also reportedBlockingIOError. Precise initial
worker exception/originalpidslimit unknown because stderr was omitted. Local
exact-image bounded process test reproduces native-thread exhaustion and verifies
child-scoped native-thread=1 initialization. Bugseocho-zcc9 underseocho-6niy.
Recovery adds scopedworker limits, errno/classes, cgroupPID counters and private
stderr preservation. No GPU saturation claim from the failed condition.

The user's stages1/2/3 authorization remains active. Prepared runtime-recovery
queue skips completedstage1 and reruns all16baseline/MTP cells on one new host
with corrected workers, then10real-corpus context cells. Prior low-K results
remain separate; new controls are necessary after the harness change. New
lab-s25b:9800s watchdog at<=3.60/h,max9.80 plus2.706424 alreadyspent totals
12.506424<12.60. Exact-image preflight and localCI precede create. At this
checkpoint no owned liveinstance. Stage2/3 are still pending.


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

### Active completion run (2026-09-17)

lab-s25c owned instance **51261456**, source **9a2e0ab6**; watchdog7500s, max7.50USD. Lifecycle under local run root. Do not create a replacement until teardown is verified. Preflight v3 passed all8 checks; source bundle SHA43996eb9cd29ae71284773abde0d3a929321bc5d3032a0effcfe49de79324de5. Stage2 eight cells/two gates then stage3 ten context cells. Full basicCI passed; focused dynamic/engine/plan tests40passed. [Prior recovery report](reports/lab026-worker-recovery/README.md).

## 19. s25c ended; timeout recovery prepared (2026-09-17)

This supersedes the active51261456 checkpoint above. Instance destroyed and
artifacts verified. Six timed cells passed; MTP K128 failed exact-trace gate
with two180s TimeoutError requests whose IDs/timings were not preserved.
No MTP K256 or context cells ran. Total current-stage spend7.287970750USD.
Prepared next: `--runtime-timeout-recovery`, max5.00USD, paired high-K controls
then all context cells. Error IDs/timings preserved,900s transport timeout,
condition-scoped root traces; strict trace gate unchanged. Details and cost
receipts in [request026](lab/requests/026-runtime-saturation-kv-pressure.md).


## 20. Graph-system observability extension — 2026-09-22

The owner requested storage-structure/runtime/procedure and thread/hardware
observations in the existing Runtime / Tool / Agent views. See
[the implementation and coverage contract](GRAPH_SYSTEM_OBSERVABILITY.md).
`SEOCHO_GRAPH_SYSTEM_OBSERVER=1` enables pre-workload read-only DB capability
probes and five-second root-thread/cgroup/NUMA/device sampling. Existing
process-tree and out-of-band PROFILE evidence are reused. PMU, actual store
metadata, JVM/cache/Bolt and traversal internals remain explicit source gaps
until measured; no inferred zeros or retroactive claims.

Local Linux collector smoke, focused40tests, historical lab-s25 re-export
(1616tasks/7479requests/6496tools, unchanged five throughput values), browser
rendering and basic CI passed. This is implementation/contract validation, not
a live DozerDB/GPU performance result. Existing source bundles are unchanged;
a fresh bundle/preflight and observer overhead comparison precede live use.
No paid run or additional spend was initiated. The prior lab026 timeout recovery
and long-context execution remain pending; this extension does not close them.
Bead: seocho-tq2c.

## 21. Shared experiment platform — 2026-09-22

The [platform guide](EXPERIMENT_PLATFORM.md) documents a read-only FastAPI viewer,
immutable workspace/owner registry and CLI imports. Runtime / Tool / Agent charts
lead to condition and task timelines, then evidence-linked observations. Unknown
comparison contracts suppress improvement percentages. JSON metric extensions
allow contributor additions without executing artifact-supplied code.

Existing published reports import as 30 conditions and 10,821 tasks. They retain
unverified admission until reviewed manifests are supplied. Perfetto JSON exports
contain measured task/request/tool intervals. Reviewed Nsight SQLite and original
reports can be attached with checksums, profiler version, clock and capture scope;
there are no actual Nsight captures attached to the historical imports. No GPU
kernel activity is inferred from OTel spans.

Local HTTP/browser and focused tests validate navigation and download contracts.
This is a locally runnable platform, not a deployed team service or a new GPU/DB
performance result. Team hostname, TLS/auth proxy and actual profiler capture
remain deployment/experiment follow-ups. No new paid experiment was launched.
Bead: seocho-rn3p; architecture ADR-0227.

## 22. Graph query evidence and observability — 2026-09-25

Bead seocho-ix30 extends the current platform, not a separate benchmark. See
[graph optimization observability](GRAPH_OPTIMIZATION_OBSERVABILITY.md).
Query receipts distinguish attempts, DB submissions, reuse and separate PROFILE
replay. The viewer adds query logs, plan tables, quality and linked insights.
The optional local Collector/Tempo/Prometheus/Grafana companion passed actual
trace/log/scrape/health checks. LangSmith is analyzed but not connected.

Historical lab021 reanalysis: 120 tasks, 317 profiles, 2,309 operators; all DB-hit
and page-miss counters were present. The parser defect does not invalidate that
specific historical zero-miss observation. Parent topology remains unavailable.
A fresh user-authorized USD100 ceiling covers new live work. Pilot1 admission
failed before creation ($0). Pilot2 completed: 16 diagnostic/gate/comparison
conditions, 32 tasks, 167 attempts. Fixed reuse avoided19/20 DB submissions with
identical results; 24 agent comparison sessions had zero cache hits. No agent
performance benefit or quality promotion. Server joins119/122; three missing.

Owned instance52552894 was destroyed and absence verified; watchdog stopped.
Hourly cost estimate$0.991837, transfer/invoice unverified. Essential122files
checksum verified; lifecycle exit2 records intentionally skipped bulk transfer.
No new paid continuation scheduled. [Full result](reports/lab027-graph-evidence/README.md).

Local viewer at127.0.0.1:18775 uses the private registry with48conditions and
10,973tasks. Grafana13000, Prometheus19090, Tempo13200, Collector14317/14318;
actual pilot trace/log ingestion passed. LangSmith and team deployment remain
optional and unconfigured. The exact local paths/start commands are in ignored
experiment memory; public docs do not contain credentials.
