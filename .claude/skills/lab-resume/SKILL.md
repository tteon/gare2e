---
name: lab-resume
description: Resume SEOCHO agentic-os lab work at the start of a Claude Code session — restore the handoff memo, lab board, approved requests and beads in a fixed order, re-check the environment (our Vast instances by label filter only, AWS trace profile, secret-guard hook self-test), enforce the conversation-hygiene rules that avoid the auto-mode classifier lockout, then continue the recorded execution order. Use when the user says "이어서", "resume", "재개", "/lab-resume", starts a new session after a handoff, or asks where the lab work stopped.
---

# Lab resume (session start procedure)

Chat history does not survive a session; the files below do. Read them in this
order, then act. Never start from memory of a previous chat.

## 1. Restore state (read-only, ~2 min)

1. Handoff memo: newest `seocho-handoff-*.md` in the Claude memory dir
   (`~/.claude/projects/-home-hadry-lab-seocho-room-seocho/memory/`), then
   `vast-agentic-e2e-state.md`. These say what was running, what is unverified,
   and the next steps in order.
2. `.agents/lab/BOARD.md`, then every `requests/*.md` with `state` not
   `done|dropped`; `LEDGER.md` tail for spend and the last live-instance check.
3. `bd list --status in_progress` and `bd ready`; claim the bead you continue.
4. Working tree: `git status --short --branch`. This tree is shared with a
   Codex agent (branch `codex/…`, about 140 tracked files modified by it, plus
   its own untracked scripts under `scripts/benchmarks/`). Do not `git add -A`,
   `git stash`, or `git checkout` here; commit lane work through a temporary
   worktree of `agentic-os-e2e` (see §4). Appliance bundles are built from this
   shared tree, so record the bundle sha and note foreign files if they matter.
5. The previous session's scratchpad (launchers, watch scripts) is under
   `/tmp/claude-1000/-home-hadry-lab-seocho-room-seocho/<session-id>/scratchpad/`;
   copy what you reuse into the current scratchpad.

## 2. Re-check the environment (read-only)

Run each with a `timeout`; print filtered fields only.

```bash
# our instances only — the raw JSON carries the jupyter token, never print it
timeout 60 vastai show instances --raw | python3 -c '
import json,sys; d=json.load(sys.stdin)
print([(i["id"], i.get("label"), i.get("actual_status"), round(i.get("dph_total",0),2)) for i in d])'
# trace-sharing identity (this user has no ListBucket; "aws s3 ls" is expected to fail)
timeout 60 aws sts get-caller-identity --profile seocho-traces --output json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])'
# hook self-test
python3 .claude/skills/lab-resume/hooks/guard_secrets.py --self-test
systemctl --user list-units 'seocho-vast*' --all --no-pager
```

Labels: ours start with `seocho-agentic-os-`; a `gare2e-…` instance is the
teammate's on the shared key — report it in LEDGER.md, never destroy it.

## 3. Conversation hygiene (why this skill exists)

On 2026-09-10 the auto-mode safety classifier blocked every Bash call for the
rest of the session after credential setup and raw provider JSON had passed
through the chat. Rules:

- Keys, tokens, passwords, raw model output: never in chat, commits, lab files,
  or receipts. `*.private.*` files are read with `grep -c` / field filters.
- Credential configuration (`aws configure`, `vastai set api-key`, HF login)
  happens in a separate throwaway session, or the user runs it with `!`.
- Prefer the default permission mode with the allowlist in
  `.claude/settings.local.json` over auto mode for lab days. The
  `hooks/guard_secrets.py` hook (PreToolUse/PostToolUse on Bash) fails closed
  on the command shapes that caused the lockout; wiring is in its docstring.
- Editing `.claude/settings*.json` is itself a sensitive action: do it through
  the `update-config` skill, one small change at a time.
- If a call is blocked by the classifier anyway: write the handoff memo first
  (memory dir), then tell the user to open a new session.

## 4. Commit lane work (gare2e, private)

Lane code lives on branch `agentic-os-e2e`, pushed to remote `gare2e`
(`tteon/gare2e`), never to public `origin` without the user's say.

```bash
WT=$SCRATCH/gare2e-wt; git worktree add -q "$WT" agentic-os-e2e
for f in <changed files>; do mkdir -p "$WT/$(dirname "$f")"; cp "$f" "$WT/$f"; done
(cd "$WT" && PYTHONPATH=src:. timeout 600 python3 -m pytest <tests> -q | tail -1 \
  && git add <paths> && git -c user.name="yitae jeong" commit -q -F - <<'MSG'
<type>(<scope>): <summary>

<why / what / tests / bundle version>
MSG
  && git push -q gare2e agentic-os-e2e)
git worktree remove --force "$WT"
```

## 5. Continue the recorded order

Execute the handoff memo's numbered steps; each step ends with a `bd update
--append-notes`, a lab request `## Log` line, and (paid runs) a `LEDGER.md`
row. Paid launches follow `vast-agentic-e2e` (ownership gate → bundle
freshness → systemd unit → teardown verified). Trace packages follow
`trace-share`. Before the session ends, or as soon as commands start being
blocked, rewrite the handoff memo with: what is running (unit, instance,
ceiling), what is unverified in the tree, and the next steps in order.
