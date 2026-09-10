---
name: vast-agentic-e2e
description: Run and recover SEOCHO graph-agent E2E experiments on a paid Vast.ai co-located appliance (vLLM + DozerDB + Oxigraph) with bounded cost, verified staging, readiness gates, artifact checksums, and teardown verification. Use when the user asks to launch, resume, inspect, or tear down a Vast appliance run, rebuild the appliance bundle, or read a vast-colocated-lifecycle receipt. Do not use for ordinary local tests or the split-host (SSH-tunnel) pilot.
---

# Vast Agentic E2E (Claude Code edition)

Drive `scripts/benchmarks/vast_colocated_agentic_os_run.py` as a fail-closed
state machine. Every state below is one or more **Bash tool calls with an
explicit `timeout`**; the paid lifecycle itself runs with
`run_in_background: true`. Never skip forward after a failed state and never
launch a replacement instance while a prior one may still exist.

```
admit -> create -> provider running -> SSH ready -> stage -> checksum
      -> bootstrap -> services ready -> one-case gate -> matrix
      -> collect -> verify -> destroy -> verify absent
```

Repo root for every command: `/home/hadry/lab/seocho-room/seocho`. Artifacts,
bundles, and inputs live under `.seocho/` (gitignored). Always use `python3`;
the Vast vLLM image has no `python` alias (incident V12).

**Detach the paid lifecycle from this session.** In V13 the Claude Code
process exited and took the runner *and* the cost watchdog with it; the
instance idled for 8.8 h. Launch the runner through `systemd-run --user`
(linger is enabled on this host) so it survives the session, and keep the
Bash `run_in_background` call only as the notifier.

## How the Codex skill maps onto Claude tool calls

| Codex rule | Claude Code realisation |
|---|---|
| explicit timeouts on every provider/SSH op | `Bash` `timeout` per call: vastai 60 s, bundle build 300 s, local preflight 600 s; the runner already bounds its own SSH/SCP/destroy calls |
| long-running lifecycle | one `Bash` call with `run_in_background: true`; the harness notifies on exit. No `sleep` loops, no `vastai show` polling |
| observe milestones | read `vast-colocated-lifecycle.json` (`state` field) only when a milestone is due, or attach `Monitor` to that file |
| paid action | requires an explicit user go-ahead **in this session** (budget + label). Autonomous sessions stop at §4 and report |
| runner must outlive the session | `systemd-run --user --unit seocho-vast-vN ...` for the runner; `journalctl --user -u seocho-vast-vN` for its log |
| private evidence | `*.private.*` files are mode 0600. Read them with `sed -n`/`grep`; never paste raw model output or tokens into chat, commits, or public receipts |
| teardown is highest priority | any `destroy_verified_absent != true` is handled before any other analysis (§6) |

## 0. Ownership gate (always first, read-only)

```bash
timeout 60 vastai show instances --raw | python3 -c '
import json,sys; d=json.load(sys.stdin)
print([(i["id"], i.get("label"), i.get("actual_status"), i.get("host_id")) for i in d])'
```

Print ids/labels only — the raw instance JSON carries the `jupyter_token`
(never paste it into chat, journals or receipts; bead seocho-ffl4).

Expected: no instance whose label starts with `seocho-agentic-os-`. The Vast
key is shared with the gare2e handoff, so a teammate's instance (other label,
e.g. `gare2e-…`) may legitimately be live: it does not block a launch, but it
does share the account's billing — say so in the ledger. If one of **ours**
exists, do **not** create anything. Go to `references/recovery.md`
(cost-safety row) and resolve it first: bounded `show`, explicit
`destroy instance <id>` (ours only), then re-run the listing. Also check the
most recent `runs/*/vast-colocated-lifecycle.json` for
`destroy_verified_absent: false` and reconcile it against the listing.

## 1. Inputs and bundle freshness (local, no cost)

Bundle dir: `B=.seocho/benchmarks/agentic-os/colocated-appliance/bootstrap`.

1. Confirm every upload source exists. Public datasets: GRBench MAPLE/Physics
   `graph.json` + questions (HF `PeterJinGo/GRBench`) and the GTSQA
   with-graphs parquet (HF). Repo-provided: `examples/agentic-os/frozen/gate-10/`
   (discovery caches + schedules). Derived locally: `dozerdb-runtime.tar.gz`
   (`export_dozerdb_appliance_rootfs.py`) and the GRBench bulk-import CSV
   (step 1b). Missing input is a stop condition, not something to substitute.
   The GRBench graph is **not** uploaded as JSON any more: projecting 67.3M
   relationships through Bolt at run time took >2 h and ate the whole paid
   window (V13). Export once locally, then the appliance bulk-imports it
   offline before DozerDB starts (minutes):
   ```bash
   timeout 3600 uv run --project . python3 scripts/benchmarks/export_grbench_import_csv.py \
     --graph .seocho/datasets/graph-cot/graph-environments/maple/Physics/graph.json \
     --output-dir $B/grbench-import-vK --workspace-id grbench-maple-physics
   ```
   The manifest's `workspace_id` must equal the id the Text2Cypher runners
   query (`grbench-maple-physics`, runtime-valid). raw16k-v6 queried
   `grbench:maple:physics` against a store loaded as `grbench-maple-physics`
   and every GRBench attempt returned zero rows.
2. A source bundle is stale if any included path is newer than it:
   ```bash
   find src scripts/benchmarks docker/agentic-os-appliance pyproject.toml \
     -newer $B/seocho-source-vN.tar.gz -type f | grep -v __pycache__
   ```
   Non-empty output means rebuild with the next version number:
   ```bash
   timeout 300 python3 scripts/benchmarks/prepare_agentic_os_appliance_bundle.py \
     --output $B/seocho-source-vN+1.tar.gz \
     --receipt-out $B/seocho-source-vN+1-receipt.json
   ```
   The script refuses to overwrite; never delete an old bundle to reuse its name.
3. Standalone bootstrap digest must equal the archived member:
   ```bash
   tar -xzOf $B/seocho-source-vN.tar.gz scripts/benchmarks/bootstrap_agentic_os_appliance.sh | sha256sum
   sha256sum scripts/benchmarks/bootstrap_agentic_os_appliance.sh
   ```
4. Local appliance preflight (needs local Docker and the exact vLLM image;
   it runs ephemeral containers to prove `python3`, the tool parser, and the
   DozerDB runtime layout inside that image):
   ```bash
   timeout 600 python3 scripts/benchmarks/preflight_agentic_os_appliance.py \
     --source-bundle $B/seocho-source-vN.tar.gz \
     --bootstrap scripts/benchmarks/bootstrap_agentic_os_appliance.sh \
     --dozer-runtime $B/dozerdb-runtime.tar.gz \
     --vllm-image vllm/vllm-openai:v0.27.1 \
     --receipt-out $B/appliance-preflight-vN.json
   ```
   Stop on any failing gate in the receipt.

## 2. Pin the model revision (required by the entrypoint)

`docker/agentic-os-appliance/entrypoint.sh` refuses to start without
`VLLM_MODEL_REVISION`. Resolve and record the immutable commit:

```bash
timeout 30 git ls-remote https://huggingface.co/Qwen/Qwen3.6-27B HEAD
```

Put the 40-hex commit into the remote command below and into the run notes.

## 3. Offer admission (read-only)

```bash
timeout 60 vastai search offers "num_gpus=2 rentable=true" -o dph_total --raw
```

Keep the provider predicate minimal (compound predicates silently dropped
valid rows on 2026-09-02) and filter locally, exactly as the runner does.
Filter for the target GPU (`A100`, 2x40 GB = `gpu_total_ram 81920`) and
check `cpu_ram` (appliance needs >= 48 GiB: DozerDB heap 8g + pagecache 24g + vLLM host memory; the admission floor is 64 GiB),
`cpu_cores_effective >= 16`, `disk_space >= 200`. Remember the live price
includes the disk allocation (`--disk-gb 200` adds about $0.06/h), so set
`--max-dph` against the *live* total and state the hard budget as
`max_dph * max_runtime_s / 3600` in the report. Zero candidates is a
legitimate outcome: report it and wait; do not loosen GPU or RAM floors to
force a launch.

## 4. Paid launch (needs explicit user approval in this session)

Before calling, restate to the user: label, offer filter, `--max-dph`,
`--max-runtime-s`, worst-case cost, and the new `--local-artifacts` dir.
The runner arms an independent cost watchdog (`vast_instance_watchdog.py`)
at create time; its deadline is `create + max_runtime_s`.

Launch from the repo root as a transient user unit so the runner, its SSH
session, and the cost watchdog survive this Claude Code process. Use a
**new** run directory (`vN-e2e`), the standalone bootstrap path, and the
bulk-import inputs instead of `graph.json`:

`cuda_max_good>=13.0` keeps hosts whose driver matches the image's CUDA major (lab-s5: a driver-525 host died at vLLM start with error 803).

```bash
B=.seocho/benchmarks/agentic-os/colocated-appliance/bootstrap
G10=examples/agentic-os/frozen/gate-10   # frozen discovery caches + schedules (in the repo)
IMP=$B/grbench-import-vK
systemd-run --user --collect --unit seocho-vast-vN --working-directory "$PWD" \
  --property=KillMode=process \
  python3 scripts/benchmarks/vast_colocated_agentic_os_run.py \
  --offer-query "num_gpus=2 rentable=true cuda_max_good>=13.0" --gpu-name-regex A100 \
  --min-num-gpus 2 --min-gpu-total-ram 81920 --min-effective-cpu 16 \
  --min-cpu-ram-mb 65536 --min-reliability 0.98 \
  --image vllm/vllm-openai:v0.27.1 --disk-gb 200 \
  --label seocho-agentic-os-colocated-vN --max-dph 2.25 \
  --startup-timeout-s 900 --max-runtime-s 10000 \
  --remote-artifacts /artifacts \
  --local-artifacts .seocho/benchmarks/agentic-os/colocated-appliance/runs/vN-e2e \
  --upload scripts/benchmarks/bootstrap_agentic_os_appliance.sh=/workspace/bootstrap/bootstrap_agentic_os_appliance.sh \
  --upload $B/dozerdb-runtime.tar.gz=/workspace/bootstrap/dozerdb-runtime.tar.gz \
  --upload $B/seocho-source-vN.tar.gz=/workspace/bootstrap/seocho-source.tar.gz \
  --upload $IMP/grbench-nodes.csv.gz=/workspace/input/grbench-nodes.csv.gz \
  --upload $IMP/grbench-rels.csv.gz=/workspace/input/grbench-rels.csv.gz \
  --upload $IMP/grbench-import-manifest.json=/workspace/input/grbench-import-manifest.json \
  --upload .seocho/datasets/graph-cot/data/processed_data/maple/Physics/data.json=/workspace/input/grbench-questions.json \
  --upload $G10/grbench-discovery-serving.json=/workspace/input/grbench-discovery.json \
  --upload $G10/grbench-single-tool-schedule.json=/workspace/input/grbench-single-tool-schedule.json \
  --upload .seocho/datasets/gtsqa/gtsqa-with-graphs/test-00000-of-00001.parquet=/workspace/input/gtsqa-with-graphs.parquet \
  --upload $G10/gtsqa-discovery-serving.json=/workspace/input/gtsqa-discovery.json \
  --upload $G10/gtsqa-single-tool-schedule.json=/workspace/input/gtsqa-single-tool-schedule.json \
  --upload $G10/gtsqa-multi-tool-schedule.json=/workspace/input/gtsqa-multi-tool-schedule.json \
  --remote-command "timeout --signal=TERM 9200 env \
    VLLM_MODEL=Qwen/Qwen3.6-27B VLLM_MODEL_REVISION=<40-hex commit> \
    VLLM_TENSOR_PARALLEL_SIZE=2 \
    VLLM_EXTRA_ARGS='--gpu-memory-utilization 0.92 --max-model-len 32768' \
    SEOCHO_GRBENCH_IMPORT_DIR=/workspace/input SEOCHO_GRBENCH_WORKSPACE_ID=grbench-maple-physics \
    DOZERDB_HEAP_INITIAL_SIZE=8g DOZERDB_HEAP_MAX_SIZE=8g DOZERDB_PAGECACHE_SIZE=24g \
    SEOCHO_REPO_ROOT=/workspace/seocho SEOCHO_APPLIANCE_INPUT=/workspace/input \
    SEOCHO_APPLIANCE_ARTIFACTS=/artifacts SEOCHO_E2E_PHASE=all \
    SEOCHO_MAX_TURNS=6 SEOCHO_MAX_OUTPUT_TOKENS=2048 SEOCHO_TOKEN_BUDGET=16384 \
    bash /workspace/bootstrap/bootstrap_agentic_os_appliance.sh \
    bash /workspace/seocho/scripts/benchmarks/run_agentic_os_colocated_e2e.sh"
# notifier only: wakes this session when the unit ends
# Bash(run_in_background): while systemctl --user is-active --quiet seocho-vast-vN; do sleep 60; done
```

Rules baked into that command:

- The bootstrap is uploaded **separately** and invoked at
  `/workspace/bootstrap/...`, never from inside the archive (incident V9/V10).
- Do not pass `NEO4J_PASSWORD`; the bootstrap generates an appliance-only
  secret so it never lands in shell history, transcripts, or receipts.
- Inner `timeout 9200` < `--max-runtime-s 10000` so artifacts can still be
  hashed and copied before the watchdog deadline.
- `SEOCHO_E2E_PHASE=gate` runs only the one-case gates; use it when the
  user wants a cheaper admission-only run before committing to `all`.
- `SEOCHO_GRBENCH_IMPORT_DIR` switches the appliance to the offline
  `neo4j-admin database import` path and the e2e to verify-only counting;
  the loader refuses a manifest whose workspace differs from
  `SEOCHO_GRBENCH_WORKSPACE_ID`.
- The runner passes `vastai destroy instance <id> -y`; without `-y` the CLI
  prompts and the destroy times out (V12/V13 `destroy_exit_code=124`).
- Experiment knobs in the remote `env`: `SEOCHO_PROMPT_PROFILE=baseline|strengthened`
  (PE arm), `SEOCHO_GATE_B0_CRITERION=validated|attempted` (lab 001),
  `SEOCHO_E2E_PHASE=gate|main|all`.

## 4b. Persistent lab instance (lab request 002)

For iteration, keep one appliance alive instead of paying bootstrap per
attempt. Same launcher as §4, but the remote command **holds** after
readiness and every experiment is an SSH invocation of the e2e script.

```bash
# 1) launch (systemd unit as in §4); the hold keeps vLLM + DozerDB up.
#    --max-runtime-s is the hard watchdog deadline (deadline_hours × 3600).
--remote-command "env <same env as §4> \
  bash /workspace/bootstrap/bootstrap_agentic_os_appliance.sh \
  bash -c 'echo appliance-ready > /artifacts/hold.ready; sleep infinity'"

# 2) wait for /artifacts/hold.ready (receipt stays at remote_execution), then per experiment:
URL=$(vastai ssh-url <id>); PORT=${URL##*:}; HOST=root@${URL#ssh://root@}; HOST=${HOST%%:*}
ssh -p $PORT $HOST 'env SEOCHO_E2E_PHASE=gate SEOCHO_PROMPT_PROFILE=strengthened \
  SEOCHO_GATE_B0_CRITERION=attempted SEOCHO_APPLIANCE_ARTIFACTS=/artifacts/run-NN \
  SEOCHO_REPO_ROOT=/workspace/seocho SEOCHO_APPLIANCE_INPUT=/workspace/input \
  VLLM_MODEL=Qwen/Qwen3.6-27B NEO4J_PASSWORD="$(cat /workspace/appliance.secret)" DOZERDB_PID=$(pgrep -o -f org.neo4j) \
  bash /workspace/seocho/scripts/benchmarks/run_agentic_os_colocated_e2e.sh'
scp -P $PORT -r $HOST:/artifacts/run-NN <local-run-dir>/   # then sha256 verify as in §6

# 3) end of day: vastai destroy instance <id> -y ; vastai show instances --raw == []
```

## 4c. Serving-configuration sweep (the optimisation channel)

An engineer changes `examples/agentic-os/serving-sweep.yaml` (variants =
vLLM args and/or env, workload held fixed), nothing else:

```bash
uv run --project . python3 scripts/benchmarks/sweep_agentic_os_serving.py \
  examples/agentic-os/serving-sweep.yaml --ssh-url "$(vastai ssh-url <id>)" \
  --local-root <run>/sweep [--only baseline --only cudagraph-full] [--dry-run]
```

Per variant the driver: restarts vLLM on the appliance with the variant's
arguments (`docker/agentic-os-appliance/restart_vllm.sh`, config receipt),
runs the frozen cells under the workload wrapper (`run_lab_pass.sh`), pulls
`/artifacts/run-<pass>`, joins telemetry, decomposes attempts, and writes
`serving-sweep-summary.{csv,md}` with per-cell deltas vs `baseline`, comparing
latency only where the funnel outcome matches. Resumable per variant. Both
helper scripts must exist on the appliance (bundle ≥ v26, or scp them into
`/workspace/seocho/docker/agentic-os-appliance/` on older sessions).

Rules: one instance at a time (§0 gate before launch); every SSH run gets its
own `/artifacts/run-NN` and a LEDGER row; the watchdog deadline is the hard
cap, not a reminder; destroy is manual and verified at the end of the session.
The entrypoint writes the appliance-only DozerDB secret to `/workspace/appliance.secret` (mode 0600, phase `appliance_secret_file`) so SSH-run experiments can read it without it ever entering a receipt.

## 5. While it runs

The harness wakes you when the background command exits. Between
milestones, at most read the receipt:

```bash
python3 -c "import json;d=json.load(open('<run>/vast-colocated-lifecycle.json'));print(d['state'],d.get('instance_id'),d.get('failure_class'))"
```

Rough milestone budget: `provider_running` within 900 s of create;
`uploads_verified` a few minutes later (5.3 GiB of inputs);
`remote_execution` covers bootstrap + model download/compile (10-20 min
cold) + gates + matrix. If the state has not moved past `staging` in
30 min, read `remote-execution.stderr.private.log` and `bootstrap-phases.private.tsv`
rather than restarting. Do not run `vastai show` in a loop; the runner and
the watchdog already do bounded status checks.

## 6. After exit: teardown first, evidence second

0. If this session was resumed, first check the unit and the account:
   `systemctl --user is-active seocho-vast-vN`, then
   `timeout 60 vastai show instances --raw`. A receipt frozen at
   `remote_execution` with no runner process means the parent died; the
   watchdog may have died with it (V13). Go straight to step 1.
1. Teardown truth, in this order:
   - receipt `destroy_verified_absent` must be `true`;
   - `vast-cost-watchdog.json` `terminal_status` should be a clean stop, not
     `deadline_destroyed` (that means the parent failed to destroy);
   - `timeout 60 vastai show instances --raw` must be `[]`.
   Any mismatch is a cost-safety incident: `timeout 60 vastai destroy instance <id>`,
   re-list, and report it as the top blocker before touching results.
2. Then read `state` and `failure_class`. Use the first matching row in
   `references/recovery.md`; read private stderr before naming a cause.
   Remote exit 3 with `e2e/one-case-gate.json` present and every lane
   `complete` in `progress.tsv` is a **gate verdict**, not a run failure
   (V17): report the per-cell checks instead of retrying.
3. Gate evidence lives under `<run>/e2e/`: `service-readiness.json`,
   `vllm-preflight.json`, `one-case-gate.json`, `progress.tsv`. Aggregate with:
   ```bash
   python3 scripts/benchmarks/validate_agentic_os_e2e_acceptance.py \
     --service-readiness <run>/e2e/service-readiness.json \
     --vllm-preflight <run>/e2e/vllm-preflight.json \
     --one-case-gate <run>/e2e/one-case-gate.json \
     --workload-receipt <run>/... --telemetry-join <run>/... \
     --receipt-out <run>/e2e-acceptance.json --stage final_raw_matrix
   ```
4. A retry is a new run ID and a new `--local-artifacts` dir. Never reuse or
   delete a failed run directory.
5. Workload analysis (free, local): join samples to attempt windows, then
   decompose and draw one timeline per cell.
   ```bash
   uv run --project . python3 scripts/benchmarks/join_agentic_os_gpu_samples.py <run>/e2e/gate-* \
     --vllm-samples <run>/gate-workload/vllm-metrics.jsonl --gpu-samples <run>/gate-workload/gpu.csv \
     --max-gap-s 2.5 --output <run>/e2e/gate-case-telemetry.json
   uv run --project . python3 scripts/benchmarks/analyze_agentic_os_attempt_windows.py <run>/e2e/gate-* \
     --telemetry-join <run>/e2e/gate-case-telemetry.json --gpu-samples <run>/gate-workload/gpu.csv \
     --host-samples <run>/gate-workload/host-process.csv --vllm-samples <run>/gate-workload/vllm-metrics.jsonl \
     --output-dir <run>/analysis
   ```

## Stop conditions

Stop before the matrix when the ownership gate, bundle digest, local
preflight, staging hashes, bootstrap, service readiness, fixed DB queries, or
the one-case gate fails. Fix the exact named failure. Do not infer a cause from
an exit code alone and do not launch speculative replacement instances.

Do not claim latency, throughput, prefix-cache effectiveness, database cost,
or agent quality from mocks, failed bootstraps, or incomplete telemetry.

## Receipt hygiene (current gap)

The runner copies the raw `vastai show instance --raw` payload into the
lifecycle receipt under `startup`, which includes the provider `jupyter_token`
and public IP. Until the runner redacts it, treat
`vast-colocated-lifecycle.json` as private: keep it under gitignored
`.seocho/`, do not paste it whole into chat, PRs, or paper appendices.

## Observability acceptance

Before a full matrix, confirm artifacts will cover: agent turns/handoffs/tool
calls/token composition; middleware serialization, validation, retries,
workspace/correlation IDs; graph DB query digest, plan, rows, CPU/RAM,
retrieval duration; vLLM TTFT/TPOT, queue/prefill/decode, prefix-cache
queries/hits, KV utilization; timestamped host CPU/RAM/GPU with process-role
attribution and sampling gaps reported.

Read `references/recovery.md` when a state fails and
`references/incidents.md` before changing the bootstrap, image, transport,
or teardown path.

OTel: each pass leaves `run-<pass>/otel/spans.jsonl` (receipt-derived span trees); add `SEOCHO_OTLP_ENDPOINT=http://collector:4317` to the remote env to push spans and enable vLLM OTLP tracing (collector must be reachable from the instance).
