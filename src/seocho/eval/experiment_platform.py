"""Immutable, attributed experiment results and content-free trace inspection."""
from __future__ import annotations

import csv
import fcntl
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .agentic_os_graph_system import coverage

SCHEMA = "gare2e.experiment.v1"
CONTRACT_FIELDS = ("dataset_revision", "model_revision", "hardware", "gpu_count",
                   "db_build", "protocol", "measurement_policy")
DEFAULT_METRICS = [
    {"id": key, "label": label, "unit": unit, "view": view, "direction": direction, "path": path}
    for key, label, unit, view, direction, path in (
        ("output_tok_s_gpu", "Output throughput / GPU", "tok/s/GPU", "Runtime", "higher", "output_tok_s_gpu"),
        ("task_s", "Task completion rate", "task/s", "Runtime", "higher", "throughput.task_completions_per_s"),
        ("queue_p95", "LLM queue p95", "s", "Runtime", "lower", "runtime.queue_s.p95"),
        ("decode_p95", "Request decode p95", "s", "Runtime", "lower", "runtime.decode_s.p95"),
        ("input_tokens", "Input tokens / task", "tokens", "Agent", "descriptive", "agent.input_tokens.mean"),
        ("output_tokens", "Output tokens / task", "tokens", "Agent", "descriptive", "agent.output_tokens.mean"),
        ("turns", "LLM calls / task", "calls", "Agent", "descriptive", "agent.llm_calls.mean"),
        ("repair", "Repairs / task", "calls", "Agent", "lower", "agent.repair_count.mean"),
        ("wall_p95", "Contained task wall p95", "ms", "Agent", "lower", "agent.wall_ms.p95"),
        ("tool_p95_max", "Largest tool-family p95", "ms", "Tool", "lower", "platform_summary.tool_p95_max"),
        ("tool_calls", "Contained tool calls", "calls", "Tool", "descriptive", "platform_summary.tool_calls"),
    )]


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
        raise ValueError("ID must be 1..96 alphanumeric, dot, underscore or hyphen characters")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def diagnostic_artifacts(source: Path, manifest: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    """Register explicitly reviewed, checksummed profiler files; never synthesize Nsight data."""
    records, files = [], {}
    for item in manifest or []:
        aid = identifier(item["id"])
        if aid in files:
            raise ValueError("Duplicate diagnostic artifact ID")
        if item.get("content_reviewed") is not True:
            raise ValueError("Profiler artifacts require content_reviewed=true for shared downloads")
        if item.get("type") not in {"nsight_sqlite", "nsight_report"}:
            raise ValueError("Unsupported diagnostic format")
        source_path = (source / item["path"]).resolve()
        if source.resolve() not in source_path.parents or not source_path.is_file():
            raise ValueError("Diagnostic artifact must be a file within the source directory")
        checksum = file_sha256(source_path)
        if checksum != item.get("sha256"):
            raise ValueError("Diagnostic checksum mismatch")
        if item.get("scope") not in {"separate_diagnostic", "throughput_capture"}:
            raise ValueError("Diagnostic scope is required")
        if not item.get("profiler_version") or not item.get("capture_window"):
            raise ValueError("Profiler version and capture window are required")
        window = item["capture_window"]
        start, end = _number(window.get("start_s")), _number(window.get("end_s"))
        if start is None or end is None or end < start or window.get("clock_domain") not in {"profiler_relative", "unix", "monotonic", "unknown"}:
            raise ValueError("Invalid profiler capture window")
        table_count = None
        if item["type"] == "nsight_sqlite":
            with source_path.open("rb") as handle:
                if handle.read(16) != b"SQLite format 3\x00":
                    raise ValueError("Nsight SQL download must be an exported SQLite database")
            # Read schema only, never execute contributor SQL or read StringIds contents.
            with sqlite3.connect(source_path.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
                connection.execute("PRAGMA trusted_schema=OFF")
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            if not any(name.startswith("CUPTI_ACTIVITY_KIND_") or name in {"NVTX_EVENTS", "OSRT_API", "SCHED_EVENTS"} for name in tables):
                raise ValueError("No recognized Nsight Systems diagnostic tables; use a reviewed version-specific adapter")
            table_count = len(tables)
        records.append({"id": aid, "condition": _label(item["condition"]), "type": item["type"],
                        "sha256": checksum, "bytes": source_path.stat().st_size,
                        "profiler_version": _label(item["profiler_version"]), "scope": item["scope"],
                        "capture_window": {"start_s": start, "end_s": end, "clock_domain": window["clock_domain"]},
                        "content_reviewed": True, "table_count": table_count})
        files[aid] = source_path
    return records, files


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _label(value: Any, default: str = "unknown") -> str:
    text = str(value or default)
    return text if re.fullmatch(r"[A-Za-z0-9_ .:/+-]{1,160}", text) else "id-" + _digest(text)[:16]


def _get(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def metric_definitions(extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    definitions = list(DEFAULT_METRICS)
    seen = {d["id"] for d in definitions}
    for item in extra or []:
        key = identifier(item["id"])
        if key in seen or item.get("view") not in {"Runtime", "Agent", "Tool"}:
            raise ValueError("Duplicate metric ID or invalid view")
        if item.get("direction") not in {"higher", "lower", "descriptive"}:
            raise ValueError("Metric direction is required")
        if not re.fullmatch(r"[A-Za-z0-9_.]+", item.get("path", "")):
            raise ValueError("Metric path must be a dotted numeric source path")
        definitions.append({k: str(item[k])[:160] for k in ("id", "label", "unit", "view", "direction", "path")})
        seen.add(key)
    return definitions


def _csv(directory: Path, name: str) -> list[dict[str, Any]] | None:
    for suffix in (".csv.gz", ".csv"):
        path = directory / (name + suffix)
        if path.exists():
            with (gzip.open(path, "rt") if suffix.endswith("gz") else path.open()) as handle:
                return list(csv.DictReader(handle))
    return None


def _truth(value: Any) -> bool:
    return value is True or value == "True" or value == "true"


def _trace_id(value: Any, length: int) -> str | None:
    text = str(value or "")
    return text if re.fullmatch(rf"[0-9a-f]{{{length}}}", text) else None


def task_result(task: dict[str, Any], requests: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Export only bounded labels and numeric observations, never arbitrary args."""
    begun = _number(task.get("started_unix_s"))
    task_id = "task-" + _digest(task.get("task_id"))[:20]
    events = []

    def event(lane: str, name: str, start: Any, duration_ms: Any, evidence: dict[str, Any]) -> None:
        stamp, duration = _number(start), _number(duration_ms)
        if begun is None or stamp is None or duration is None or duration < 0:
            return
        events.append({"lane": lane, "name": name, "start_us": (stamp - begun) * 1e6,
                       "duration_us": duration * 1000, "evidence": evidence})

    event("Agent", "task", begun, task.get("wall_ms"), {})
    for index, request in enumerate(requests, 1):
        evidence = {key: _number(request.get(key)) for key in (
            "client_input_tokens", "client_output_tokens", "client_cached_tokens", "queue_s", "prefill_s", "decode_s", "ttft_s")}
        evidence["server_exact_join"] = _truth(request.get("server_span_present"))
        evidence["trace_id"] = _trace_id(request.get("trace_id"), 32)
        evidence["client_span_id"] = _trace_id(request.get("client_span_id"), 16)
        start = _number(request.get("request_started_unix_s"))
        latency = _number(request.get("client_latency_s"))
        event("LLM client", f"LLM {index}", start, latency * 1000 if latency is not None else None, evidence)
        server_start, server_end = (_number(request.get(k)) for k in ("server_started_unix_s", "server_finished_unix_s"))
        if evidence["server_exact_join"] and server_start is not None and server_end is not None:
            event("LLM server", f"request {index}", server_start, (server_end - server_start) * 1000, evidence)
    for index, tool in enumerate(tools, 1):
        offset = _number(tool.get("started_elapsed_ms"))
        evidence = {key: _number(tool.get(key)) for key in ("rows", "result_bytes", "result_tokens", "connection_pool_wait_ms",
            "server_available_ms", "server_consumed_ms", "serialize_ms")}
        evidence["result_class"] = _label(tool.get("result_class"))
        evidence["parent_request_id"] = _trace_id(tool.get("parent_request_id"), 16)
        evidence["trace_id"] = _trace_id(tool.get("trace_id"), 32)
        event("Graph tool", f"tool {index} / {_label(tool.get('query_shape'))}",
              begun + offset / 1000 if begun is not None and offset is not None else None, tool.get("latency_ms"), evidence)
    return {"id": task_id, "case_id": _label(task.get("case_id")),
            "trace_id": _trace_id(task.get("trace_id"), 32),
            "terminal": _label(task.get("terminal")), "clock_source": _label(task.get("clock_source")),
            "contained": _truth(task.get("fully_contained")),
            "wall_ms": _number(task.get("wall_ms")), "input_tokens": _number(task.get("input_tokens")),
            "output_tokens": _number(task.get("output_tokens")), "events": sorted(events, key=lambda e: e["start_us"]),
            "limitations": ["Client/server/tool intervals overlap; not exclusive GPU time.",
                            "Stage durations do not establish stage boundaries; no fabricated prefill/decode slices.",
                            "No GPU kernels collected in this task trace. Profiler diagnostics are separate."]}


def perfetto_trace(task: dict[str, Any]) -> dict[str, Any]:
    """Chrome JSON complete events in microseconds, with non-overlapping tracks."""
    events: list[dict[str, Any]] = []
    tracks: dict[str, list[float]] = {}
    ids: dict[tuple[str, int], int] = {}
    for event in task["events"]:
        lane = event["lane"]
        ends = tracks.setdefault(lane, [])
        slot = next((i for i, end in enumerate(ends) if end <= event["start_us"]), len(ends))
        if slot == len(ends):
            ends.append(0)
            tid = len(ids) + 1
            ids[lane, slot] = tid
            events.append({"ph": "M", "name": "thread_name", "pid": 1, "tid": tid,
                           "args": {"name": f"{lane} {slot + 1}"}})
        tid = ids[lane, slot]
        ends[slot] = event["start_us"] + event["duration_us"]
        events.append({"ph": "X", "pid": 1, "tid": tid, "cat": lane, "name": event["name"],
                       "ts": event["start_us"], "dur": event["duration_us"], "args": event["evidence"]})
    return {"traceEvents": events, "displayTimeUnit": "ms",
            "metadata": {"task_id": task["id"], "time_origin": "task_relative", "limitations": task["limitations"]}}


def import_characterization(source: Path, registry: Path, *, run_id: str, workspace_id: str,
                            owner: str, metadata: dict[str, Any], extra_metrics: list[dict[str, Any]] | None = None,
                            diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for value in (run_id, workspace_id, owner):
        identifier(value)
    definitions = metric_definitions(extra_metrics)
    diagnostic_records, diagnostic_files = diagnostic_artifacts(source, diagnostics)
    source_file = source / "characterization.json"
    data = json.loads(source_file.read_text())
    tasks, requests, tools = (_csv(source, name) for name in ("tasks", "requests", "tools"))
    full_tasks = tasks is not None and requests is not None and tools is not None
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for kind, rows in (("request", requests), ("tool", tools)):
        for row in rows or []:
            groups.setdefault((kind, str(row.get("pass")), str(row.get("task_id"))), []).append(row)
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()
                     if p.name in {"characterization.json", "tasks.csv", "tasks.csv.gz", "requests.csv", "requests.csv.gz", "tools.csv", "tools.csv.gz"}}
    run: dict[str, Any] = {"schema_version": SCHEMA, "id": run_id, "workspace_id": workspace_id, "owner": owner,
        "source_commit": _label(metadata.get("source_commit")), "source_hashes": source_hashes,
        "contract": {key: metadata.get("contract", {}).get(key) for key in CONTRACT_FIELDS},
        "metric_definitions": definitions, "conditions": [], "task_coverage": "all_exported_tasks" if full_tasks else "representative_examples_only"}
    artifacts: dict[str, dict[str, Any]] = {}
    for condition in data.get("passes", []):
        name = str(condition["pass"])
        cid = "condition-" + _digest(name)[:16]
        if any(c["id"] == cid for c in run["conditions"]):
            raise ValueError("Duplicate condition in characterization")
        examples = condition.get("examples", [])
        normalized = []
        if full_tasks:
            for task in tasks or []:
                if task.get("pass") == name:
                    normalized.append(task_result(task, groups.get(("request", name, str(task.get("task_id"))), []), groups.get(("tool", name, str(task.get("task_id"))), [])))
        else:
            normalized = [task_result(e["task"], e.get("requests", []), e.get("tools", [])) for e in examples]
        normalized = list({t["id"]: t for t in normalized}.values())
        # Numeric tool distributions are reusable across implementations.
        tool_types = [{"shape": _label(t.get("shape")), "calls": _number(t.get("calls")),
                       "p95_ms": _number(_get(t, "latency_ms.p95")),
                       "mean_bytes": _number(_get(t, "result_bytes.mean"))} for t in condition.get("tools", [])]
        condition = {**condition, "platform_summary": {
            "tool_p95_max": max((t["p95_ms"] for t in tool_types if t["p95_ms"] is not None), default=None),
            "tool_calls": sum(t["calls"] for t in tool_types) if tool_types and all(t["calls"] is not None for t in tool_types) else None}}
        metrics = {d["id"]: _number(_get(condition, d["path"])) for d in definitions}
        coverage_rows = [{k: _label(r.get(k)) for k in ("view", "measurement", "status")}
                         for r in (condition.get("graph_system", {}).get("coverage") or coverage())]
        admitted = metadata.get("admission", {}).get(name, "unverified")
        if admitted not in {"passed", "failed", "unverified"}:
            raise ValueError("Admission must be passed, failed or unverified")
        entry = {"id": cid, "label": _label(name), "application_k": _number(condition.get("application_k")),
            "diagnostics": [r for r in diagnostic_records if r["condition"] == _label(name)],
            "contract": {**run["contract"], **{k: v for k, v in metadata.get("condition_contracts", {}).get(name, {}).items() if k in CONTRACT_FIELDS}},
            "metrics": metrics, "tool_types": tool_types, "coverage": coverage_rows,
            "admission": admitted, "admission_source": "contributor_manifest",
            "task_count": len(normalized), "reported_tasks": _number(condition.get("all_pass_tasks")),
            "exact_join_requests": _number(condition.get("matched_requests")),
            "requests": _number(condition.get("requests")), "cohort_tasks": _number(condition.get("cohort_tasks")),
            "window_s": _number(_get(condition, "throughput.duration_s")),
            "quality": "unscored", "repetitions": _number(metadata.get("repetitions")),
            "factors": {"serving_layout": _label(condition.get("serving_layout")),
                        "router_policy": _label(condition.get("router_policy")),
                        "configured_logical_cpus": _number(condition.get("configured_logical_cpus")),
                        "constrained_query_ratio": _number(condition.get("actual_constrained_query_ratio"))}}
        run["conditions"].append(entry)
        artifacts[cid] = {"tasks": normalized}
    if not run["conditions"]:
        raise ValueError("No conditions found")
    if any(r["condition"] not in {c["label"] for c in run["conditions"]} for r in diagnostic_records):
        raise ValueError("Diagnostic condition not present in characterization")
    run["content_sha256"] = _digest({"run": run, "artifacts": artifacts})
    registry.mkdir(parents=True, exist_ok=True)
    with (registry / ".import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = registry / run_id
        if target.exists():
            old = json.loads((target / "run.json").read_text())
            if old.get("content_sha256") == run["content_sha256"]:
                return old
            raise ValueError("Run ID already exists with different evidence; choose a new ID")
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=registry))
        try:
            (temporary / "run.json").write_text(json.dumps(run, indent=2, allow_nan=False) + "\n")
            for cid, artifact in artifacts.items():
                (temporary / f"{cid}.json").write_text(json.dumps(artifact, allow_nan=False) + "\n")
            if diagnostic_files:
                (temporary / "diagnostics").mkdir()
            for record in diagnostic_records:
                target_file = temporary / "diagnostics" / record["id"]
                shutil.copyfile(diagnostic_files[record["id"]], target_file)
                if file_sha256(target_file) != record["sha256"]:
                    raise ValueError("Diagnostic changed during import")
            os.rename(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return run


class Registry:
    def __init__(self, root: Path, workspace_id: str) -> None:
        self.root = root.resolve()
        self.workspace_id = identifier(workspace_id)

    def run(self, run_id: str) -> dict[str, Any]:
        path = self.root / identifier(run_id) / "run.json"
        if self.root not in path.resolve().parents:
            raise ValueError("Artifact outside registry")
        data = json.loads(path.read_text())
        if data["workspace_id"] != self.workspace_id:
            raise FileNotFoundError("Run not found")
        return data

    def runs(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.root.glob("*/run.json")):
            if path.parent.name.startswith("."):
                continue
            try:
                result.append(self.run(path.parent.name))
            except (ValueError, FileNotFoundError):
                continue
        return result

    def condition(self, run_id: str, condition_id: str) -> dict[str, Any]:
        run = self.run(run_id)
        for condition in run["conditions"]:
            if condition["id"] == condition_id:
                return condition
        raise FileNotFoundError("Condition not found")

    def tasks(self, run_id: str, condition_id: str) -> list[dict[str, Any]]:
        self.condition(run_id, condition_id)
        path = self.root / identifier(run_id) / (identifier(condition_id) + ".json")
        if self.root not in path.resolve().parents:
            raise ValueError("Artifact outside registry")
        return json.loads(path.read_text())["tasks"]

    def task(self, run_id: str, condition_id: str, task_id: str) -> dict[str, Any]:
        for task in self.tasks(run_id, condition_id):
            if task["id"] == task_id:
                return task
        raise FileNotFoundError("Task not found")

    def diagnostic(self, run_id: str, condition_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        condition = self.condition(run_id, condition_id)
        for record in condition.get("diagnostics", []):
            if record["id"] == artifact_id:
                path = self.root / identifier(run_id) / "diagnostics" / identifier(artifact_id)
                if self.root not in path.resolve().parents or not path.is_file():
                    raise FileNotFoundError("Diagnostic not found")
                return path, record
        raise FileNotFoundError("Diagnostic not found")


def compare(baseline_run: dict[str, Any], baseline: dict[str, Any],
            candidate_run: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    differences, missing = [], []
    for key in CONTRACT_FIELDS:
        a = {**baseline_run["contract"], **baseline.get("contract", {})}.get(key)
        b = {**candidate_run["contract"], **candidate.get("contract", {})}.get(key)
        if a in (None, "", "unknown") or b in (None, "", "unknown"):
            missing.append(key)
        elif a != b:
            differences.append(key)
    admitted = baseline["admission"] == candidate["admission"] == "passed"
    comparable = not differences and not missing and admitted
    deltas = []
    base_defs = {m["id"]: m for m in baseline_run["metric_definitions"]}
    for definition in candidate_run["metric_definitions"]:
        key = definition["id"]
        if definition != base_defs.get(key):
            continue
        a, b = baseline["metrics"].get(key), candidate["metrics"].get(key)
        deltas.append({"metric": key, "baseline": a, "candidate": b,
                       "percent_change": (b / a - 1) * 100 if comparable and a is not None and a != 0 and b is not None else None})
    return {"comparable": comparable, "contract_differences": differences, "contract_missing": missing,
            "both_admitted": admitted, "metrics": deltas,
            "limitations": ["Compatibility is contributor-declared, not independently verified.",
                            "Different K or intervention factors must be interpreted explicitly.",
                            "Single-run deltas are descriptive, not statistical significance or causal proof."]}


def insights(condition: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    if condition["admission"] != "passed":
        observations.append({"title": "검증되지 않은 비교 조건", "detail": "차트에서 관찰할 수 있지만 개선율 계산에는 사용하지 않습니다.", "evidence": ["admission"]})
    if condition["requests"] is not None and condition["exact_join_requests"] != condition["requests"]:
        observations.append({"title": "서버 trace 연결이 불완전합니다", "detail": "연결되지 않은 요청의 내부 단계를 추정하지 않습니다.", "evidence": ["requests", "exact_join_requests"]})
    expensive = max(condition["tool_types"], key=lambda t: t["p95_ms"] if t["p95_ms"] is not None else -1, default=None)
    if expensive and expensive["p95_ms"] is not None:
        observations.append({"title": "Tool 유형별 tail 확인", "detail": f"가장 높은 관측 p95: {expensive['shape']} / {expensive['p95_ms']:.3f} ms. 호출 수와 함께 해석하세요.", "evidence": ["tool_types"]})
    observations.append({"title": "생성 속도와 생성 수요를 함께 비교", "detail": "tok/s/GPU, task/s, task당 입력·출력 토큰을 함께 확인하세요. 답변 품질은 별도 평가가 필요합니다.", "evidence": ["metrics.output_tok_s_gpu", "metrics.task_s", "metrics.input_tokens", "metrics.output_tokens", "quality"]})
    return observations
