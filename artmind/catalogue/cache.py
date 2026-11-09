"""The catalogue's memory + disk cache, per part (spec 2026-11-02 §4).

A cheap fingerprint query (`fingerprints`) says whether a domain changed; each part
then compares its own key (`parts.part_key`) and rebuilds alone when it moved, so a
cheap part never waits for an expensive one. Memory serves a domain checked within
`FINGERPRINT_TTL_S`; the disk copy (`logs/plan-catalog/<domain>.json`, atomic, format
`CATALOGUE_FORMAT`) serves a fresh process. One build per domain at a time.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger

from artmind import graph_query
from artmind.catalogue import parts as parts_mod
from artmind.catalogue import stamp as stamps
from artmind.catalogue.model import CATALOGUE_FORMAT, PART_FIELDS, DomainCatalogue
from artmind.graph_query import normalize_domains
from artmind.domain_config import is_domain_name

FINGERPRINT_TTL_S = 10.0
MAX_AGE_S = 3600.0  # the safety net: the generation and disk stamp are the freshness check

#: What `get` loads when no parts are named: the four the planner reads.
DEFAULT_PARTS = ("classes", "relations", "documents", "names")

_FINGERPRINT_CYPHER = """
UNWIND $domains AS dom
CALL (dom) {
  RETURN COUNT { (e:Entity) WHERE (e._domain = dom OR e._domain STARTS WITH (dom + '.')) } AS entities,
         COUNT { (d:Document) WHERE (d._domain = dom OR d._domain STARTS WITH (dom + '.')) } AS documents,
         COUNT { (h:DocumentHistory) WHERE (h._domain = dom OR h._domain STARTS WITH (dom + '.')) } AS history
}
CALL (dom) {
  OPTIONAL MATCH (s:DomainState) WHERE s._domain = dom OR s._domain STARTS WITH (dom + '.')
  RETURN coalesce(sum(s.generation), 0) AS generation
}
RETURN dom AS domain, generation, entities, documents, history
"""


def fingerprints(domains) -> dict:
    """(generation, entities, documents, history, disk stamp) per domain (spec
    2026-11-02 §3). The generation is exact for every change artmind makes; the
    counts catch a writer outside the choke points; the disk stamp covers views,
    topic pages, insight snapshots and schemas."""
    domains = normalize_domains(domains)
    rows = graph_query._run_read_query(_FINGERPRINT_CYPHER, {"domains": domains})
    out = {}
    for row in rows:
        out[row["domain"]] = (
            int(row.get("generation") or 0), int(row.get("entities") or 0),
            int(row.get("documents") or 0), int(row.get("history") or 0),
            stamps.disk_stamp(row["domain"]),
        )
    for d in domains:
        out.setdefault(d, (0, 0, 0, 0, stamps.disk_stamp(d)))
    return out


def available_parts() -> tuple:
    return tuple(p for p in PART_FIELDS if p in parts_mod.BUILDERS)


def _check_parts(parts) -> tuple:
    parts = tuple(parts)
    unknown = [p for p in parts if p not in parts_mod.BUILDERS]
    if unknown:
        raise ValueError(f"unknown catalogue part(s) {', '.join(unknown)}; available: {', '.join(available_parts())}")
    return parts


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty(domain: str, fingerprint: tuple) -> DomainCatalogue:
    return DomainCatalogue(domain=domain, fingerprint=tuple(fingerprint), built_at=_now_iso())


def build_part(domain: str, part: str, fingerprint: tuple) -> tuple:
    """(fields, key) of one part, built from the graph and files now."""
    return parts_mod.BUILDERS[part](domain), parts_mod.part_key(part, fingerprint)


def _with_parts(cat: DomainCatalogue, fingerprint: tuple, built: dict) -> DomainCatalogue:
    """`cat` with the freshly built parts merged in (`built`: part -> (fields, key))."""
    fields: dict = {}
    keys = dict(cat.part_keys)
    stamped = dict(cat.part_built)
    now = _now_iso()
    for part, (values, key) in built.items():
        fields.update(values)
        keys[part] = key
        stamped[part] = now
    return replace(cat, fingerprint=tuple(fingerprint), built_at=now, part_keys=keys, part_built=stamped, **fields)


def build(domain: str, fingerprint: tuple | None = None, parts=DEFAULT_PARTS) -> DomainCatalogue:
    """A catalogue of `parts` built live, ignoring every cache."""
    parts = _check_parts(parts)
    if fingerprint is None:
        fingerprint = fingerprints([domain])[domain]
    built = {p: build_part(domain, p, fingerprint) for p in parts}
    return _with_parts(_empty(domain, fingerprint), fingerprint, built)


# ---------------------------------------------------------------- cache

_lock = threading.Lock()
_memory: dict = {}  # domain -> [catalogue, {part: built_mono}, checked_mono]
_clock: Callable[[], float] = time.monotonic
_domain_locks: dict = {}  # domain -> Lock, so one domain's build never blocks another's


def _domain_lock(domain: str) -> threading.Lock:
    with _lock:
        return _domain_locks.setdefault(domain, threading.Lock())


def reset() -> None:
    global _clock
    with _lock:
        _memory.clear()
        _domain_locks.clear()
    _clock = time.monotonic


def set_clock(clock: Callable[[], float]) -> None:
    global _clock
    _clock = clock


def _enabled() -> bool:
    return os.environ.get("ARTMIND_PLAN_CATALOG", "1") != "0"


def _disk_path(domain: str) -> Path | None:
    if not is_domain_name(domain):
        return None
    import paths

    return Path(paths.LOGS_DIR) / "plan-catalog" / f"{domain}.json"


def _read_disk(domain: str) -> DomainCatalogue | None:
    path = _disk_path(domain)
    if path is None or not path.is_file():
        return None
    try:
        cat = DomainCatalogue.from_json(json.loads(path.read_text(encoding="utf-8")))
        return cat if cat.domain == domain else None
    except Exception as exc:
        logger.warning("catalogue: ignoring unreadable cache {}: {}", path.name, exc)
        return None


def _write_disk(cat: DomainCatalogue) -> None:
    path = _disk_path(cat.domain)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{cat.domain}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cat.to_json(), fh, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except Exception as exc:
        logger.warning("catalogue: could not write cache for {}: {}", cat.domain, exc)


def _age_s(built_at: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(built_at)).total_seconds()
    except ValueError:
        return float("inf")


def _summary_source(sources: dict) -> str:
    values = set(sources.values())
    return "live" if "live" in values else "disk" if "disk" in values else "memory"


def health_state(cat: DomainCatalogue | None, now_age: Callable[[str], float] = _age_s) -> str:
    """`absent`, `stale` (older than `ARTMIND_CATALOGUE_HEALTH_TTL_S`) or `fresh`."""
    if cat is None or not cat.has("health"):
        return "absent"
    from artmind.catalogue import health

    built = cat.part_built.get("health", cat.built_at)
    return "stale" if now_age(built) >= health.health_ttl_s() else "fresh"


def _merge_built(d: str, fps: dict, built: dict, now: float, origin_cat: DomainCatalogue | None,
                 built_at: dict) -> DomainCatalogue:
    cat = origin_cat or _empty(d, fps[d])
    for p in built:
        built_at[p] = now
    cat = _with_parts(cat, fps[d], built)
    _write_disk(cat)
    return cat


def get_detailed(domains, parts=DEFAULT_PARTS) -> tuple:
    """(catalogues by domain, source by domain and part: memory|disk|live|absent).

    `health` is never built here: it is served as cached (memory or disk) and reported
    `absent` when there is none. A changed generation, an absent or an expired `health`
    schedules a background rebuild, which does nothing outside the long-lived servers."""
    domains = normalize_domains(domains)
    parts = _check_parts(parts)
    build_parts = tuple(p for p in parts if p != "health")
    want_health = "health" in parts
    if not _enabled():
        fps = fingerprints(domains)
        cats = {d: build(d, fps[d], build_parts) for d in domains}
        return cats, {d: {**{p: "live" for p in build_parts}, **({"health": "absent"} if want_health else {})}
                      for d in domains}
    now = _clock()
    cats: dict = {}
    sources: dict = {}
    pending = []
    with _lock:  # memory reads only: a domain checked recently is served without asking
        for d in domains:
            entry = _memory.get(d)
            if (entry and now - entry[2] < FINGERPRINT_TTL_S
                    and all(entry[0].has(p) and now - entry[1].get(p, -MAX_AGE_S) < MAX_AGE_S for p in build_parts)):
                cats[d] = entry[0]
                sources[d] = {p: "memory" for p in build_parts}
                if want_health:
                    sources[d]["health"] = "memory" if entry[0].has("health") else "absent"
            else:
                pending.append(d)
    if pending:
        fps = fingerprints(pending)  # a query: never under a lock
        for d in pending:
            with _domain_lock(d):  # single flight: a second caller waits, then finds the first one's build
                with _lock:
                    entry = _memory.get(d)
                if entry:
                    cat, built_at, origin = entry[0], dict(entry[1]), "memory"
                else:
                    cat, origin = _read_disk(d), "disk"
                    built_at = {p: now - _age_s(cat.part_built.get(p, cat.built_at)) for p in cat.part_keys} if cat else {}
                before_generation = cat.fingerprint[0] if cat else None
                had_health = health_state(cat)
                got: dict = {}
                built: dict = {}
                for p in build_parts:
                    fresh = (cat is not None and cat.part_keys.get(p) == parts_mod.part_key(p, fps[d])
                             and now - built_at.get(p, -MAX_AGE_S) < MAX_AGE_S)
                    if fresh:
                        got[p] = origin
                    else:
                        built[p] = build_part(d, p, fps[d])
                        got[p] = "live"
                if built:
                    cat = _merge_built(d, fps, built, now, cat, built_at)
                else:
                    cat = replace(cat or _empty(d, fps[d]), fingerprint=tuple(fps[d]))
                if want_health:
                    got["health"] = origin if cat.has("health") else "absent"
                with _lock:
                    _memory[d] = [cat, built_at, now]
                cats[d], sources[d] = cat, got
                if (before_generation is not None and before_generation != fps[d][0]) or had_health != "fresh":
                    _schedule_health(d)
    return cats, sources


def _schedule_health(domain: str) -> None:
    from artmind.catalogue import refresh

    refresh.schedule_health([domain])


def rebuild(domains, parts, *, force: bool = True) -> tuple:
    """Build `parts` now (the one place `health` is built) and store them in memory and on disk.
    Returns (catalogues by domain, parts built by domain)."""
    domains = normalize_domains(domains)
    parts = _check_parts(parts)
    fps = fingerprints(domains)
    cats: dict = {}
    done: dict = {}
    now = _clock()
    for d in domains:
        with _domain_lock(d):
            with _lock:
                entry = _memory.get(d)
            cat = entry[0] if entry else _read_disk(d)
            built_at = dict(entry[1]) if entry else (
                {p: now - _age_s(cat.part_built.get(p, cat.built_at)) for p in cat.part_keys} if cat else {})
            todo = [p for p in parts if force or cat is None or cat.part_keys.get(p) != parts_mod.part_key(p, fps[d])]
            built = {p: build_part(d, p, fps[d]) for p in todo}
            if built:
                cat = _merge_built(d, fps, built, now, cat, built_at)
                with _lock:
                    _memory[d] = [cat, built_at, now]
            cats[d], done[d] = cat or _empty(d, fps[d]), todo
    return cats, done


def cached(domain: str) -> DomainCatalogue | None:
    """The memory or disk copy of `domain`'s catalogue, or None. Reads, never builds."""
    with _lock:
        entry = _memory.get(domain)
    return entry[0] if entry else _read_disk(domain)


def stale_reason(cat: DomainCatalogue | None, part: str, fingerprint: tuple) -> str | None:
    """Why `part` of `cat` would be rebuilt now, or None when it is current."""
    if cat is None or not cat.has(part):
        return "not built"
    built = cat.part_built.get(part, cat.built_at)
    if part == "health":
        return {"absent": "not built", "stale": "older than the health TTL"}.get(health_state(cat))
    if cat.part_keys.get(part) != parts_mod.part_key(part, fingerprint):
        return "inputs changed"
    return "older than the maximum age" if _age_s(built) >= MAX_AGE_S else None


def get(domains, parts=DEFAULT_PARTS) -> tuple:
    """(catalogues by domain, source by domain: memory|disk|live)."""
    cats, detail = get_detailed(domains, parts)
    return cats, {d: _summary_source(s) for d, s in detail.items()}
