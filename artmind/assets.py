"""Answer assets: everything precomputed that can answer, or steer the answer to, a question
(spec 2026-11-02 §5). Read-only.

One record shape for four kinds: `view` (saved graph queries), `topic_plan` (saved topic plans),
`insight` (a detector's findings in the latest snapshot) and `summary_set` (a domain's
document-summary coverage). `build_assets(domain)` is the catalogue's `assets` part;
`list_assets` merges the parts of several domains; `like` ranks by meaning.
Topic plans hold the user's words and insight snapshots private corpus text, so the records live only in
the catalogue cache under `logs/`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

KINDS = ("view", "topic_plan", "insight", "summary_set")
ANSWERS_MAX = 600  # characters of a record's `answers` text
_EVIDENCE = {"view": "rows", "topic_plan": "documents", "insight": "subjects", "summary_set": "documents"}


def _record(kind: str, id: str, domains, title: str, answers: str, *, built_at: str | None, open: str,
            anchor: dict | None = None, stale: bool = False, stale_reasons=(), status: str = "ok",
            detail: dict | None = None) -> dict:
    return {
        "kind": kind, "id": id, "domains": sorted(set(domains)), "title": title,
        "answers": answers[:ANSWERS_MAX], "anchor": anchor, "built_at": built_at,
        "stale": bool(stale), "stale_reasons": list(stale_reasons), "evidence_kind": _EVIDENCE[kind],
        "open": open, "status": status, "detail": detail or {},
    }


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


# ---------------------------------------------------------------- views

def view_assets(domain: str) -> list[dict]:
    """Saved views related to `domain`; a folder that fails to load is listed `status: invalid`."""
    from artmind.kg_views import store

    good, invalid = store.list_views([domain])
    out = []
    for loaded in good:
        spec = loaded.spec
        out.append(_record(
            "view", spec.name, spec.domains, spec.name,
            " ".join([spec.summary, *spec.examples]), built_at=_iso_mtime(loaded.path / store.VIEW_FILE),
            open=f"artmind query views show {spec.name} --compact",
            detail={"version": spec.version,
                    "params": {n: {"type": p.type, **({"entity_class": p.entity_class} if p.entity_class else {})}
                               for n, p in spec.params.items()}},
        ))
    for bad in invalid:
        out.append(_record(
            "view", bad["name"], [], bad["name"], "", built_at=None,
            open=f"artmind views validate {bad['name']} --compact", status="invalid",
            detail={"error": bad["error"]},
        ))
    return out


# ---------------------------------------------------------------- topic plans

def plan_assets(domain: str) -> list[dict]:
    """Topic plans over `domain` (spec 2026-11-10 §5.2), from the plan files; a folder that
    fails to load is listed `status: invalid`. Pages are never assets: agents run plans."""
    from artmind.topics import freshness, plan_store

    good, invalid = plan_store.list_plans([domain])
    out = []
    for loaded in good:
        spec = loaded.spec
        fresh = freshness.stored(spec.slug) or {}
        status = fresh.get("status")
        out.append(_record(
            "topic_plan", spec.slug, spec.domains, spec.topic, " ".join([spec.topic, *spec.subjects]),
            built_at=(spec.built or {}).get("at"), open=f"artmind query topics plan {spec.slug} --compact",
            stale=status == "stale", stale_reasons=fresh.get("reasons") or [],
            detail={"version": spec.version, "subjects": list(spec.subjects),
                    "slices": {"by": spec.by, "split": spec.split}, "freshness": status},
        ))
    for bad in invalid:
        out.append(_record("topic_plan", bad["slug"], [], bad["slug"], "", built_at=None,
                           open=f"artmind topic plan validate {bad['slug']} --compact", status="invalid",
                           detail={"error": bad["error"]}))
    return out


# ---------------------------------------------------------------- insights

def insight_assets(domain: str, counts: dict | None = None) -> list[dict]:
    """One record per detector with findings in the latest snapshot of `domain`.

    A snapshot is stale when the graph now holds a different number of entities or
    documents than the run recorded (`counts`: the live numbers)."""
    from artmind.insights import store
    from artmind.insights.model import InsightsError

    try:
        run = store.load_latest(domain)
    except (InsightsError, OSError, ValueError) as exc:
        logger.warning("assets: insights snapshot of {} unreadable: {}", domain, exc)
        return []
    if run is None:
        return []
    reasons = []
    for key in ("entities", "documents"):
        if counts and key in run.fingerprint and counts.get(key) is not None and counts[key] != run.fingerprint[key]:
            reasons.append(f"{key} changed since the snapshot: {run.fingerprint[key]} -> {counts[key]}")
    by_detector: dict = {}
    for finding in run.findings:
        if finding.diff != "gone":
            by_detector.setdefault(finding.detector, []).append(finding)
    out = []
    for name in sorted(by_detector):
        found = sorted(by_detector[name], key=lambda f: -f.score)
        out.append(_record(
            "insight", name, [domain], name.replace("_", " "),
            f"{name.replace('_', ' ')}: " + "; ".join(f.title for f in found[:3]), built_at=run.created_at,
            open=f"artmind query insights list --domain {domain} --detector {name} --compact",
            stale=bool(reasons), stale_reasons=reasons,
            detail={"detector": name, "findings": len(found), "as_of": run.as_of},
        ))
    return out


# ---------------------------------------------------------------- summary sets

def summary_set_assets(domain: str, now: str | None = None) -> list[dict]:
    """One record: how many of the domain's live documents have a stored summary."""
    from artmind.catalogue.parts import build_documents

    records = build_documents(domain)["doc_records"]
    if not records:
        return []
    with_summary = sum(1 for r in records if r.has_summary)
    missing = len(records) - with_summary
    return [_record(
        "summary_set", domain, [domain], f"{domain} document summaries",
        f"Stored summaries of the {len(records)} documents in {domain}.",
        built_at=now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        open=f"artmind query graph pattern10 --domain {domain} --documentName <name> --compact",
        stale=missing > 0, stale_reasons=[f"{missing} document(s) without a summary"] if missing else [],
        detail={"documents": len(records), "with_summary": with_summary},
    )]


def build_assets(domain: str) -> dict:
    """The catalogue's `assets` part for one domain."""
    from artmind.catalogue import cache

    counts = None
    try:
        fp = cache.fingerprints([domain])[domain]
        counts = {"entities": fp[1], "documents": fp[2]}
    except Exception as exc:
        logger.warning("assets: graph counts unavailable for {}: {}", domain, exc)
    records = [*view_assets(domain), *plan_assets(domain), *insight_assets(domain, counts),
               *summary_set_assets(domain)]
    return {"assets": tuple(records)}


# ---------------------------------------------------------------- listing

def list_assets(domains, kinds=None, stale_only: bool = False, limit: int | None = None) -> tuple:
    """(records, rows_total): the assets of every domain, merged by (kind, id) with the
    union of their domains, sorted by kind then id. `limit` caps the rows, not the total."""
    from artmind.catalogue import cache

    kinds = _check_kinds(kinds)
    cats, _ = cache.get(domains, ("assets",))
    merged: dict = {}
    for d in domains:
        for rec in cats[d].assets or ():
            if kinds and rec["kind"] not in kinds:
                continue
            key = (rec["kind"], rec["id"])
            if key in merged:
                merged[key] = {**merged[key], "domains": sorted(set(merged[key]["domains"]) | set(rec["domains"]))}
            else:
                merged[key] = rec
    rows = [r for _, r in sorted(merged.items(), key=lambda kv: (KINDS.index(kv[0][0]), kv[0][1]))
            if not stale_only or r["stale"]]
    return (rows[:limit] if limit else rows), len(rows)


def _check_kinds(kinds) -> tuple:
    kinds = tuple(kinds or ())
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown asset kind(s) {', '.join(unknown)}; available: {', '.join(KINDS)}")
    return kinds


# ---------------------------------------------------------------- similarity

def _embed_model() -> str:
    from utils.functions import load_env

    return load_env().get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")


def _embed_query(text: str) -> list[float]:
    from artmind.extraction import embed_query

    return embed_query(_embed_model(), text)


def _embed_document(text: str) -> list[float]:
    from artmind.extraction import embed_document

    return embed_document(_embed_model(), text)


def _embed_key() -> str:
    from artmind.extraction import embed_key

    return embed_key(_embed_model())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def like(text: str, domains, kinds=None, k: int = 5, cache=None) -> list[tuple]:
    """[(asset, score)], best first: the `k` assets whose `answers` text is closest in meaning to
    `text`. Asset vectors come from the embedding cache (a second call embeds only `text`);
    invalid assets and ones with no `answers` text are never matched."""
    from artmind.embedding_cache import EmbeddingCache, cached_embed

    rows, _ = list_assets(domains, kinds)
    rows = [r for r in rows if r["status"] == "ok" and r["answers"]]
    if not rows or k < 1:
        return []
    cache = cache if cache is not None else EmbeddingCache()
    key = _embed_key()
    query = _embed_query(text)
    scored = [(r, _cosine(query, cached_embed(cache, key, r["answers"], _embed_document))) for r in rows]
    scored.sort(key=lambda pair: (-pair[1], pair[0]["kind"], pair[0]["id"]))
    return scored[:k]
