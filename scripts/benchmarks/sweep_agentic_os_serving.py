#!/usr/bin/env python3
"""Run a declarative vLLM serving-configuration sweep on the persistent lab appliance.

For each variant in the YAML: restart vLLM with the variant's arguments and
environment (via ``run_lab_pass.sh`` on the appliance), run the frozen cells
under the workload wrapper, pull the pass artifacts, join telemetry to attempt
windows, decompose every attempt, and finally write one paired comparison
against the ``baseline`` variant.  The driver never edits the appliance; the
variant list is the only thing an engineer changes.

Resumable: a variant whose local pass directory already holds a
``pass-receipt.json`` is not re-run.  ``--dry-run`` prints the plan.
"""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO = Path(__file__).resolve().parents[2]
SCHEMA = "seocho.agentic_os.serving_sweep.v1"


def load_spec(path: Path) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, Mapping) or spec.get("schema_version") != SCHEMA:
        raise ValueError(f"unsupported sweep spec: {path}")
    ids = [v["id"] for v in spec["variants"]]
    if len(set(ids)) != len(ids):
        raise ValueError("variant ids must be unique")
    if "baseline" not in ids:
        raise ValueError("a variant with id 'baseline' is required for paired comparison")
    for v in spec["variants"]:
        for arg in v.get("vllm_args", []):
            if not isinstance(arg, str):
                raise ValueError(f"vllm_args must be strings: {v['id']}")
        for key in v.get("env", {}) or {}:
            if not key.replace("_", "").isalnum() or key.startswith("SEOCHO_"):
                raise ValueError(f"variant env key not allowed: {key}")
    return dict(spec)


def pass_env(spec: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, str]:
    workload = spec.get("workload", {})
    env = {
        "PASS_ID": f"{spec['name']}-{variant['id']}",
        "SEOCHO_E2E_PHASE": str(workload.get("phase", "gate")),
        "SEOCHO_PROMPT_PROFILE": str(workload.get("prompt_profile", "baseline")),
        "SEOCHO_GATE_B0_CRITERION": str(workload.get("b0_criterion", "validated")),
        "SEOCHO_MAX_OUTPUT_TOKENS": str(workload.get("max_output_tokens", 2048)),
        "SEOCHO_TOKEN_BUDGET": str(workload.get("token_budget", 16384)),
        "SEOCHO_REASONING_MODES": str(workload.get("reasoning_modes", "false true")),
        "VLLM_MODEL": str(spec["model"]),
        "VLLM_MODEL_REVISION": str(spec["model_revision"]),
    }
    args = list(variant.get("vllm_args", []))
    # A variant with no argument change still restarts vLLM so every variant
    # starts from an identical cold server state (empty prefix cache, no
    # captured graphs from a previous variant).
    env["VLLM_EXTRA_ARGS_OVERRIDE"] = " ".join(shlex.quote(a) for a in args) if args else "--gpu-memory-utilization 0.92"
    for key, value in (variant.get("env") or {}).items():
        env[str(key)] = str(value)
    return env


def remote_command(env: Mapping[str, str]) -> str:
    assignments = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    return f"env {assignments} bash /workspace/seocho/docker/agentic-os-appliance/run_lab_pass.sh"


def _ssh_parts(ssh_url: str) -> tuple[str, str]:
    body = ssh_url.removeprefix("ssh://")
    host, port = body.rsplit(":", 1)
    return host, port


def run_variant(spec: Mapping[str, Any], variant: Mapping[str, Any], *, ssh_url: str, local_root: Path, timeout_s: int) -> Path:
    env = pass_env(spec, variant)
    local = local_root / env["PASS_ID"]
    if (local / "pass-receipt.json").exists():
        return local
    host, port = _ssh_parts(ssh_url)
    ssh = ["ssh", "-p", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", host]
    subprocess.run([*ssh, remote_command(env)], check=False, timeout=timeout_s, capture_output=True, text=True)
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["scp", "-P", port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-r", "-q",
         f"{host}:/artifacts/run-{env['PASS_ID']}", str(local)],
        check=True, timeout=1800,
    )
    analyse_pass(local)
    return local


def analyse_pass(local: Path) -> None:
    roots = sorted((local / "e2e").glob("gate-*")) + sorted((local / "e2e").glob("raw-*"))
    workload = local / "workload"
    if not roots or not (workload / "vllm-metrics.jsonl").exists():
        return
    join = local / "e2e" / "case-telemetry.json"
    subprocess.run([sys.executable, str(REPO / "scripts/benchmarks/join_agentic_os_gpu_samples.py"), *map(str, roots),
                    "--vllm-samples", str(workload / "vllm-metrics.jsonl"), "--gpu-samples", str(workload / "gpu.csv"),
                    "--max-gap-s", "2.5", "--output", str(join)], check=False, capture_output=True)
    subprocess.run([sys.executable, str(REPO / "scripts/benchmarks/analyze_agentic_os_attempt_windows.py"), *map(str, roots),
                    "--telemetry-join", str(join), "--gpu-samples", str(workload / "gpu.csv"),
                    "--host-samples", str(workload / "host-process.csv"), "--vllm-samples", str(workload / "vllm-metrics.jsonl"),
                    "--output-dir", str(local / "analysis"), "--title", f"Serving sweep — {local.name}"], check=False, capture_output=True)


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def summarise(spec: Mapping[str, Any], local_root: Path) -> list[dict[str, Any]]:
    """One row per variant × cell with metrics and deltas vs the baseline cell."""
    metrics = list(spec.get("metrics_of_record", []))
    guard = list(spec.get("funnel_guard", []))
    per_variant: dict[str, dict[str, dict[str, Any]]] = {}
    for variant in spec["variants"]:
        pass_dir = local_root / f"{spec['name']}-{variant['id']}"
        table = pass_dir / "analysis" / "attempt-decomposition.csv"
        if not table.exists():
            continue
        with table.open(encoding="utf-8", newline="") as handle:
            per_variant[variant["id"]] = {row["cell"]: row for row in csv.DictReader(handle)}
    base = per_variant.get("baseline", {})
    rows: list[dict[str, Any]] = []
    for vid, cells in per_variant.items():
        for cell, row in cells.items():
            ref = base.get(cell)
            out: dict[str, Any] = {"variant": vid, "cell": cell}
            comparable = ref is not None and all(row.get(g) == ref.get(g) for g in guard)
            out["funnel_matches_baseline"] = comparable
            for m in metrics:
                value = _num(row.get(m))
                out[m] = value
                out[f"{m}_delta_vs_baseline"] = (
                    round(value - _num(ref.get(m)), 4) if comparable and value is not None and _num(ref.get(m)) is not None and vid != "baseline" else None
                )
            rows.append(out)
    return rows


def write_summary(rows: list[dict[str, Any]], spec: Mapping[str, Any], out_dir: Path) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with (out_dir / "serving-sweep-summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    metrics = list(spec.get("metrics_of_record", []))
    lines = ["| variant | cells | comparable | " + " | ".join(f"median Δ {m}" for m in metrics) + " |", "|---|---|---|" + "---|" * len(metrics)]
    for vid in sorted({r["variant"] for r in rows}):
        vrows = [r for r in rows if r["variant"] == vid]
        comparable = [r for r in vrows if r["funnel_matches_baseline"]]
        cells = []
        for m in metrics:
            deltas = [r[f"{m}_delta_vs_baseline"] for r in comparable if r.get(f"{m}_delta_vs_baseline") is not None]
            cells.append(f"{statistics.median(deltas):+.3f}" if deltas else "")
        lines.append(f"| {vid} | {len(vrows)} | {len(comparable)} | " + " | ".join(cells) + " |")
    (out_dir / "serving-sweep-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--ssh-url", help="ssh://root@host:port of the live lab appliance (omit with --summarise-only)")
    parser.add_argument("--local-root", required=True, type=Path)
    parser.add_argument("--only", action="append", default=[], help="variant id(s) to run; default all in order")
    parser.add_argument("--pass-timeout-s", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    spec = load_spec(args.spec)
    variants = [v for v in spec["variants"] if not args.only or v["id"] in args.only]
    if args.dry_run:
        for v in variants:
            print(json.dumps({"variant": v["id"], "remote": remote_command(pass_env(spec, v))}))
        return 0
    if not args.summarise_only:
        if not args.ssh_url:
            parser.error("--ssh-url is required unless --summarise-only")
        for v in variants:
            local = run_variant(spec, v, ssh_url=args.ssh_url, local_root=args.local_root, timeout_s=args.pass_timeout_s)
            receipt = local / "pass-receipt.json"
            print(json.dumps({"variant": v["id"], "pass_dir": str(local), "exit_code": json.loads(receipt.read_text()).get("exit_code") if receipt.exists() else None}))
    rows = summarise(spec, args.local_root)
    args.local_root.mkdir(parents=True, exist_ok=True)
    write_summary(rows, spec, args.local_root)
    print(json.dumps({"variants_summarised": len({r['variant'] for r in rows}), "rows": len(rows), "summary": str(args.local_root / "serving-sweep-summary.md")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
