"""Shape profile, effective kind and anchor inference (spec 2026-10-17 §4.2, §4.4)."""

from __future__ import annotations

import datetime
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from artmind.insights.config import InsightsConfig
from artmind.insights.model import InsightsError
from artmind.insights.projections import SKIP_PROPERTY_KEYS
from artmind.insights.stats import period_of

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EFFECTIVE_KIND_MIN_ENTITIES = 20
ANCHOR_COVERAGE = 0.8
ANCHOR_MIN_DAYS = 5
MAX_RELATION_TARGETS = 2000


@dataclass(frozen=True)
class EntityRow:
    _id: str
    name: str
    cls: str
    type: str | None
    days: tuple[str, ...]  # sorted distinct ISO dates
    docs: int


@dataclass
class ClassProfile:
    name: str
    declared_kind: str | None
    effective_kind: str
    entities: int
    median_days: float
    single_day_share: float
    type_counts: dict[str, int]
    properties: dict[str, dict] = field(default_factory=dict)


@dataclass
class Profile:
    domain: str
    classes: dict[str, ClassProfile]
    relations: list[dict]
    docs_per_period: dict[str, int]
    default_time_share: float
    latest_doc: str | None
    dated_days: int
    open_conflicts: list[dict]

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "classes": {name: asdict(cp) for name, cp in sorted(self.classes.items())},
            "relations": [{k: v for k, v in r.items() if k != "targets"} for r in self.relations],
            "docs_per_period": self.docs_per_period,
            "default_time_share": self.default_time_share,
            "latest_doc": self.latest_doc,
            "dated_days": self.dated_days,
            "open_conflicts": self.open_conflicts,
        }


def _day(value) -> str | None:
    text = str(value)[:10] if value is not None else ""
    if not _ISO_DAY.match(text):
        return None
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return None
    return text


def _iso_days(values) -> tuple[str, ...]:
    return tuple(sorted({d for d in (_day(v) for v in values or []) if d}))


def effective_kind(declared: str | None, median_days: float, single_share: float, entities: int) -> str:
    """The declared kind unless the graph contradicts it (spec §4.2)."""
    declared = declared or "recurrent"
    if entities < EFFECTIVE_KIND_MIN_ENTITIES:
        return declared
    if declared == "recurrent" and median_days <= 1 and single_share >= 0.8:
        return "occurrent"
    if declared == "occurrent" and median_days >= 3:
        return "recurrent"
    return declared


def summarise_class(name: str, declared: str | None, rows: list[EntityRow], properties: dict | None) -> ClassProfile:
    dated_rows = [r for r in rows if r.days]
    counts = [len(r.days) for r in dated_rows]
    median = float(statistics.median(counts)) if counts else 0.0
    single = sum(1 for c in counts if c <= 1) / len(counts) if counts else 0.0
    types = Counter(r.type.strip().lower() for r in rows if r.type and r.type.strip())
    props = {k: dict(v) for k, v in (properties or {}).items()}
    for stats in props.values():
        stats["filled_rate"] = round(stats.get("filled", 0) / len(rows), 4) if rows else 0.0
    return ClassProfile(
        name=name, declared_kind=declared, effective_kind=effective_kind(declared, median, single, len(dated_rows)),
        entities=len(rows), median_days=median, single_day_share=round(single, 4),
        type_counts=dict(types.most_common(15)), properties=props,
    )


def declared_triples(schema: dict) -> set[tuple[str, str, str]]:
    """{(REL_TYPE, SRC, DST)} from every class's `relates_to`; verbs may carry '(properties: ...)'."""
    out: set[tuple[str, str, str]] = set()
    for src, decl in (schema.get("entity_types") or {}).items():
        for dst, verbs in ((decl or {}).get("relates_to") or {}).items():
            if isinstance(verbs, str):
                continue
            for verb in verbs or []:
                token = re.split(r"[\s(]", str(verb).strip(), maxsplit=1)[0]
                if token:
                    out.add((re.sub(r"[^A-Za-z0-9_]", "_", token).upper(), src, dst))
    return out


def build_profile(projector, schema: dict, config: InsightsConfig) -> tuple[Profile, list[EntityRow]]:
    entities = sorted(
        (
            EntityRow(
                _id=r["_id"], name=r.get("name") or r["_id"], cls=r.get("cls") or "UNKNOWN",
                type=r.get("type") if isinstance(r.get("type"), str) else None,
                days=_iso_days(r.get("days")), docs=int(r.get("docs") or 0),
            )
            for r in projector.rows("entity_evidence")
        ),
        key=lambda e: e._id,
    )
    props: dict[str, dict] = defaultdict(dict)
    for r in projector.rows("property_stats", skip_keys=list(SKIP_PROPERTY_KEYS)):
        props[r.get("cls") or "UNKNOWN"][r["property"]] = {
            "filled": int(r.get("filled") or 0),
            "distinct": int(r.get("distinct_values") or 0),
            "sample": sorted(str(s) for s in r.get("sample") or [] if s is not None),
        }
    by_cls: dict[str, list[EntityRow]] = defaultdict(list)
    for e in entities:
        by_cls[e.cls].append(e)
    decl = schema.get("entity_types") or {}
    classes = {
        cls: summarise_class(cls, (decl.get(cls) or {}).get("kind"), rows, props.get(cls))
        for cls, rows in sorted(by_cls.items())
    }

    docs = projector.rows("documents")
    dated = [
        d for d in (_day(row.get("valid_from")) for row in docs if row.get("time_source") != "default_ingestion") if d
    ]
    defaulted = sum(1 for row in docs if not _day(row.get("valid_from")) or row.get("time_source") == "default_ingestion")
    per_period = Counter(period_of(d, config.period) for d in dated)

    declared = declared_triples(schema)
    relations = sorted(
        (
            {"rel": r["rel"], "src": r["src"], "dst": r["dst"], "n": int(r["n"]),
             "declared": (r["rel"], r["src"], r["dst"]) in declared, "targets": list(r.get("targets") or [])}
            for r in projector.rows("relation_triples", max_targets=MAX_RELATION_TARGETS)
        ),
        key=lambda r: (-r["n"], str(r["rel"]), str(r["src"]), str(r["dst"])),
    )
    conflicts = sorted(
        ({"cls": r.get("cls"), "property": r.get("property"), "n": int(r["n"])} for r in projector.rows("open_conflicts")),
        key=lambda r: (-r["n"], str(r["cls"]), str(r["property"])),
    )
    profile = Profile(
        domain=config.domain, classes=classes, relations=relations,
        docs_per_period=dict(sorted(per_period.items())),
        default_time_share=round(defaulted / len(docs), 4) if docs else 1.0,
        latest_doc=max(dated) if dated else None, dated_days=len(set(dated)), open_conflicts=conflicts,
    )
    return profile, entities


def infer_anchors(entities: list[EntityRow], config: InsightsConfig, dated_days: int) -> list[dict]:
    """Config first, then a single PERSON of type `self`, then >= 80% day coverage (spec §4.4)."""
    if config.anchors:
        out = []
        for ref in config.anchors:
            if ref.startswith("@"):
                match = [e for e in entities if e._id == ref[1:]]
            else:
                match = [e for e in entities if str(e.name).casefold() == ref.casefold()]
            if len(match) != 1:
                raise InsightsError(f"anchor {ref!r} matched {len(match)} entities in {config.domain}; use @<_id>")
            out.append({"_id": match[0]._id, "name": match[0].name, "rule": "config"})
        return out
    selves = [e for e in entities if e.cls == "PERSON" and (e.type or "").strip().lower() == "self"]
    if len(selves) == 1:
        return [{"_id": selves[0]._id, "name": selves[0].name, "rule": "type=self"}]
    if dated_days < ANCHOR_MIN_DAYS:
        return []
    return [
        {"_id": e._id, "name": e.name, "rule": "coverage"}
        for e in entities if len(e.days) / dated_days >= ANCHOR_COVERAGE
    ]
