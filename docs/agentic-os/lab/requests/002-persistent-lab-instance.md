---
id: 002
title: Persistent Vast instance for iteration (no destroy per run)
state: done
created: 2026-09-05
owner_intent: user
bead: seocho-hijp
---

## Intent (user)
(draft by Claude — edit freely) Keep one appliance alive (vLLM + DozerDB + Oxigraph
ready) and iterate gate/matrix/treatment runs over SSH instead of paying the
25-minute bootstrap per attempt.

## Acceptance (user; Claude may draft)
- [x] One instance up with services ready; readiness receipts collected (lab-s3).
- [x] Repeated e2e passes (queue mode) with per-pass artifacts.
- [x] Watchdog armed; destroyed and verified absent when the queue ended.

## Constraints (user)
- Hourly cap and daily deadline to be set in Approval.

## Feasibility (Claude)
- verdict: feasible without runner code changes
- basis: the existing runner already does create/upload/bootstrap; a "hold" remote
  command (`entrypoint ... sleep infinity` after readiness) keeps services up; gate and
  matrix are then launched with `ssh ... run_agentic_os_colocated_e2e.sh` and artifacts
  pulled with the same scp/checksum path. Watchdog script is independent and takes a deadline.
- alternatives: pause/resume via Vast (`vastai stop instance`) keeps disk but re-bills
  storage; not needed for a one-day session.
- estimated cost/time: $1.6–1.9/h; a 4 h session ≈ $7. Setup ≈ 25 min.

## Approval (user)
approved: yes  # chat approval 2026-09-05 15:5x (AskUserQuestion: 유지 인스턴스 + PE-0/PE-1 게이트)
budget_usd: 12  # proposed: 6 h × ≤$2.25/h ceiling; typical A100 SXM4×2 ≈ $1.6–1.9/h
deadline_hours: 6

## Plan (Claude)
1. Add a `persistent` mode note to the vast-agentic-e2e skill (hold command, SSH run recipe, end-of-day destroy).
2. Launch with `--max-runtime-s` = deadline; record instance in LEDGER.md.
3. Run 001/003/004 experiments against it; collect artifacts per run.

## Log (Claude)
- 2026-09-09 — lab-s4 queue session for 008 A (≤ 5.5 h, ≤ ~$10.5): bundle v28, serving caches reused from lab-s3, outer GPU sampler off.
- 2026-09-09 — lab-s3 queue session complete: instance 50270476 (Slovenia, A100 SXM4×2, $1.47/h), services ready in ~8 min, 4 passes ran unattended (cache 5 s, PE-0 strata gate 35 min, PE-0 main 1 h 44 min, PE-1 gate 35 min), runner collected (checksums ok) and destroyed; 220 min ≈ $5.38 of the $10.5 ceiling. Queue mode works; no controller dependence.
- 2026-09-08 — re-approved in chat for a queue-mode session (lab-s3): uploaded pass queue = cache re-tokenisation → PE-0 stratified gate → PE-0 B0 main (reasoning off, strata-v1) → PE-1 stratified gate; no hold; runner collects + destroys when the queue ends; watchdog 5.5 h; ≤ $10.5 at $1.9/h. Bundle v27.
- 2026-09-07 — post-mortem of lab-s2: services were ready 7 min after launch (host 445596, healthy topology), the hold worked, but the passes were to be issued live from my session over SSH and that session died minutes later; 3.5 h of idle hold ≈ $6.8, then the runner tore down cleanly at SSH loss. Root cause is the design: the persistent mode made experiment execution depend on the controller's liveness. Fix: the appliance runs a **pass queue** uploaded with the inputs (run_lab_queue.sh) and only *then* holds; the local side just pulls artifacts. Session budget used $7.8 of $12; a new approval is needed for the next session.
- 2026-09-05 17:09 — lab-s1 (host 3497) torn down after a post-CUDA-graph NCCL spin (14+ min, GPU 100 %/41 W, no port). Artifacts collected (checksums ok), destroy verified, 36 min ≈ $1.0. Relaunching with --exclude-host-id 458442,3497 and bundle v26 (stall detector ignores shm heartbeat; helper scripts on board).
- 2026-09-05 16:33 — third attempt lost the admitted offer 39460471 to another renter within ~60 s. Switched to an admission-retry launcher (systemd unit seocho-vast-lab-s1d, 60 s cadence, ≤120 attempts, ownership gate before each try); admission misses create nothing and cost nothing.
- 2026-09-05 16:29–16:31 — two launch attempts (lab-s1, lab-s1b) ended at admission: no 2×A100 offer met the floors (only one near miss at $1.65/h, rented by someone else within a minute). CPU-RAM floor lowered 128→64 GiB (appliance needs 48). Offer watcher armed; no instance created, nothing billed.
- 2026-09-05 — approved in chat; launching hold-mode instance (bundle v25, max-runtime 21600 s = 6 h watchdog, --max-dph 2.25).
- 2026-09-05 — persistent-mode recipe added to .claude/skills/vast-agentic-e2e (§4b): hold command after readiness, SSH-run e2e phases with SEOCHO_E2E_PHASE/SEOCHO_PROMPT_PROFILE/SEOCHO_GATE_B0_CRITERION, per-run scp+checksum collection, watchdog deadline = deadline_hours, end-of-day destroy+verify. Budget proposed above; flip `approved: yes` to launch.

## Result (Claude)

## Decision needed (Claude -> user)
Re-approve for the queue-mode session (proposed budget_usd: 10, deadline_hours: 4): PE-0 + PE-1 gate passes + serving sweep V-0/V-3/V-2/V-1/V-6 run unattended from the uploaded queue; hold afterwards only if time remains.
