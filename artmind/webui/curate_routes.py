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


# ── CLI bridge: commands whose logic lives in a Click body run in process (output unchanged) ──
_cli_lock = threading.Lock()


def _cli(args: list[str]) -> dict:
    """Run `artmind <args> --compact` in process and return its JSON; a failure raises ValueError."""
    import json

    from click.testing import CliRunner

    from artmind.cli import cli

    with _cli_lock:
        result = CliRunner().invoke(cli, [*args, "--compact"])
    if result.exit_code != 0:
        text = " ".join(ln.strip(" │╭╮╰╯─") for ln in (result.output or "").splitlines())
        text = " ".join(text.replace("Error", "", 1).split())
        raise ValueError(text or f"artmind {' '.join(args)} failed")
    return json.loads(result.stdout)


def _sample(column: dict):
    """A column's distinct_sample, from its profile (a JSON string or dict)."""
    import json

    profile = column.get("profile_json") or column.get("profile") or {}
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError:
            return None
    return (profile or {}).get("distinct_sample") or column.get("distinct_sample")


# ── more library wrappers ────────────────────────────────────────────
def _domains_for(domain: str) -> list[str]:
    from artmind.cli import _get_available_domains

    return [domain] if domain else _get_available_domains()


def _list_conflicts(status, domain):
    from artmind.graph_query import list_conflicts

    domains = _domains_for(domain)
    return list_conflicts(domains, status=status)["rows"] if domains else []


def _closed_documents(doc_ids) -> set:
    """Documents that carry a valid_to (an older version): one side of a conflict may be stale."""
    from artmind.graph_query import neo4j_session

    if not doc_ids:
        return set()
    with neo4j_session() as session:
        rows = session.run(
            "MATCH (d:Document) WHERE d.id IN $ids AND coalesce(d.valid_to, d._valid_to) IS NOT NULL "
            "OR (d.id IN $ids AND EXISTS { MATCH (:Document)-[:SUPERSEDES]->(d) }) RETURN DISTINCT d.id AS id",
            ids=list(doc_ids),
        ).data()
    return {r["id"] for r in rows}


def _resolve_conflict(cid, status, reason, dry_run):
    from artmind.conflicts import resolve_conflict

    return resolve_conflict(cid, status, reason, dry_run=dry_run)


def _effect(summary: str, **more) -> dict:
    return {"summary": summary, **more}


def _table_write(kind, table, domain, action, column, value):
    """The registry writes behind `db mappings|bridge|grain` (no dry run exists for them)."""
    from artmind.cli import _reexport_table_meta, _resolve_table_row
    from artmind.structured import registry as reg

    row = _resolve_table_row(table, (domain,) if domain else ())
    tid = row["id"]
    if kind == "mapping":
        if action == "confirm":
            if reg.set_mapping_confirmed(tid, column, value, True) == 0:
                raise ValueError(f"no mapping for column {column!r} / class {value!r}")
        elif action == "set":
            reg.upsert_mapping(tid, column, value, 1.0, confirmed=True)
        else:
            reg.clear_mappings(tid, column)
    elif kind == "bridge":
        if action == "confirm":
            if reg.set_column_role_confirmed(tid, column, True) == 0:
                raise ValueError(f"no bridge column {column!r} on {table!r}")
        else:
            reg.clear_column_roles(tid, column)
    else:
        reg.set_grain(tid, value, confirmed=True)
    _reexport_table_meta(tid)
    return {"table": table, "domain": row["domain"], "done": True}


# ── request models (steps 2-5) ───────────────────────────────────────
class CloseRequest(BaseModel):
    status: str
    reason: str
    dry_run: bool = True


class MappingRequest(BaseModel):
    domain: str = ""
    action: str
    column: str
    entity_class: str | None = None
    dry_run: bool = True


class GrainRequest(BaseModel):
    domain: str = ""
    grain: str
    dry_run: bool = True


class BridgeRequest(BaseModel):
    domain: str = ""
    action: str
    column: str
    dry_run: bool = True


class DomainRequest(BaseModel):
    domain: str


class MartMemberRequest(BaseModel):
    domain: str
    dimension: str
    member: str
    levels: dict[str, str]
    dry_run: bool = True


class MartBuildRequest(BaseModel):
    domain: str
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

    # ── conflicts ────────────────────────────────────────────────────
    @app.get("/api/curate/conflicts")
    async def conflicts_list(status: str = "open", domain: str = ""):
        rows = await asyncio.to_thread(_list_conflicts, status, domain)
        ids = {e["doc_id"] for r in rows for e in r.get("evidence") or [] if e.get("doc_id")}
        closed = await asyncio.to_thread(_closed_documents, ids)
        groups: dict[str, list] = {}
        for r in rows:
            r = dict(r)
            r["older_side_docs"] = sorted({e["doc_id"] for e in r.get("evidence") or [] if e.get("doc_id") in closed})
            groups.setdefault((r.get("conflict") or {}).get("aspect") or "", []).append(r)
        return {"status": status, "groups": [{"aspect": a, "rows": g} for a, g in groups.items()]}

    @app.post("/api/curate/conflicts/{cid}/close")
    async def conflict_close(cid: str, req: CloseRequest):
        if not req.reason.strip():
            raise HTTPException(status_code=400, detail="a reason is required")
        try:
            return await asyncio.to_thread(_resolve_conflict, cid, req.status, req.reason.strip(), req.dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ── structured tables ────────────────────────────────────────────
    @app.get("/api/curate/tables")
    async def tables_list(domain: str = ""):
        scope = ["--domain", domain] if domain else []
        try:
            review = await asyncio.to_thread(_cli, ["db", "review", *scope])
            schema = await asyncio.to_thread(_cli, ["db", "schema", *scope])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cols = {t["table_name"]: t.get("columns") or [] for t in schema.get("tables", [])}
        for t in review.get("tables", []):
            t["samples"] = {c["name"]: _sample(c) for c in cols.get(t["table"], []) if _sample(c)}
        return review

    async def _table_op(kind, table, domain, action, column, value, dry_run, words):
        if dry_run:
            return {"dry_run": True, "effect": _effect(words, reversible=False)}
        try:
            return await asyncio.to_thread(_table_write, kind, table, domain, action, column, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/curate/tables/{table}/mapping")
    async def table_mapping(table: str, req: MappingRequest):
        if req.action not in ("confirm", "clear", "set"):
            raise HTTPException(status_code=400, detail="action must be confirm, clear or set")
        if req.action != "clear" and not req.entity_class:
            raise HTTPException(status_code=400, detail="entity_class is required")
        words = {
            "confirm": f"Confirm column {req.column} as {req.entity_class} on {table}.",
            "set": f"Set column {req.column} to {req.entity_class} (confirmed) on {table}.",
            "clear": f"Clear every class mapped to column {req.column} on {table}.",
        }[req.action]
        return await _table_op("mapping", table, req.domain, req.action, req.column, req.entity_class, req.dry_run, words)

    @app.post("/api/curate/tables/{table}/grain")
    async def table_grain(table: str, req: GrainRequest):
        words = f"Confirm {table}'s grain as {req.grain}."
        return await _table_op("grain", table, req.domain, "set", None, req.grain, req.dry_run, words)

    @app.post("/api/curate/tables/{table}/bridge")
    async def table_bridge(table: str, req: BridgeRequest):
        if req.action not in ("confirm", "clear"):
            raise HTTPException(status_code=400, detail="action must be confirm or clear")
        words = f"{req.action.capitalize()} the bridge role of column {req.column} on {table}."
        return await _table_op("bridge", table, req.domain, req.action, req.column, None, req.dry_run, words)

    @app.post("/api/curate/catalogue")
    async def catalogue(req: DomainRequest):
        try:
            return await asyncio.to_thread(_cli, ["db", "catalogue", "--domain", req.domain])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ── graph health ─────────────────────────────────────────────────
    @app.get("/api/curate/health")
    async def health(domain: str):
        try:
            return await asyncio.to_thread(_cli, ["insights", "list", "--domain", domain, "--kind", "health"])
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── mart ─────────────────────────────────────────────────────────
    @app.get("/api/curate/mart")
    async def mart(domain: str):
        try:
            status = await asyncio.to_thread(_cli, ["mart", "status", "--domain", domain])
            review = await asyncio.to_thread(_cli, ["mart", "review", "list", "--domain", domain])
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": status, "rows": review.get("rows", [])}

    @app.post("/api/curate/mart/member")
    async def mart_member(req: MartMemberRequest):
        args = ["mart", "review", "set", "--domain", req.domain, "--dimension", req.dimension, "--member", req.member]
        for k, v in req.levels.items():
            args += ["--level", f"{k}={v}"]
        if req.dry_run:
            args.append("--dryRun")
        try:
            return await asyncio.to_thread(_cli, args)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/curate/mart/build")
    async def mart_build(req: MartBuildRequest):
        args = ["mart", "build", "--domain", req.domain] + (["--dryRun"] if req.dry_run else [])
        try:
            return await asyncio.to_thread(_cli, args)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
