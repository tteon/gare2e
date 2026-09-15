# Experiment agent guide

Use this guide for Graph Agentic RAG benchmark and observability work. The root
[AGENTS.md](../../AGENTS.md) remains the repository execution contract.

## Restore from the repository

1. Read [README](README.md), the latest section of [HANDOFF](HANDOFF.md), and
   [BOARD](lab/BOARD.md)/[LEDGER](lab/LEDGER.md). Older snapshots are historical.
2. Read the relevant [request](lab/requests/) Result and approval scope, then
   [HARNESS](HARNESS.md) and [metric definitions](OBSERVABILITY_FACTORS.md).
3. Inspect reports, source commits, manifests, and actual lifecycle receipts
   before proposing a new run. Separate measured, implemented-only, and proposed.
4. Local `.seocho/memory/`, `.agents/`, beads, and credentials may supplement the
   tracked record if present. Another operator must not need those files just
   to read results or regenerate the published charts.

## Preserve the experiment

- Keep output tok/s/GPU primary, with task/s and input/output tokens per task.
  Distinguish changed generation demand from faster generation.
- Preserve the single-agent task topology and NVLink-pair constraint unless the
  user explicitly changes that scope. Application K is concurrent tasks.
- Use a common throughput window and count physical GPUs once. LLM stage times
  overlap across requests and are not exclusive GPU time. Label each denominator.
- Use exact trace/parent IDs for server joins. Unknown telemetry stays unknown;
  valid empty results are not errors, and final termination is not judged accuracy.
- State the incremental hypothesis and closest prior condition before paid work.
  Preserve original inputs/results and identify intentional replications.
- Honor existing explicit user authorization; record its scope and remaining
  budget. Reading results, rendering charts, or merging a branch does not request
  a new paid experiment. Historical approvals are not a reusable spending pool.
- Use the lifecycle runner for paid execution: admission, source/image checks,
  readiness, graph/trace gates, bounded queue, verified collection, teardown and
  verified absence. Never operate on another person's instance or infer ownership
  solely from a shared credential. No replacement while an owned instance may exist.
- Keep credentials, raw prompts/answers/logs, provider payloads and connection
  addresses out of Git and reports. Share only reviewed summaries/manifests.

## Handoff and landing

Work in an isolated checkout/worktree and stage explicit paths. Confirm the
remote URL: this experiment repository is `tteon/gare2e`. Published material is
on its `main`; development may use `agentic-os-e2e` or a task branch. Do not infer
permission to push to the separate public SEOCHO repository or change visibility.
The user's current landing instruction takes precedence over historical branch
names in earlier HANDOFF sections.

At completion, update the request Result, BOARD, LEDGER when spend changes,
HANDOFF, report links and validation gaps. Preserve source bundle and artifact
hashes. A partial bulk archive stays partial even when essential receipts passed.
Read-only chart reproduction requires no cloud credentials; a live rerun needs
the private inputs and resource authorization listed in HARNESS.
