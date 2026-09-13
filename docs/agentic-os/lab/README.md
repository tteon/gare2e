# SEOCHO Lab — interaction zone

Private (untracked) async collaboration between the user and Claude Code.
The user writes intent; Claude answers feasibility, runs approved experiments,
and writes evidence-backed results. Chat is for discussion; this directory is
the record.

## Protocol

- One request = one file in `requests/NNN-slug.md` (copy `templates/request.md`).
- `BOARD.md` lists every request with its state. Claude keeps it current.
- States: `proposed -> feasibility -> approved -> running -> done | blocked | dropped`.
- Paid, destructive, or outward-facing actions run **only** when the request
  file contains `approved: yes` and a `budget_usd:` line in `## Approval`.
  Without them Claude stops at `feasibility` and asks in `## Decision needed`.
- Every `## Log` line carries a timestamp and an evidence path (receipt,
  catalog, log). Claims without a file behind them are not written.
- Every request that produces code or an experiment gets a beads issue (`bd create`), referenced in its front matter as `bead:`; Claude appends progress there with `bd update --append-notes` and closes it when landed.
- `LEDGER.md` is the running cost record for paid runs. `DECISIONS.md` holds
  confirmed decisions only; decisions that change repo behaviour are later
  mirrored into `docs/execplans/` or an ADR.

## Triggers

- Session live: Claude watches `requests/` (Monitor) and reacts to saves.
- Otherwise: say "보드 확인" or run `/lab` and Claude reads `BOARD.md`.

## Who writes what

| Section | Author |
|---|---|
| Intent, Acceptance (edit), Constraints, Approval | user |
| Feasibility, Plan, Log, Result, Decision needed | Claude |
