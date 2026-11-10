"""The catalogue's data shapes and their JSON form (spec 2026-11-02 §4).

`DomainCatalogue` has one attribute per part. A part is *loaded* when its name is
in `part_keys`; the key records what the part was built from, so each part is
revalidated on its own (`cache`). `CATALOGUE_FORMAT` is bumped when the cached
JSON gains or changes fields: an older file is discarded, never migrated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CATALOGUE_FORMAT = 5

#: The parts, in display order, and the DomainCatalogue fields each one fills.
PART_FIELDS: dict[str, tuple[str, ...]] = {
    "classes": ("classes",),
    "relations": ("relations",),
    "shapes": ("shapes",),
    "types": ("types",),
    "documents": ("documents", "doc_records"),
    "versions": ("versions",),
    "names": ("names", "truncated"),
    "stores": ("stores",),
    "time": ("time",),
    "assets": ("assets",),
    "health": ("health",),
    "levels": ("levels",),
}


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
class DocRecord:
    """One live document of the `documents` part."""
    id: str
    name: str
    domain: str = ""
    valid_from: str | None = None
    has_summary: bool = False


@dataclass(frozen=True)
class DomainCatalogue:
    domain: str
    fingerprint: tuple
    built_at: str
    classes: tuple = ()  # (name, count, description)
    relations: tuple = ()  # (rel_type, count)
    documents: tuple = ()  # every live and retired document name, sorted
    names: tuple = ()
    truncated: bool = False
    doc_records: tuple = ()  # DocRecord per live document
    stores: dict | None = None
    time: dict | None = None
    assets: tuple | None = None
    health: dict | None = None
    # orient's parts (spec 2026-11-09), last so positional construction keeps the older fields' order
    shapes: tuple = ()  # (src_class, rel_type, dst_class, count)
    types: tuple = ()  # (class, {total, distinct, dated, top: [[value, n], ...]})
    versions: tuple = ()  # {name, status, valid_from, successor[, replaces]} per retired/replacing document
    # the granularity ladder (spec 2026-11-10 §2, §9.2): n, focused/anchor entities, passages, summaries
    levels: dict | None = None
    part_keys: dict = field(default_factory=dict)  # part -> what it was built from
    part_built: dict = field(default_factory=dict)  # part -> ISO build time

    def has(self, part: str) -> bool:
        return part in self.part_keys

    def to_json(self) -> dict:
        return {
            "format": CATALOGUE_FORMAT,
            "domain": self.domain,
            "fingerprint": list(self.fingerprint),
            "built_at": self.built_at,
            "classes": [list(c) for c in self.classes],
            "relations": [list(r) for r in self.relations],
            "shapes": [list(s) for s in self.shapes],
            "types": [[c, dict(v)] for c, v in self.types],
            "versions": [dict(v) for v in self.versions],
            "documents": list(self.documents),
            "names": [
                {"_id": n._id, "name": n.name, "aliases": list(n.aliases),
                 "entity_class": n.entity_class, "observation_count": n.observation_count,
                 "domain": n.domain, "connection_count": n.connection_count}
                for n in self.names
            ],
            "truncated": self.truncated,
            "doc_records": [
                {"id": r.id, "name": r.name, "domain": r.domain, "valid_from": r.valid_from,
                 "has_summary": r.has_summary}
                for r in self.doc_records
            ],
            "stores": self.stores,
            "time": self.time,
            "assets": None if self.assets is None else list(self.assets),
            "health": self.health,
            "levels": self.levels,
            "parts": {p: {"key": self.part_keys[p], "built_at": self.part_built.get(p, self.built_at)}
                      for p in self.part_keys},
        }

    @classmethod
    def from_json(cls, data: dict) -> "DomainCatalogue":
        if data.get("format") != CATALOGUE_FORMAT:
            raise ValueError(f"cache format {data.get('format')!r}, expected {CATALOGUE_FORMAT}")
        parts = data.get("parts") or {}
        assets = data.get("assets")
        return cls(
            domain=str(data["domain"]),
            fingerprint=tuple(data["fingerprint"]),
            built_at=str(data["built_at"]),
            classes=tuple((str(c[0]), int(c[1]), str(c[2] or "")) for c in data["classes"]),
            relations=tuple((str(r[0]), int(r[1])) for r in data["relations"]),
            shapes=tuple((str(s[0]), str(s[1]), str(s[2]), int(s[3])) for s in data.get("shapes") or ()),
            types=tuple((str(c), dict(v)) for c, v in data.get("types") or ()),
            versions=tuple(dict(v) for v in data.get("versions") or ()),
            levels=data.get("levels"),
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
            doc_records=tuple(
                DocRecord(id=str(r["id"]), name=str(r.get("name") or ""), domain=r.get("domain") or "",
                          valid_from=r.get("valid_from"), has_summary=bool(r.get("has_summary")))
                for r in data.get("doc_records") or ()
            ),
            stores=data.get("stores"),
            time=data.get("time"),
            assets=None if assets is None else tuple(assets),
            health=data.get("health"),
            part_keys={p: v["key"] for p, v in parts.items()},
            part_built={p: str(v["built_at"]) for p, v in parts.items()},
        )
