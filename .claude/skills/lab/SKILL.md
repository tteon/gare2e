---
name: lab
description: Read and act on the private SEOCHO lab board in .agents/lab (async user↔Claude interaction zone) — answer feasibility for proposed requests, run approved experiments, log evidence, update BOARD/LEDGER/DECISIONS. Use when the user says "보드 확인", "/lab", edits a request file, or asks about lab request status.
---

# SEOCHO lab board

`.agents/lab/` is the private, untracked interaction zone (protocol in its
`README.md`). Chat is for discussion; the files are the record. Never move
lab content into tracked paths except as a distilled execplan/ADR entry.

## On every invocation

1. Read `BOARD.md`, then every `requests/*.md` whose `state` is not
   `done|dropped`. Parse the front matter and the `## Approval` block.
2. For each request, act by state:
   - `proposed` → write `## Feasibility` (verdict, basis with file evidence,
     alternatives, cost/time), set `state: feasibility`, fill
     `## Decision needed` with the minimal question.
   - `feasibility` → nothing unless the user changed Intent/Constraints or
     Approval; then re-evaluate or proceed.
   - `approved: yes` present **and** `budget_usd` > 0 (or the action is
     free) → set `state: running`, write `## Plan`, execute, append `## Log`
     lines (`YYYY-MM-DD HH:MM — what — evidence: path`), then `## Result`,
     set `state: done` or `blocked` with the blocker named.
   - Paid/destructive/outward actions without `approved: yes` → stop at
     feasibility. Do not infer approval from chat; the file is the gate.
3. Rewrite the `BOARD.md` table and its `Updated:` line. Paid runs also add
   a `LEDGER.md` row (instance id, $/h, billed time, outcome). Confirmed
   decisions go to `DECISIONS.md` with the date.
4. Reply in chat with a short delta: which requests changed state and what
   the user must decide. Do not paste whole files.

## Executing experiments

- Vast appliance runs follow the `vast-agentic-e2e` skill (systemd unit,
  ownership gate first, gate-only before matrix, teardown verified).
- Evidence lives under gitignored `.seocho/benchmarks/...`; link paths, never
  paste model output or secrets into lab files.
- One request should be decidable by one experiment. If it is not, propose a
  split in `## Decision needed` instead of expanding scope.

## Watching (trigger C)

While a session is live, keep a Monitor polling `requests/*.md` mtimes and
re-run step 2 for the changed file only. Without a live session the user
invokes `/lab` or says "보드 확인".
