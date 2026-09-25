"""Read-only experiment viewer; registry writers use the reviewed import CLI."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from seocho.eval.experiment_platform import Registry, compare, insights, perfetto_trace
from seocho.eval.experiment_evidence import structured_logs, otlp_logs
import json


def create_app(registry_path: Path, workspace_id: str, *, token: str | None = None) -> FastAPI:
    registry = Registry(registry_path, workspace_id)
    app = FastAPI(title="gare2e Experiment Platform", docs_url=None, redoc_url=None, openapi_url=None)
    assets = Path(__file__).parent / "experiment_platform_web"

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(401, "Team access token required")

    @app.middleware("http")
    async def response_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(StopIteration)
    def missing(request: Any, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Result not found"}, status_code=404)

    @app.exception_handler(ValueError)
    def invalid(request: Any, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "Invalid result identifier or evidence"}, status_code=400)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(assets / "index.html")

    @app.get("/app.js")
    def javascript() -> FileResponse:
        return FileResponse(assets / "app.js", media_type="application/javascript")

    @app.get("/style.css")
    def style() -> FileResponse:
        return FileResponse(assets / "style.css", media_type="text/css")

    @app.get("/api/runs", dependencies=[Depends(authorize)])
    def runs() -> dict[str, Any]:
        return {"workspace_id": workspace_id, "runs": registry.runs()}

    @app.get("/api/compare", dependencies=[Depends(authorize)])
    def comparison(baseline_run: str, baseline_condition: str, candidate_run: str, candidate_condition: str) -> dict[str, Any]:
        return compare(registry.run(baseline_run), registry.condition(baseline_run, baseline_condition),
                       registry.run(candidate_run), registry.condition(candidate_run, candidate_condition))

    @app.get("/api/runs/{run_id}/conditions/{condition_id}", dependencies=[Depends(authorize)])
    def condition(run_id: str, condition_id: str) -> dict[str, Any]:
        item = registry.condition(run_id, condition_id)
        return {"condition": item, "insights": insights(item)}

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/tasks", dependencies=[Depends(authorize)])
    def tasks(run_id: str, condition_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), search: str = "") -> dict[str, Any]:
        rows = registry.tasks(run_id, condition_id)
        rows = [t for t in rows if search.lower() in (t["case_id"] + " " + t["terminal"] + " " + t["id"]).lower()]
        rows.sort(key=lambda t: t["wall_ms"] if t["wall_ms"] is not None else -1, reverse=True)
        return {"total": len(rows), "offset": offset,
                "tasks": [{k: v for k, v in t.items() if k not in {"events", "limitations", "queries", "evidence_insights", "quality_evaluation"}} for t in rows[offset:offset + limit]]}

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/diagnostics/{artifact_id}", dependencies=[Depends(authorize)])
    def diagnostic(run_id: str, condition_id: str, artifact_id: str) -> FileResponse:
        path, record = registry.diagnostic(run_id, condition_id, artifact_id)
        extension = ".sqlite" if record["type"] == "nsight_sqlite" else ".nsys-rep"
        return FileResponse(path, filename=artifact_id + extension, media_type="application/octet-stream",
                            headers={"X-Artifact-SHA256": record["sha256"]})

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/tasks/{task_id}", dependencies=[Depends(authorize)])
    def task(run_id: str, condition_id: str, task_id: str) -> dict[str, Any]:
        return registry.task(run_id, condition_id, task_id)

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/tasks/{task_id}/perfetto", dependencies=[Depends(authorize)])
    def trace(run_id: str, condition_id: str, task_id: str) -> dict[str, Any]:
        result = perfetto_trace(registry.task(run_id, condition_id, task_id))
        result["metadata"].update(workspace_id=workspace_id, run_id=run_id, condition_id=condition_id)
        return result

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/tasks/{task_id}/logs", dependencies=[Depends(authorize)])
    def logs(run_id: str, condition_id: str, task_id: str) -> Response:
        records = structured_logs(registry.task(run_id, condition_id, task_id))
        for record in records:
            record.update(workspace_id=workspace_id, registry_run_id=run_id, condition_id=condition_id)
        return Response("".join(json.dumps(r) + "\n" for r in records), media_type="application/x-ndjson")

    @app.get("/api/runs/{run_id}/conditions/{condition_id}/tasks/{task_id}/otlp-logs", dependencies=[Depends(authorize)])
    def export_logs(run_id: str, condition_id: str, task_id: str) -> dict[str, Any]:
        result = otlp_logs(registry.task(run_id, condition_id, task_id))
        result["resourceLogs"][0]["resource"]["attributes"].extend(
            {"key": key, "value": {"stringValue": value}}
            for key, value in {"workspace_id": workspace_id, "gare2e.registry_run_id": run_id, "gare2e.condition_id": condition_id}.items()
        )
        return result

    @app.get("/metrics", dependencies=[Depends(authorize)])
    def platform_metrics() -> Response:
        # Registry inventory gauges, NOT workload throughput or per-query time series.
        runs = registry.runs()
        conditions = [c for r in runs for c in r["conditions"]]
        values = {"registered_runs": len(runs), "registered_conditions": len(conditions),
                  "imported_tasks": sum(c["task_count"] for c in conditions),
                  "imported_query_attempts": sum(c.get("query_attempts", 0) for c in conditions),
                  "evaluated_tasks": sum(c.get("evaluated_tasks", 0) for c in conditions)}
        lines = []
        for name, value in values.items():
            metric = "gare2e_registry_" + name
            lines.extend([f"# HELP {metric} Current imported registry inventory, not live workload measurements.",
                          f"# TYPE {metric} gauge", f"{metric} {value}"])
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    return app
