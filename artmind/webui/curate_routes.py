"""Admin-console routes for the Curate tab (spec 2026-11-12 §2).

Thin: every route calls the library function the CLI calls and returns its `effect`.
A write with `dry_run: true` writes nothing. Neo4j work runs off the event loop.
Library calls are module-level wrappers so tests can replace them.
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_sweep: dict = {"state": "idle"}


# ── pure helpers ─────────────────────────────────────────────────────
def parse_member(key: str) -> dict:
    parts = str(key).split("|")
    name, cls, domain = (parts + ["", "", ""])[:3] if len(parts) < 3 else (parts[0], parts[1], parts[2])
    return {"key": key, "name": name, "class": cls, "domain": domain}


def domain_matches(member_domain: str, wanted: str) -> bool:
    return member_domain == wanted or member_domain.startswith(wanted + ".")


def classify_proposal(proposal: dict) -> dict:
    """Row for the pane: parsed members and Merge/Link, as `sameas._plan_group` decides it
    at approve time (spec §2.2): merge when members share class and domain, else link."""
    members = [parse_member(m) for m in proposal.get("members") or []]
    same = len({(m["class"], m["domain"]) for m in members}) <= 1
    return {
        "id": proposal.get("id"), "canonical": proposal.get("canonical"), "members": members,
        "reason": proposal.get("reason") or "", "source": proposal.get("source") or "",
        "status": proposal.get("status"), "detected_at": proposal.get("detected_at"),
        "resolution_reason": proposal.get("resolution_reason"),
        "class": members[0]["class"] if members else "", "action": "merge" if same else "link",
    }


# ── library wrappers (replaced in tests) ─────────────────────────────
def _list_proposals(status):
    from artmind import sameas
    return sameas.list_proposals(status)


def _proposal(pid):
    from artmind import sameas
    return sameas.get_proposal(pid)


def _approve_many(ids, canonical, dry_run):
    from artmind import sameas
    return sameas.approve_many(ids, canonical=canonical, dry_run=dry_run)


def _reject(pid, reason, dry_run):
    from artmind import sameas
    return sameas.reject(pid, reason, dry_run=dry_run)


def _evidence(key: str) -> list[dict]:
    from artmind.conflicts import gather_evidence
    from artmind.graph_query import neo4j_session
    from artmind.observations import entity_id

    parts = tuple(key.split("|"))
    if len(parts) != 3:
        return []
    with neo4j_session() as session:
        return gather_evidence(session, entity_id(parts), 3)


def _open_conflicts() -> int:
    from artmind.cli import _get_available_domains
    from artmind.graph_query import list_conflicts

    domains = _get_available_domains()
    return len(list_conflicts(domains, status="open")["rows"]) if domains else 0


def _projection_status() -> dict:
    from artmind import projection
    from artmind.graph_query import read_session

    with read_session() as session:
        return session.execute_read(projection.status)


def _plan_sweep() -> dict:
    from artmind import projection
    return projection.plan_full_rebuild(None)


def _run_sweep() -> dict:
    from artmind.ingest import rebuild_and_sweep_all
    return rebuild_and_sweep_all()


# ── request models ───────────────────────────────────────────────────
class ApproveRequest(BaseModel):
    ids: list[str]
    canonical: str | None = None
    dry_run: bool = True


class RejectRequest(BaseModel):
    ids: list[str]
    reason: str
    dry_run: bool = True


class SweepRequest(BaseModel):
    dry_run: bool = True


def _run_sweep_job() -> None:
    try:
        _sweep.update(state="done", result=_run_sweep())
    except Exception as exc:  # noqa: BLE001 - shown to the operator
        _sweep.update(state="failed", error=str(exc))


def register_curate_routes(app: FastAPI) -> FastAPI:
    @app.get("/api/curate/overview")
    async def overview():
        warnings: list[str] = []
        out: dict = {"sameas_open": None, "conflicts_open": None, "drift": None}
        for key, label, fn, pick in (
            ("sameas_open", "same-as", lambda: _list_proposals("open"), len),
            ("conflicts_open", "conflicts", _open_conflicts, lambda v: v),
            ("drift", "projection", _projection_status, lambda v: bool(v.get("drift"))),
        ):
            try:
                out[key] = pick(await asyncio.to_thread(fn))
            except Exception as exc:  # noqa: BLE001 - a queue that cannot be read says so
                warnings.append(f"{label}: could not read ({exc})")
        out["warnings"] = warnings
        return out

    @app.get("/api/curate/sameas")
    async def sameas_list(status: str = "open", domain: str = "", source: str = ""):
        rows = [classify_proposal(p) for p in await asyncio.to_thread(_list_proposals, status)]
        if domain:
            rows = [r for r in rows if any(domain_matches(m["domain"], domain) for m in r["members"])]
        if source:
            rows = [r for r in rows if r["source"] == source]
        return {"status": status, "rows": rows}

    @app.get("/api/curate/sameas/{pid}/evidence")
    async def sameas_evidence(pid: str):
        proposal = await asyncio.to_thread(_proposal, pid)
        if not proposal:
            raise HTTPException(status_code=404, detail=f"no proposal {pid}")
        members = []
        for m in classify_proposal(proposal)["members"]:
            m["evidence"] = await asyncio.to_thread(_evidence, m["key"])
            members.append(m)
        return {"id": pid, "members": members}

    @app.post("/api/curate/sameas/approve")
    async def sameas_approve(req: ApproveRequest):
        if not req.ids:
            raise HTTPException(status_code=400, detail="no proposals selected")
        if req.canonical and len(req.ids) != 1:
            raise HTTPException(status_code=400, detail="a canonical override applies to one proposal")
        try:
            return await asyncio.to_thread(_approve_many, req.ids, req.canonical, req.dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/curate/sameas/reject")
    async def sameas_reject(req: RejectRequest):
        if not req.reason.strip():
            raise HTTPException(status_code=400, detail="a reason is required")
        results = []
        for pid in req.ids:
            try:
                results.append(await asyncio.to_thread(_reject, pid, req.reason.strip(), req.dry_run))
            except ValueError as exc:
                results.append({"id": pid, "error": str(exc)})
        return {"dry_run": req.dry_run, "results": results}

    @app.post("/api/curate/sweep", status_code=200)
    async def sweep(req: SweepRequest):
        if req.dry_run:
            return {"dry_run": True, "plan": await asyncio.to_thread(_plan_sweep)}
        if _sweep.get("state") == "running":
            raise HTTPException(status_code=409, detail="a sweep is already running")
        _sweep.clear()
        _sweep["state"] = "running"
        threading.Thread(target=_run_sweep_job, daemon=True).start()
        return {"dry_run": False, "state": "running"}

    @app.get("/api/curate/sweep")
    async def sweep_status():
        return dict(_sweep)

    return app
