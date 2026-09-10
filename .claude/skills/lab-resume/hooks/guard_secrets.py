#!/usr/bin/env python3
"""Claude Code Bash hook: keep credential material out of the conversation.

Why: on 2026-09-10 an auto-mode session was locked out (every Bash call
blocked) after credential setup and raw provider JSON had passed through the
chat. The lockout was triggered by the *conversation content*, not by the
actions. This hook fails closed on the command shapes that produced that
content, and warns when a command's output carries key-like material.

Wire-up (``.claude/settings.local.json``)::

    "hooks": {
      "PreToolUse":  [{"matcher": "Bash", "hooks": [{"type": "command",
          "command": "python3 .claude/skills/lab-resume/hooks/guard_secrets.py pre"}]}],
      "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
          "command": "python3 .claude/skills/lab-resume/hooks/guard_secrets.py post"}]}]
    }

Exit codes: 0 allow / silent, 2 block (pre) or warn (post; the tool already ran,
stderr is fed back to the model). ``--self-test`` runs the built-in examples.
"""
from __future__ import annotations

import json
import re
import sys

# Command shapes that put credential material into the transcript.
BLOCK_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "credential setup belongs in a separate throwaway session (aws configure / vastai set api-key / huggingface-cli login)",
        re.compile(r"\b(aws\s+configure\b(?!\s+list\b)|vastai\s+set\s+api-key|huggingface-cli\s+login|hf\s+auth\s+login)"),
    ),
    (
        "do not print credential files (~/.aws/credentials, .env, appliance secret files) into chat",
        re.compile(
            r"\b(cat|less|more|head|tail|sed|awk|grep|rg|strings|bat|python3?\s+-c\s+[\"'][^\"']*open)\b[^|;&]*"
            r"(\.aws/credentials|(^|[\s/])\.env(\.(?!example\b)[a-z]+)?(\s|$)|appliance[-_]secret|credentials\.json|\.netrc|id_(rsa|ed25519)(\s|$))"
        ),
    ),
    (
        "do not echo secret environment variables; check presence with `[ -n \"$VAR\" ]` instead",
        re.compile(r"(echo|printf|printenv)\b[^|;&]*\$\{?[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)[A-Z0-9_]*"),
    ),
    (
        "bare `env`/`printenv`/`set` dumps every variable; grep for the one name you need (`printenv | grep -c NAME`)",
        re.compile(r"(^|[;&|]\s*)((env|printenv)\s*(;|&&|\|\||$|\|\s*(head|tail|less|more|cat|sort|tee|wc)\b)|(set|export)\s*(;|&&|\|\||$|\|))"),
    ),
    (
        "raw Vast instance JSON carries the jupyter token and SSH key; pipe `vastai show instances --raw` through a field filter (id/label/status only)",
        re.compile(r"vastai\s+show\s+instances?\b[^|;&]*--raw\s*($|[;&])"),
    ),
    (
        "the command contains a key-shaped literal; never paste keys into commands",
        re.compile(
            r"(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\bhf_[A-Za-z0-9]{30,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[baprs]-[A-Za-z0-9-]{10,})"
        ),
    ),
]

# Output shapes that mean a secret just entered the transcript.
WARN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_access_key assignment", re.compile(r"aws_secret_access_key\s*[=:]\s*\S{20,}", re.I)),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Vast instance jupyter token", re.compile(r"\"jupyter_token\"\s*:\s*\"[^\"]{8,}\"")),
    ("Vast SSH key material", re.compile(r"\"ssh_key\"\s*:\s*\"ssh-(rsa|ed25519) ")),
    ("OpenAI/HF/GitHub style token", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")),
]


def check_pre(command: str) -> str | None:
    for reason, rx in BLOCK_RULES:
        if rx.search(command):
            return reason
    return None


def check_post(output: str) -> list[str]:
    hits = []
    for name, rx in WARN_PATTERNS:
        if rx.search(output):
            hits.append(name)
    return hits


def _tool_output(payload: dict) -> str:
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        parts = [str(resp.get(k, "")) for k in ("stdout", "stderr", "output", "content")]
        return "\n".join(parts)
    return str(resp or "")


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    mode = argv[1] if len(argv) > 1 else "pre"
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") not in (None, "Bash"):
        return 0
    if mode == "pre":
        command = str(payload.get("tool_input", {}).get("command", ""))
        reason = check_pre(command)
        if reason:
            sys.stderr.write(f"guard_secrets: blocked — {reason}\n")
            return 2
        return 0
    hits = check_post(_tool_output(payload))
    if hits:
        sys.stderr.write(
            "guard_secrets: WARNING — the last command's output contained "
            + ", ".join(hits)
            + ". Do not repeat or quote it; treat the material as exposed and rotate it "
            "(same-session exposure is what caused the 2026-09-10 lockout).\n"
        )
        return 2
    return 0


def self_test() -> int:
    blocked = [
        "aws configure --profile seocho-traces",
        "vastai set api-key XXXX",
        "cat ~/.aws/credentials",
        "sed -n 1,20p .env",
        "grep VAST .env.local",
        "echo $AWS_SECRET_ACCESS_KEY",
        "printenv | head",
        "env",
        "timeout 60 vastai show instances --raw",
        "vastai show instance 123 --raw; echo done",
        "curl -H 'x: AKIAABCDEFGHIJKLMNOP' https://x",
    ]
    allowed = [
        "aws configure list --profile seocho-traces",
        "aws sts get-caller-identity --profile seocho-traces",
        "aws s3 ls s3://seocho-agentic-os-traces/ --profile seocho-traces",
        "[ -n \"$VAST_API_KEY\" ] && echo set",
        "printenv | grep -c AWS_PROFILE",
        "timeout 60 vastai show instances --raw | python3 -c 'import json,sys; print([i[\"id\"] for i in json.load(sys.stdin)])'",
        "cat .env.example",
        "cat docs/agentic-os/TRACE_SHARING.md",
        "grep -n environment docs/RUNTIME_DEPLOYMENT.md",
        "python3 -m pytest tests/seocho/test_agentic_os_appliance_shell.py -q",
        "systemd-run --user --unit seocho-vast-lab-s11 bash launch.sh",
        "export PYTHONPATH=src; python3 -c 'print(1)'",
        "set -euo pipefail; ls",
    ]
    warn_outputs = [
        '{"id": 1, "jupyter_token": "abcdefghijklmnop", "label": "x"}',
        "aws_access_key_id = AKIAABCDEFGHIJKLMNOP",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ]
    quiet_outputs = [
        "account 000000000000 arn arn:aws:iam::000000000000:user/seocho-traces-uploader",
        '{"id": 1, "label": "seocho-agentic-os-lab-s11", "actual_status": "running"}',
        "23 passed in 0.21s",
    ]
    fails = 0
    for c in blocked:
        if check_pre(c) is None:
            fails += 1
            print("FAIL should block:", c)
    for c in allowed:
        r = check_pre(c)
        if r is not None:
            fails += 1
            print("FAIL should allow:", c, "->", r)
    for o in warn_outputs:
        if not check_post(o):
            fails += 1
            print("FAIL should warn:", o[:40])
    for o in quiet_outputs:
        if check_post(o):
            fails += 1
            print("FAIL should be quiet:", o[:40])
    total = len(blocked) + len(allowed) + len(warn_outputs) + len(quiet_outputs)
    print(f"guard_secrets self-test: {total - fails}/{total} ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
