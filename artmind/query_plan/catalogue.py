"""The per-domain catalogue for `query plan` (spec 2026-10-22 §11).

A few deterministic Neo4j reads (classes, relationship types, document names, entity
names and aliases) plus the schema's class descriptions, cached in memory and on disk
and revalidated by a cheap fingerprint query. Contents are sorted, so a plan built from
a cached catalogue equals one built live.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger

from artmind import graph_query
from artmind.graph_query import domain_predicate, normalize_domains
from artmind.query_plan.config import is_domain_name

CATALOGUE_FORMAT = 2  # bump when the cached JSON gains or changes fields
NAME_INDEX_MAX = 20_000
RELATIONS_MAX = 50
FINGERPRINT_TTL_S = 10.0
MAX_AGE_S = 900.0

_FINGERPRINT_CYPHER = """
UNWIND $domains AS dom
CALL (dom) {
  RETURN COUNT { (e:Entity) WHERE (e._domain = dom OR e._domain STARTS WITH (dom + '.')) } AS entities,
         COUNT { (d:Document) WHERE (d._domain = dom OR d._domain STARTS WITH (dom + '.')) } AS documents,
         COUNT { (h:DocumentHistory) WHERE (h._domain = dom OR h._domain STARTS WITH (dom + '.')) } AS history
}
OPTIONAL MATCH (s:ProjectionState {id: 'singleton'})
RETURN dom AS domain, entities, documents, history, s.last_rebuilt_at AS rebuilt
"""

_CLASSES_CYPHER = f"""
MATCH (e:Entity)
WHERE {domain_predicate("e")} AND e.entity_class IS NOT NULL
RETURN e.entity_class AS name, count(e) AS n
ORDER BY n DESC, name
"""

_RELATIONS_CYPHER = f"""
MATCH (a:Entity)-[r:RELATES_TO]->(:Entity)
WHERE {domain_predicate("a")} AND r.rel_type IS NOT NULL
RETURN r.rel_type AS rel_type, count(r) AS n
ORDER BY n DESC, rel_type
LIMIT $limit
"""

_DOCUMENTS_CYPHER = f"""
MATCH (d:Document|DocumentHistory)
WHERE {domain_predicate("d")}
RETURN DISTINCT d.name AS name
"""

_NAMES_CYPHER = f"""
MATCH (e:Entity)
WHERE {domain_predicate("e")} AND e.name IS NOT NULL
RETURN e._id AS _id, e.name AS name, e.aliases AS aliases,
       e.entity_class AS entity_class, coalesce(e._observation_count, 0) AS observation_count,
       e._domain AS domain, COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} AS connection_count
ORDER BY observation_count DESC, name, _id
LIMIT $limit
"""


@dataclass(frozen=True)
class NameEntry:
    _id: str
    name: str
    aliases: tuple
    entity_class: str
    observation_count: int
    domain: str = ""
    connection_count: int = 0


@dataclass(frozen=True)
class DomainCatalogue:
    domain: str
    fingerprint: tuple
    built_at: str
    classes: tuple  # (name, count, description)
    relations: tuple  # (rel_type, count)
    documents: tuple
    names: tuple
    truncated: bool

    def to_json(self) -> dict:
        return {
            "format": CATALOGUE_FORMAT,
            "domain": self.domain,
            "fingerprint": list(self.fingerprint),
            "built_at": self.built_at,
            "classes": [list(c) for c in self.classes],
            "relations": [list(r) for r in self.relations],
            "documents": list(self.documents),
            "names": [
                {"_id": n._id, "name": n.name, "aliases": list(n.aliases),
                 "entity_class": n.entity_class, "observation_count": n.observation_count,
                 "domain": n.domain, "connection_count": n.connection_count}
                for n in self.names
            ],
            "truncated": self.truncated,
        }

    @classmethod
    def from_json(cls, data: dict) -> "DomainCatalogue":
        if data.get("format") != CATALOGUE_FORMAT:
            raise ValueError(f"cache format {data.get('format')!r}, expected {CATALOGUE_FORMAT}")
        return cls(
            domain=str(data["domain"]),
            fingerprint=tuple(data["fingerprint"]),
            built_at=str(data["built_at"]),
            classes=tuple((str(c[0]), int(c[1]), str(c[2] or "")) for c in data["classes"]),
            relations=tuple((str(r[0]), int(r[1])) for r in data["relations"]),
            documents=tuple(str(d) for d in data["documents"]),
            names=tuple(
                NameEntry(
                    _id=n["_id"], name=n["name"], aliases=tuple(n.get("aliases") or ()),
                    entity_class=n.get("entity_class") or "",
                    observation_count=int(n.get("observation_count") or 0),
                    domain=n.get("domain") or "", connection_count=int(n.get("connection_count") or 0),
                )
                for n in data["names"]
            ),
            truncated=bool(data["truncated"]),
        )


def fingerprints(domains) -> dict:
    domains = normalize_domains(domains)
    rows = graph_query._run_read_query(_FINGERPRINT_CYPHER, {"domains": domains})
    out = {}
    for row in rows:
        out[row["domain"]] = (
            int(row.get("entities") or 0), int(row.get("documents") or 0),
            int(row.get("history") or 0), str(row.get("rebuilt") or ""),
        )
    for d in domains:
        out.setdefault(d, (0, 0, 0, ""))
    return out


def _class_descriptions(domain: str) -> dict:
    try:
        from artmind import temporal

        schema = temporal.load_schema(domain) or {}
        types = schema.get("entity_types") or {}
        return {str(k): " ".join(str((v or {}).get("description") or "").split()) for k, v in types.items()}
    except Exception as exc:  # a missing or broken schema must not stop planning
        logger.debug("plan catalogue: no schema descriptions for {}: {}", domain, exc)
        return {}


def build(domain: str, fingerprint: tuple | None = None) -> DomainCatalogue:
    params = {"domains": [domain]}
    run = graph_query._run_read_query
    if fingerprint is None:
        fingerprint = fingerprints([domain])[domain]
    descriptions = _class_descriptions(domain)
    classes = tuple(
        (r["name"], int(r["n"]), descriptions.get(r["name"], ""))
        for r in run(_CLASSES_CYPHER, params)
    )
    relations = tuple(
        (r["rel_type"], int(r["n"]))
        for r in run(_RELATIONS_CYPHER, {**params, "limit": RELATIONS_MAX})
    )
    documents = tuple(sorted({r["name"] for r in run(_DOCUMENTS_CYPHER, params) if r.get("name")}))
    name_rows = run(_NAMES_CYPHER, {**params, "limit": NAME_INDEX_MAX + 1})
    truncated = len(name_rows) > NAME_INDEX_MAX
    names = tuple(
        NameEntry(
            _id=r.get("_id"), name=r["name"],
            aliases=tuple(a for a in (r.get("aliases") or ()) if a),
            entity_class=r.get("entity_class") or "",
            observation_count=int(r.get("observation_count") or 0),
            domain=r.get("domain") or "", connection_count=int(r.get("connection_count") or 0),
        )
        for r in name_rows[:NAME_INDEX_MAX]
    )
    return DomainCatalogue(
        domain=domain, fingerprint=tuple(fingerprint),
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        classes=classes, relations=relations, documents=documents, names=names, truncated=truncated,
    )


# ---------------------------------------------------------------- cache

_lock = threading.Lock()
_memory: dict = {}  # domain -> [catalogue, built_mono, checked_mono]
_clock: Callable[[], float] = time.monotonic


def reset() -> None:
    global _clock
    with _lock:
        _memory.clear()
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
        logger.warning("plan catalogue: ignoring unreadable cache {}: {}", path.name, exc)
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
        logger.warning("plan catalogue: could not write cache for {}: {}", cat.domain, exc)


def _age_s(cat: DomainCatalogue) -> float:
    try:
        built = datetime.fromisoformat(cat.built_at)
        return (datetime.now(timezone.utc) - built).total_seconds()
    except ValueError:
        return float("inf")


def get(domains) -> tuple:
    """(catalogues by domain, source by domain: memory|disk|live)."""
    domains = normalize_domains(domains)
    if not _enabled():
        fps = fingerprints(domains)
        return {d: build(d, fps[d]) for d in domains}, {d: "live" for d in domains}
    now = _clock()
    cats: dict = {}
    sources: dict = {}
    with _lock:
        # Domains whose last fingerprint check is recent are served without asking.
        pending = []
        for d in domains:
            entry = _memory.get(d)
            if entry and now - entry[2] < FINGERPRINT_TTL_S and now - entry[1] < MAX_AGE_S:
                cats[d], sources[d] = entry[0], "memory"
            else:
                pending.append(d)
        if pending:
            fps = fingerprints(pending)
            for d in pending:
                entry = _memory.get(d)
                if entry and entry[0].fingerprint == fps[d] and now - entry[1] < MAX_AGE_S:
                    entry[2] = now
                    cats[d], sources[d] = entry[0], "memory"
                    continue
                disk = _read_disk(d) if entry is None else None
                if disk and disk.fingerprint == fps[d] and _age_s(disk) < MAX_AGE_S:
                    _memory[d] = [disk, now - _age_s(disk), now]
                    cats[d], sources[d] = disk, "disk"
                    continue
                cat = build(d, fps[d])
                _memory[d] = [cat, now, now]
                _write_disk(cat)
                cats[d], sources[d] = cat, "live"
    return cats, sources
