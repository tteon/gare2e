# SEOCHO Lab — interaction zone

Reviewed experiment record shared through the repository. The user writes
intent; the agent records feasibility, authorized execution and evidence-backed
results. Start with [the experiment index](../README.md) and
[agent handoff guide](../AGENTS.md). Raw/private operator state stays local.

## Protocol

- One request = one file in `requests/NNN-slug.md` (copy `templates/request.md`).
- `BOARD.md` lists every request with its state. The acting agent keeps it current.
- States: `proposed -> feasibility -> approved -> running -> done | blocked | dropped`.
- Record user authorization in the request, including scope and any spending
  limit. Honor explicit authorization already given in the current session;
  missing template fields do not require asking again. Historical approvals
  do not authorize new paid runs, and unused budget does not expand scope.
- Every `## Log` line carries a timestamp and an evidence path (receipt,
  catalog, log). Claims without a file behind them are not written.
- In workspaces using beads, link the local issue in the request and keep it
  updated. Other readers can continue from tracked requests without that tracker.
- `LEDGER.md` is the running cost record for paid runs. `DECISIONS.md` holds
  confirmed decisions only; decisions that change repo behaviour are later
  mirrored into `docs/execplans/` or an ADR.

## Triggers

- Session live: agent watches `requests/` (Monitor) and reacts to saves.
- Otherwise: say "보드 확인" or run `/lab` and agent reads `BOARD.md`.

## Who writes what

| Section | Author |
|---|---|
| Intent, Acceptance (edit), Constraints, Approval | user |
| Feasibility, Plan, Log, Result, Decision needed | agent |
