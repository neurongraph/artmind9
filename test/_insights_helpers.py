"""Synthetic fixtures for insights tests. Synthetic data only -- never real vault content."""

from datetime import date, timedelta

from artmind.insights.config import parse_config
from artmind.insights.profile import EntityRow, Profile, summarise_class


class FakeProjector:
    def __init__(self, tables: dict):
        self.tables = tables
        self.calls: list[tuple[str, dict]] = []

    def rows(self, name, **params):
        self.calls.append((name, params))
        return list(self.tables.get(name, []))


def year_days(year: int, n: int) -> list[str]:
    """n distinct ISO days spread across `year` (n <= 365)."""
    step = max(1, 365 // max(n, 1))
    return [(date(year, 1, 1) + timedelta(days=i * step)).isoformat() for i in range(n)]


def ent(_id, cls, days, name=None, type=None) -> EntityRow:
    days = tuple(sorted(set(days)))
    return EntityRow(_id=_id, name=name or _id, cls=cls, type=type, days=days, docs=len(days))


def make_ctx(entities, *, schema=None, effective=None, docs_per_period=None, config=None, anchors=(),
             as_of=None, relations=(), open_conflicts=(), properties=None, default_time_share=0.0,
             entity_props=None, anchor_relations=(), entity_edges=()):
    from artmind.insights.detectors import RunContext

    schema = schema or {}
    decl = schema.get("entity_types") or {}
    classes = {}
    for cls in sorted({e.cls for e in entities} | set(properties or {})):
        rows = [e for e in entities if e.cls == cls]
        cp = summarise_class(cls, (decl.get(cls) or {}).get("kind") or "recurrent", rows, (properties or {}).get(cls))
        if effective and cls in effective:
            cp.effective_kind = effective[cls]
        classes[cls] = cp
    all_days = sorted({d for e in entities for d in e.days})
    profile = Profile(
        domain="demo", classes=classes, relations=list(relations),
        docs_per_period=docs_per_period or {"2022": 100, "2023": 100, "2024": 100},
        default_time_share=default_time_share,
        latest_doc=as_of or (all_days[-1] if all_days else None),
        dated_days=len(all_days), open_conflicts=list(open_conflicts),
        entity_edges=list(entity_edges),
    )
    return RunContext(
        domain="demo", schema=schema, config=parse_config(config or {}, "demo"), profile=profile,
        entities=list(entities), anchors=set(anchors), as_of=profile.latest_doc,
        entity_props=dict(entity_props or {}), anchor_relations=list(anchor_relations),
    )
