"""The admin console's Topics tab (spec 2026-11-10 §8.3): topic plans with their stored
freshness (and what an automatic refresh did or why it was skipped), and the topic pages,
read from disk on every request. `POST /api/topics/{slug}/check` runs a freshness check
(graph read, no LLM). Page text is private corpus content; the JS renders it sanitized."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def _plans() -> dict:
    from artmind.topics import freshness, plan_store

    good, invalid = plan_store.list_plans()
    rows = []
    for loaded in good:
        row = loaded.spec.summary()
        row["freshness"] = freshness.stored(loaded.spec.slug)
        rows.append(row)
    return {"plans": rows, "invalid": invalid}


def _pages() -> list:
    from artmind.topics import store

    try:
        pages = store.list_pages(store.topics_dir())
    except ValueError:
        return []
    out = []
    for p in pages:
        built = p.meta.get("built")
        out.append({"slug": p.slug, "topic": p.meta.get("topic"), "domains": p.meta.get("domains") or [],
                    "built_at": built.get("at") if isinstance(built, dict) else built,
                    "sources": len([s for s in p.meta.get("sources") or [] if isinstance(s, dict)])})
    return out


def register_topics_routes(app: FastAPI) -> FastAPI:
    @app.get("/api/topics")
    async def api_topics():
        return {**_plans(), "pages": _pages()}

    @app.post("/api/topics/{slug}/check")
    async def api_topic_check(slug: str):
        import asyncio

        from artmind.topics import freshness, plan_store
        from artmind.topics.plan import PlanError

        try:
            loaded = plan_store.load_plan(slug)
        except PlanError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return await asyncio.to_thread(freshness.check, loaded)

    @app.get("/api/topics/{slug}/page")
    async def api_topic_page(slug: str):
        from artmind.topics import plan_store, store

        try:
            plan_store.check_slug(slug)
            page = store.read_page(store.topics_dir(), slug)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if page is None:
            raise HTTPException(status_code=404, detail=f"no topic page {slug!r}")
        return {"slug": slug, "topic": page.meta.get("topic"), "body": page.body}

    return app
