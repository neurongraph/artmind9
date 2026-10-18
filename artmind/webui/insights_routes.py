"""Admin-console routes for the Insights and Views tabs (spec 2026-10-17 §10.4–§10.5).

Read routes serve the latest snapshot or the view catalogue from disk. Refresh
runs `artmind.insights.runner.refresh` in a background thread with a progress
readout. "Send to curation" proposes same-as groups through refine-graph's
review queue; it never merges. Views are read-only here: authoring stays with
the artmind-create-view skill.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_progress: dict[str, dict] = {}
_threads: dict[str, threading.Thread] = {}


class RefreshRequest(BaseModel):
    domain: str
    force: bool = False


class ProposeRequest(BaseModel):
    domain: str
    keys: list[str]


class ViewRunRequest(BaseModel):
    domain: str
    params: dict[str, str] = {}


def _latest(domain: str):
    from artmind.insights import store
    from artmind.insights.model import InsightsError

    try:
        run = store.load_latest(domain)
    except InsightsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail=f"no insights snapshot for {domain}: run a refresh")
    return run


def _refresh(domain: str, force: bool) -> dict:
    from artmind.insights import runner

    return runner.refresh(domain, force=force)


def _propose(merges: dict[str, str], domain: str) -> dict:
    from artmind.refine_graph import propose_merges
    from utils.functions import load_env, resolve_llm_model

    return propose_merges(merges, domain, resolve_llm_model(load_env()))


def _run_view(spec, domains: list[str], params: dict[str, str]) -> dict:
    from artmind.kg_views.runner import run_view

    return run_view(spec, domains, params, render=True)


def _join_refresh(domain: str, timeout: float = 30.0) -> None:
    """Test helper: wait for a background refresh to finish."""
    t = _threads.get(domain)
    if t is not None:
        t.join(timeout)


def register_insights_routes(app: FastAPI) -> FastAPI:
    @app.get("/api/insights")
    async def api_insights(domain: str):
        run = _latest(domain)
        return {"domain": run.domain, "created_at": run.created_at, "as_of": run.as_of, "anchors": run.anchors,
                "diff": run.diff, "llm": run.llm, "circles": run.circles,
                "plan": [p.to_dict() for p in run.plan if p.status != "ran"],
                "findings": [f.to_dict() for f in run.findings if f.diff != "gone"]}

    @app.get("/api/insights/runs")
    async def api_insights_runs(domain: str):
        from artmind.insights import store
        from artmind.insights.model import InsightsError

        try:
            rows = []
            for path in store.list_runs(domain):
                run = store.load_run(path)
                rows.append({"run": path.name, "created_at": run.created_at, "as_of": run.as_of, "diff": run.diff,
                             "findings": sum(1 for f in run.findings if f.diff != "gone")})
        except InsightsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"domain": domain, "runs": list(reversed(rows))}

    @app.post("/api/insights/refresh", status_code=202)
    async def api_insights_refresh(payload: RefreshRequest):
        from artmind.insights import store
        from artmind.insights.model import InsightsError

        try:
            store.domain_dir(payload.domain)
        except InsightsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        current = _progress.get(payload.domain, {})
        if current.get("state") == "running":
            raise HTTPException(status_code=409, detail="a refresh is already running for this domain")
        _progress[payload.domain] = {"state": "running", "started": datetime.now(timezone.utc).isoformat()}

        def work():
            try:
                result = _refresh(payload.domain, payload.force)
                _progress[payload.domain] = {"state": "done", "result": result}
            except Exception as exc:  # surfaced to the operator via the progress readout
                _progress[payload.domain] = {"state": "error", "detail": str(exc)}
            finally:  # never leave the domain locked on "running"
                if _progress.get(payload.domain, {}).get("state") == "running":
                    _progress[payload.domain] = {"state": "error", "detail": "refresh stopped unexpectedly"}

        t = threading.Thread(target=work, name=f"insights-refresh-{payload.domain}", daemon=True)
        _threads[payload.domain] = t
        t.start()
        return {"state": "running"}

    @app.get("/api/insights/refresh/progress")
    async def api_insights_refresh_progress(domain: str):
        return _progress.get(domain, {"state": "idle"})

    @app.post("/api/insights/same-as/propose")
    async def api_insights_propose(payload: ProposeRequest):
        from artmind.insights.sameas_merges import build_merges

        run = _latest(payload.domain)
        merges, candidates, skipped = build_merges(run.findings, only_keys=set(payload.keys))
        if not merges:
            return {"stats": {"proposed": 0}, "skipped": skipped, "candidates": []}
        import asyncio

        # Neo4j work: off the event loop, so the console and its streams stay responsive.
        result = await asyncio.to_thread(_propose, merges, payload.domain)
        return {**result, "skipped": skipped, "candidates": candidates}

    @app.get("/api/views")
    async def api_views(domain: str | None = None):
        from artmind.kg_views import store as views_store

        domains = [d.strip() for d in domain.split(",") if d.strip()] if domain else None
        loaded, invalid = views_store.list_views(domains)
        return {"views": [{"name": v.spec.name, "version": v.spec.version, "summary": v.spec.summary,
                           "domains": list(v.spec.domains), "examples": list(v.spec.examples),
                           "params": v.spec.to_dict().get("params") or {},
                           "presentation_format": v.spec.presentation.format}
                          for v in loaded], "invalid": invalid}

    @app.get("/api/views/{name}")
    async def api_view(name: str):
        from artmind.kg_views import store as views_store
        from artmind.kg_views.model import ViewError

        try:
            loaded = views_store.load_view(name)
        except ViewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"view": loaded.spec.to_dict(), "skill_md": loaded.skill_md}

    @app.post("/api/views/{name}/run")
    async def api_view_run(name: str, payload: ViewRunRequest):
        import asyncio

        from artmind.kg_views import store as views_store
        from artmind.kg_views.model import ViewError

        try:
            loaded = views_store.load_view(name)
            return await asyncio.to_thread(_run_view, loaded.spec, [payload.domain], payload.params)
        except ViewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
