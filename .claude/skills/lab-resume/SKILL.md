---
name: lab-resume
description: Resume Graph Agentic RAG lab work from the tracked handoff, board, reports and harness. Use when continuing these experiments or checking their status; no cloud access is needed to inspect existing results.
---

# Lab resume

Restore experiment state from this checkout. A previous operator's home
folder, chat history, scratch directory or local tracker is not required.

## Read the durable record

1. Read the root `AGENTS.md`, then the
   [experiment agent guide](../../../docs/agentic-os/AGENTS.md).
2. Read the latest section of [HANDOFF](../../../docs/agentic-os/HANDOFF.md),
   [BOARD](../../../docs/agentic-os/lab/BOARD.md), the relevant request Result
   and approval scope, and [LEDGER](../../../docs/agentic-os/lab/LEDGER.md).
   Older preparation/live snapshots are historical, not current status.
3. Follow [HARNESS](../../../docs/agentic-os/HARNESS.md) to view/reproduce charts
   or identify the required private inputs for a live run. Inspect the existing
   report/source/manifest before repeating a completed gate.
4. Inspect `git status --short --branch` and remote URLs. Use an isolated
   worktree for shared-checkout edits. Confirm the target is `tteon/gare2e`;
   `main` carries published results, and task branches carry new changes.
5. If local `.seocho/memory/`, `.agents/lab/`, or beads exist, reconcile their
   notes with the tracked record and lifecycle evidence. They are optional
   local aids. Do not copy their state or credentials into Git.

## Check live resources only when relevant

For a running or newly authorized live experiment, read its private lifecycle
receipt and use the bounded provider checks in the lifecycle runner. Ownership
must match the recorded instance; a shared key is not ownership. Filter provider
JSON to IDs, labels, status and cost fields before displaying anything. Preserve
colleague resources and never create a replacement while an owned instance may
still exist. Recorded completed runs require no provider login just to read them.

Do not reset accumulated spend. Honor authorization already given in the current
session and record its scope; historical approval is not permission to spend
again. A status, chart or merge request alone does not launch paid resources.

## Preserve evidence and finish the handoff

Use the repository lifecycle runner for paid work: admission → verified staging
→ service readiness → graph/trace gate → bounded queue → checksum collection →
verified teardown. Preserve partial artifacts and report gaps without substituting
mock timings or matching server spans by timestamps.

Keep keys, raw model content, provider payloads and connection addresses in
private files. The optional local secret guard can be checked without credentials:

```bash
python3 .claude/skills/lab-resume/hooks/guard_secrets.py --self-test
```

Before ending, update the request Result, BOARD, relevant LEDGER entries and
HANDOFF with measured/implemented/proposed status, source and artifact hashes,
remaining scope and exact validation gaps. Use explicit paths when committing;
follow the user's current target branch instruction instead of historical
branch names in older handoffs.
