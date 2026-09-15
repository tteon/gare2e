# Vast Agentic E2E recovery

Use the first matching failure class. Preserve the failed receipt and logs; a
retry gets a new run ID and a new `--local-artifacts` dir. Every command below
is a single `Bash` call with the shown `timeout`.

| Evidence | Classification | Response |
|---|---|---|
| `vastai show instances --raw` not `[]` at start | leftover paid instance | `timeout 60 vastai show instance <id> --raw`, then `timeout 60 vastai destroy instance <id>`, re-list until `[]`. No create before that. |
| receipt `destroy_verified_absent=false` or `destroy_exit_code=124` | cost-safety incident | Same as above. Also read `vast-cost-watchdog.json`; if `terminal_status` is missing the watchdog may still be armed: leave it, verify absence, then report. |
| `vastai copy`/rsync in `CLOSE_WAIT` | provider copy transport | Never use `vastai copy`; the runner uses direct `scp` from `vastai ssh-url`. Destroy and retry with a new run ID. |
| provider `running`, SSH exit 255 | SSH endpoint not ready | Runner retries the direct-SSH `true` probe within `--startup-timeout-s`. Do not stage manually. |
| remote upload hash differs | corrupt staging | Runner stops before bootstrap. Check local disk/network, preserve receipt, destroy. |
| bootstrap path missing | archived entrypoint | Bootstrap must be its own `--upload` to `/workspace/bootstrap/` and invoked from there. |
| remote exit 127 | missing command/path | `sed -n 1,40p <run>/remote-execution.stderr.private.log`; fix only the named executable. |
| `python: command not found` | image has only `python3` | Use `python3` everywhere; re-run local preflight inside the exact image before launch. |
| Dozer or Java missing | Docker ENV not restored | Verify `JAVA_HOME=/opt/java/openjdk`, `NEO4J_HOME=/var/lib/neo4j`, PATH in entrypoint. |
| `VLLM_MODEL_REVISION` unset error | entrypoint contract | Resolve with `git ls-remote https://huggingface.co/<model> HEAD` and pass the commit. |
| vLLM readiness fails | model/server startup | Read `<run>/e2e/vllm-preflight.json` and private vLLM logs; do not start agent cases. |
| DozerDB readiness fails | database startup | Read `appliance-phases.private.tsv` and DozerDB private logs; do not start agent cases. |
| fixed query fails | interface/schema/data gate | Read `service-readiness.json`; do not run the matrix. |
| agent never calls graph tool | trigger/tool-description gate | Inspect trace and tool schema in `one-case-gate.json`; final-answer accuracy is not retrieval evidence. |
| remote run fails after artifacts exist | late workload failure | Runner hashes and collects partial artifacts; mark lanes incomplete from `progress.tsv`, then destroy. |
| zero admitted offers | market state | Report and wait. Do not lower GPU RAM, CPU RAM, or reliability floors to force a launch. |
| receipt frozen at `remote_execution`, no local runner/watchdog process | parent session died (V13) | `timeout 60 vastai show instances --raw`; if present: `vastai ssh-url`, collect `/artifacts` with `scp -r host:/artifacts <run>/` (no `/.`), verify sha256, then `vastai destroy instance <id> -y`, re-list. Record `manual_recovery` in the receipt. |
| `service_start` error after ~40 min, vLLM stdout ends at `vLLM is using nccl` | NCCL TP init hang (V14) | Read `gpu-topology.private.txt` and `vllm-readiness-last-error.private.log`. `nccl_env.sh` already disables P2P on non-NVLink hosts (`SEOCHO_NCCL_P2P_MODE=auto`); if it recurs on an NVLink host, set `SEOCHO_NCCL_P2P_MODE=disable`, or prefer a host with `bw_nvlink>0` / TP=1 on one 80 GB GPU. Check `SEOCHO_NCCL_P2P_DECISION` in `gpu-topology.private.txt`. |
| gate cell `runner_exit_code=1` within ~2 s, no `attempts/` dir | lane runner import/setup failure (V16) | Read `attempts/*/runner.stderr.private.log`; if absent (older coordinator) reproduce locally with the coordinator's exact command via `_runner_command`. Check the bundle lists every repo package the runner imports (`runtime/`). |
| lifecycle `RuntimeError: artifact checksums did not match`, mismatch only on `*.private.log` | volatile log still growing (V16) | Newer runner records it under `volatile_artifacts`; on an old receipt compare both remote digests before treating the copy as corrupt. |
| vLLM: CUDA graphs captured, then workers at 100 % util but ~40 W, no `/v1/models`, only `shm_broadcast` heartbeats in stdout | post-capture NCCL spin (lab-s1) | Record `gpu-topology.private.txt` and `startup.host_id`; kill the remote `timeout` wrapper so the entrypoint cleanup + runner teardown run; relaunch with `--exclude-host-id <host>`. |
| `dozer_bulk_import` phase fails | CSV/import contract | Read `dozerdb-bulk-import.private.log` and the report file; re-run the exporter locally and rehearse the import in a throwaway `graphstack/dozerdb` container before relaunching. |
| GRBench queries validated + executed but always 0 rows | workspace scope mismatch (raw16k-v6) | Compare `config.workspace_id` in run receipts with `MATCH (n:GRBenchNode) RETURN DISTINCT n._workspace_id`; do not read 0 rows as model failure until they match. |

Avoid routine high-frequency polling. The background lifecycle wakes the
session on exit; check the receipt at milestones only. Repeated `vastai show`
calls are neither workload telemetry nor paper evidence.
| `service_start` error within ~2 min, vLLM stderr `Error 803: system has unsupported display driver / cuda driver combination` | Host NVIDIA driver older than the image's CUDA (cu130 needs driver ≥ 580) | Not recoverable on that host. Add `cuda_max_good>=13.0` to the offer query, `--exclude-host-id` the host, relaunch. |
| lifecycle `state=failed`, `remote workload exited with status 3`, but `queue-receipt.jsonl` shows every pass `exit_code 0` | (historical, lab-s3/s4/s6) the workload wrapper turned sampler errors / telemetry gaps into exit 3 | Fixed: the wrapper now exits with the workload's status and records `telemetry_degraded` in `colocated-workload-receipt.json`. Trust queue and attempt receipts; check `telemetry_degradation` before using per-attempt vLLM windows. |
