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
