"""Lane B JSON endpoints for the admin benchmark driver.

Runs a batch of Q&A questions from a markdown file through the artmind query
agent, one after another, and records every answer. Mounted only on the admin
app (`create_app(..., admin_routes=True)` / `run_admin_ui`), alongside the
ingest dashboard routes — never the Q&A app.
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from artmind.benchmark import (
    _cancel_benchmark_run,
    _create_benchmark_run,
    _export_benchmark_json,
    _export_benchmark_markdown,
    _fetch_active_benchmark_runs,
    _fetch_completed_benchmark_runs,
    _get_benchmark_question,
    _get_benchmark_run,
    _list_benchmark_runs,
    _parse_questions_file,
    _run_benchmark,
)
from artmind.webui.dashboard_routes import _camelize


class BenchmarkPreviewRequest(BaseModel):
    path: str


class BenchmarkRunRequest(BaseModel):
    path: str
    domain_hint: str | None = Field(None, alias="domainHint")
    backend: str = "claude-sdk"
    name: str | None = None

    model_config = {"populate_by_name": True}


def _resolve_questions_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path_str}")
    return path


def register_benchmark_routes(app: FastAPI, templates: Jinja2Templates) -> FastAPI:
    @app.post("/api/benchmark/preview")
    async def api_benchmark_preview(payload: BenchmarkPreviewRequest):
        path = _resolve_questions_path(payload.path)
        questions = _parse_questions_file(path)
        return _camelize({"question_count": len(questions), "questions": questions})

    @app.post("/api/benchmark/runs")
    async def api_benchmark_create_run(payload: BenchmarkRunRequest):
        path = _resolve_questions_path(payload.path)
        questions = _parse_questions_file(path)
        if not questions:
            raise HTTPException(status_code=400, detail=f"No questions parsed from {payload.path}")
        run_id = _create_benchmark_run(
            source_path=str(path.resolve()),
            questions=questions,
            domain_hint=payload.domain_hint,
            backend=payload.backend,
            name=payload.name,
        )
        asyncio.create_task(_run_benchmark(run_id))
        return _camelize({"run_id": run_id, "question_count": len(questions)})

    @app.get("/api/benchmark/runs")
    async def api_benchmark_list_runs(status: str | None = None):
        return _camelize(_list_benchmark_runs(status_filter=status))

    @app.get("/api/benchmark/runs/active")
    async def api_benchmark_runs_active():
        return _camelize(_fetch_active_benchmark_runs())

    @app.get("/api/benchmark/runs/completed")
    async def api_benchmark_runs_completed(limit: int = 100):
        return _camelize(_fetch_completed_benchmark_runs(limit=limit))

    @app.get("/api/benchmark/runs/{run_id}")
    async def api_benchmark_run_detail(run_id: str):
        run = _get_benchmark_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Benchmark run '{run_id}' not found")
        return _camelize(run)

    @app.get("/api/benchmark/runs/{run_id}/questions/{seq}")
    async def api_benchmark_question_detail(run_id: str, seq: int):
        question = _get_benchmark_question(run_id, seq)
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"Question {seq} not found in run '{run_id}'"
            )
        return _camelize(question)

    @app.post("/api/benchmark/runs/{run_id}/cancel")
    async def api_benchmark_cancel_run(run_id: str):
        try:
            result = _cancel_benchmark_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _camelize(result)

    @app.get("/api/benchmark/runs/{run_id}/export")
    async def api_benchmark_export_run(run_id: str, format: str = "md"):
        if format not in ("md", "json"):
            raise HTTPException(status_code=400, detail="format must be 'md' or 'json'")
        if format == "json":
            result = _export_benchmark_json(run_id)
            if result is None:
                raise HTTPException(status_code=404, detail=f"Benchmark run '{run_id}' not found")
            return _camelize(result)
        text = _export_benchmark_markdown(run_id)
        if text is None:
            raise HTTPException(status_code=404, detail=f"Benchmark run '{run_id}' not found")
        return PlainTextResponse(
            text,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="benchmark_{run_id}.md"'},
        )

    return app
