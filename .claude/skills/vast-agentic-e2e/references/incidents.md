# Known co-located appliance incidents

These incidents are regression inputs, not retry instructions.

| Run | Last successful state | Root cause | Required regression gate |
|---|---|---|---|
| V8 | instance running | `vastai copy` rsync daemon stayed in `CLOSE_WAIT` | Direct SCP only; bounded transfer timeout. |
| V9/V10 | uploads staged | Bootstrap was inside an unextracted source archive | Standalone bootstrap upload plus source/standalone digest equality. |
| V11 | provider running | Provider status preceded direct SSH readiness | Successful SSH `true` gate before staging. |
| V12 | uploads verified | Exact vLLM image had `python3` but no `python` alias; teardown `destroy` timed out (exit 124, `destroy_verified_absent=false`) | `python3` throughout appliance scripts; binary preflight inside the exact image; independent cost watchdog (added 2026-09-01) plus manual absence check on the next session. |
| V13 (2026-09-02, instance 49648917, $1.62/h, 8.8 h billed) | vLLM + DozerDB ready, vLLM native-tool preflight passed | (a) GRBench MAPLE/Physics Bolt projection (2.76M nodes, 67.3M rels, heap 2 GiB) was 95% done when the inner `timeout 9200` fired; no gate case ever ran. (b) The Claude Code session exited and killed the local runner **and** the `start_new_session` watchdog; nobody destroyed the instance until the next session. (c) `vastai destroy instance` without `-y` blocks on a prompt; (d) `scp host:/artifacts/.` is rejected by OpenSSH 8.9 scp. | Offline `neo4j-admin database import` from exporter CSV before DozerDB starts, verify-only counting in the e2e, bounded Bolt fallback; runner launched via `systemd-run --user`; `-y` on destroy; scp without `/.` (both unit-tested); resumed sessions run the §6 step-0 check first. |
| V14 (2026-09-03, instance 49693005, $2.17/h, 49 min billed, gate-only) | bulk import done in 129 s, DozerDB ready with 2 indexes ONLINE | vLLM TP=2 hung right after `vLLM is using nccl==2.30.7` on a 2x A100 PCIe host (Japan, host 458442, `bw_nvlink=0`); the 1200x2 s readiness loop then burned 40 min before `service_start` failed. Runner teardown worked (`-y`, verified absent). | Entrypoint exports `NCCL_P2P_DISABLE=1` + `NCCL_DEBUG=WARN`, records `nvidia-smi topo -m` and `/dev/shm`, and fails fast when vLLM stdout stops growing for 600 s before readiness. |
| V15 (2026-09-03, instance 49697610, $1.59/h, 24 min billed, gate-only) | bulk import 137 s, DozerDB ready, vLLM downloading weights | The 600 s stdout-only stall detector added after V14 fired during the silent 51.75 GiB weight download (V13 shows an 803 s gap there). NCCL init itself passed on this A100 SXM4 host with `NCCL_P2P_DISABLE=1`. | Stall detector now treats HF-cache and GPU-memory growth as progress with a 900 s default. Never add a fail-fast gate without measuring the healthy run's silent intervals first. |
| V16 (2026-09-03, instance 49699245, $1.59/h, 46 min billed, gate-only) | services ready (bulk import 147 s, vLLM 17.5 min), 8 gate cells executed | (a) All four GTSQA cells died in ~2 s: the source bundle lacked `runtime/`, which `run_gtsqa_text2sparql_react.py` imports; the coordinator kept only a stderr digest, so no artifact showed the `ModuleNotFoundError`. (b) Three of four GRBench cells failed the gate's `query_validated` check because the raw model's Cypher omitted the `_workspace_id` scope (`missing_workspace_scope`, `query_policy_rejected`); the single/non-reasoning cell validated 5 queries and returned 6 rows, the first non-empty GRBench result on this pipeline. (c) The lifecycle ended `RuntimeError` because vLLM stdout kept growing between remote hash and copy. Teardown verified on its own. | Bundle ships `runtime/`; bootstrap `import_preflight` imports every lane runner dependency before vLLM starts; coordinator writes `runner.std{out,err}.private.log`; runner classifies a remote file whose digest changes between passes as `volatile_artifacts` instead of failing. |
| V17 (2026-09-03, instance 49747203, $1.87/h, 33 min billed, gate-only) | **all infrastructure states passed**: import preflight, bulk import 139 s, vLLM ready 4.5 min, 8/8 gate cells executed, volatile vLLM log tolerated, teardown verified | Lifecycle still ends `RemoteExecutionFailed` (exit 3) because the one-case gate is a fail-closed *quality* gate: 5/8 raw-B0 cells had every query rejected by the scope/policy validator or issued none. Not an infrastructure defect. | Decide the gate's B0 criterion (validated vs attempted) before spending on a full matrix; treat exit 3 with `one-case-gate.json` present as "gate verdict", not "run failure". |
| lab-s1 (2026-09-05, instance 49939537, host 3497 Czechia A100 SXM4×2, $1.65/h, ~40 min billed) | bulk import, DozerDB ready, vLLM weights loaded, torch.compile + CUDA graph capture complete | Workers then spun at GPU util 100 % / ~41 W with no port 8000 for 14+ min: a post-capture NCCL collective that never completes. `nvidia-smi topo` shows GPU0–GPU1 = NODE (across PCIe host bridges, no NVLink); V17's healthy host was PHB. The stall detector did not fire because the EngineCore's 60 s shm heartbeat kept stdout growing. Orderly teardown triggered by killing the remote `timeout` wrapper. | Runner `--exclude-host-id` (458442, 3497); stall detector ignores `shm_broadcast` lines; prefer offers whose topology is PHB/NV# when it can be read (only after create). |
| lab-s2 (2026-09-05, instance 49941854, host 445596 California A100 SXM4×2, $1.87/h, 218 min billed) | **everything ready in 7 min** (bulk import 142 s, vLLM ready 4.5 min, hold.ready written) | No experiment ran: passes were to be launched interactively over SSH from the Claude Code session, which exited minutes after readiness; the appliance idled 3.5 h until SSH dropped (exit 255) and the runner collected + destroyed. A process design fault, not an appliance fault. | Persistent mode executes an uploaded **pass queue** on the appliance (`run_lab_queue.sh`) before holding; the controller only collects. Never make paid time depend on the controller's liveness. |
| raw16k-v6 (split-host, 2026-08-31) | 120 episodes complete | Text2Cypher runners queried `_workspace_id = grbench:maple:physics`; the store held `grbench-maple-physics`. All 40 GRBench attempts executed queries against an empty scope (0 rows, abstained). | Coordinator pins `--workspace-id grbench-maple-physics` for GRBench lanes; loader/exporter default to the same id; verify-only mode rejects a manifest with a different workspace. GRBench rows in the v6 catalog are plumbing evidence only, not retrieval evidence. |

2026-09-02 reconciliation: `vastai show instances --raw` returned `[]`, so the
V12 instance (49393517) is gone despite its receipt saying otherwise. Treat a
`false` teardown flag as "unknown until re-listed", never as "still billing"
or "safe".

DozerDB runtime review also found that its curated archive contains only
`opt/`, `startup/`, and `var/`, which avoids overwriting CUDA/vLLM system
files. The Docker image environment is not in the archive, so the appliance
must restore and validate at least:

```text
JAVA_HOME=/opt/java/openjdk
NEO4J_HOME=/var/lib/neo4j
PATH=/var/lib/neo4j/bin:/opt/java/openjdk/bin:...
NEO4J_EDITION=community
```

The upstream DozerDB entrypoint also assumes container user/group and
`su-exec` support. A co-located rootfs launch must either reproduce and test
those dependencies or use a directly validated Neo4j/Dozer start path.

Receipt hygiene: the lifecycle receipt embeds the raw provider instance
payload (`startup`), including `jupyter_token` and the public IP. Keep the
receipt under gitignored `.seocho/` until the runner redacts those fields.
