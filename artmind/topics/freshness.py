"""Topic plan freshness (spec 2026-11-10 §8.1), no LLM.

1. Short-circuit: every scoped domain's catalogue generation (`:DomainState`, bumped by
   every write path) equals `built.generations` → **fresh**.
2. Otherwise re-count the grid (the dry run's keyword query) and compare: a cell with more
   matches than at build is grown (`new_documents` sums the growth); a gathered document
   whose version differs from the page's source is `changed`; one no longer a live
   `:Document` is `retired`.

`status`: fresh, stale or unknown (graph unreachable). A plan edited since its build
(version above `built.version`) is stale too. The result is stored beside the plan under
`logs/topic-freshness/<slug>.json` (private); `stored` reads it.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_VERSIONS = """
UNWIND $docIds AS did
OPTIONAL MATCH (d:Document {id: did})
RETURN did AS doc_id, d IS NOT NULL AS live, coalesce(d.content_sha256, d.fingerprint) AS version
"""


def _dir() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "topic-freshness"


def stored(slug: str) -> dict | None:
    try:
        data = json.loads((_dir() / f"{slug}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def store(slug: str, result: dict) -> None:
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{slug}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, directory / f"{slug}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _page_sources(slug: str) -> dict[str, str | None]:
    from artmind.topics import store as pages

    try:
        page = pages.read_page(pages.topics_dir(), slug)
    except ValueError:
        return {}
    if page is None:
        return {}
    return {s.get("doc_id"): s.get("content_sha") for s in page.meta.get("sources") or []
            if isinstance(s, dict) and s.get("doc_id")}


def check(loaded, session=None, *, now: str | None = None, keep: bool = True) -> dict:
    """The freshness of one plan; stored under logs/ unless `keep` is False."""
    from artmind.graph_query import read_session

    spec = loaded.spec
    built = spec.built or {}
    result = {"status": "unknown", "new_documents": 0, "changed": 0, "retired": 0, "cells_grown": [],
              "reasons": [], "checked_at": now or _now()}
    if not built.get("generations") and not built.get("grid"):
        result["reasons"] = ["never built"]
        result["status"] = "stale"
        return _keep(spec.slug, result, keep)
    own = session is None
    try:
        ctx = read_session() if own else None
        session = ctx.__enter__() if own else session
        try:
            result.update(_check(spec, built, session))
        finally:
            if own:
                ctx.__exit__(None, None, None)
    except Exception as exc:
        result["reasons"] = [f"graph unreachable: {exc}"]
        return _keep(spec.slug, result, keep)
    return _keep(spec.slug, result, keep)


def _keep(slug: str, result: dict, keep: bool) -> dict:
    if keep:
        try:
            store(slug, result)
        except OSError:
            pass
    return result


def _check(spec, built: dict, session) -> dict:
    from artmind.catalogue import state
    from artmind.topics import grid

    reasons = []
    edited = isinstance(built.get("version"), int) and spec.version > built["version"]
    if edited:
        reasons.append(f"plan edited since its build (version {built['version']} -> {spec.version})")
    generations = state.generations(session, spec.domains)
    if not edited and generations == built.get("generations"):
        return {"status": "fresh", "generations": generations, "reasons": []}
    counted, floors = grid.count_all(spec.queries(), list(spec.domains), session=session)
    cells, _ = grid.cells_from(counted, floors, list(spec.domains), spec.by, spec.split, spec.per_cell, spec.max_docs)
    before = {(g.get("subject"), g.get("period"), g.get("domain")): int(g.get("matching") or 0)
              for g in built.get("grid") or [] if isinstance(g, dict)}
    grown, new = [], 0
    for c in cells:
        was = before.get((c.subject, c.period, c.domain), 0)
        if c.matching > was:
            grown.append(c.label())
            new += c.matching - was
    sources = _page_sources(spec.slug)
    docs = sorted(set(built.get("documents") or []) | set(sources))
    changed = retired = 0
    if docs:
        for r in session.run(_VERSIONS, docIds=docs):
            r = dict(r)
            if not r.get("live"):
                retired += 1
            elif r["doc_id"] in sources and sources[r["doc_id"]] and r.get("version") != sources[r["doc_id"]]:
                changed += 1
    if new:
        reasons.append(f"{new} new matching document(s) in {len(grown)} cell(s)")
    if changed:
        reasons.append(f"{changed} cited document(s) changed")
    if retired:
        reasons.append(f"{retired} gathered document(s) retired")
    stale = bool(new or changed or retired or edited)
    out = {"status": "stale" if stale else "fresh", "new_documents": new, "changed": changed, "retired": retired,
           "cells_grown": grown, "generations": generations, "reasons": reasons}
    if spec.discover:
        # Members (spec §6.4): discovery runs again; new candidates are reported, never added.
        from artmind.topics import members as members_mod

        block = members_mod.discover(list(spec.discover.get("anchors") or []), spec.discover.get("entity_class"),
                                     list(spec.domains), session, kept=[m["name"] for m in spec.members],
                                     dropped=list(spec.dropped))
        fresh_names = members_mod.new_candidates(block)
        out["new_candidates"] = len(fresh_names)
        out["new_candidate_names"] = fresh_names[:50]
        if fresh_names:
            reasons.append(f"{len(fresh_names)} co-mentioned candidate(s) neither kept nor dropped")
    return out
