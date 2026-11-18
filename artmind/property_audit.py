"""`artmind domains property-audit`: per (class, key) evidence for keeping a property (spec
2026-11-15 §3). Read-only: staging files, schema, mart declaration, insights config, saved views
and topic plans; Neo4j only with --graph. Lists key names, counts and shares -- never a value.
A family root (`banking`) is audited with its children: staging under `kg/banking/` and
`kg/banking.*/`. `depended_on` and the rows listed come from `schema_validate.family_schema`; a
key's `declared` is judged per observation against the leaf schema of its folder's domain (the
schema extraction and retime of that domain read), so a key only the root declares, observed in
`kg/banking.policy/`, is `declared: false` -- retime of `banking.policy` drops it."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from artmind.atomic_dir import is_scratch
from artmind.schema_validate import type_key
from artmind.observations import OBSERVATION_OWN_KEYS, RESERVED_PREFIX, declared_property_keys

REQUIRED_FILL = 0.95
INFERRED_DROP = 0.6
TEXT_ONLY_MAX = 0.1
RARE_FILL = 0.10
DISTINCT_CAP = 1000
_WORD = re.compile(r"[a-z0-9]+")
_BACKTICKED = re.compile(r"`(?:([A-Z][A-Z0-9_]*)\.)?([a-z][a-z0-9_]*)`")


def _words(value) -> list[str]:
    """Lower-case word tokens of longer than two characters; digit-only tokens of any length
    (a `3` or `12` is evidence too)."""
    items = value if isinstance(value, list) else [value]
    return [w for item in items for w in _WORD.findall(_text(item).lower()) if len(w) > 2 or w.isdigit()]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _normalised(value) -> str:
    """The whole value, for `distinct_values`: stripped and lower-cased; a list or dict as JSON."""
    return _text(value).strip().lower()


def _overlap(value, described: set[str], chunk: set[str]) -> tuple[int, int, int]:
    """Characters of `value` (by word) in the description/context, else only in the chunk text,
    else in neither -- spec §1.1's three shares."""
    a = b = c = 0
    for w in _words(value):
        if w in described:
            a += len(w)
        elif w in chunk:
            b += len(w)
        else:
            c += len(w)
    return a, b, c


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def document_folders(kg_dir: Path, domain: str) -> list[Path]:
    """Document staging folders of `domain` and its dotted children (also `ingest date-audit`'s)."""
    kg = Path(kg_dir)
    roots = [kg / domain, *sorted(kg.glob(f"{domain}.*"))] if kg.is_dir() else []
    return [d for root in roots if root.is_dir()
            for d in sorted(root.iterdir()) if d.is_dir() and not is_scratch(d)
            and not d.name.startswith("table__") and (d / "observations.json").exists()]


@dataclass
class _Tally:
    """What the staging scan gathers. A new audit column (e.g. the temporal plan's dates) adds its
    counters here, fills them in `_scan_observation`, and reads them in `_row`."""
    per_class: Counter = field(default_factory=Counter)
    filled: Counter = field(default_factory=Counter)
    chars: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0, 0]))
    distinct: dict = field(default_factory=lambda: defaultdict(set))
    #: {(class, key): {leaf domain}} -- folders whose own schema does not declare the key.
    undeclared_in: dict = field(default_factory=lambda: defaultdict(set))
    #: {(class, key): {leaf domain}} -- folders whose own schema declares it.
    declared_in: dict = field(default_factory=lambda: defaultdict(set))
    #: {class: Counter of normalised `type` values} -- `type` is an observation field, so the
    #: key scan skips it; this reads it for a class's declared `types:` list.
    type_values: dict = field(default_factory=lambda: defaultdict(Counter))


def _is_domain_key(key, value) -> bool:
    return not (key in OBSERVATION_OWN_KEYS or str(key).startswith(RESERVED_PREFIX) or value in (None, "", []))


def _scan_observation(obs: dict, chunk: set[str] | None, tally: _Tally,
                      leaf: tuple[str, dict[str, set[str]]] | None = None) -> None:
    """`chunk` None (no `chunk_id`): the observation counts for fill and distinct values but not
    for the overlap shares, which need its chunk text. `leaf` (folder domain, its declared keys)
    records a key that domain's own schema does not declare."""
    cls = str(obs["entity_class"])
    tally.per_class[cls] += 1
    if obs.get("type") not in (None, "", []):
        tally.type_values[cls][_normalised(obs["type"])] += 1
    context = obs.get("context") or []
    described = set(_words([obs.get("description") or "", *(context if isinstance(context, list) else [context])]))
    for key, value in obs.items():
        if not _is_domain_key(key, value):
            continue
        tally.filled[(cls, key)] += 1
        if leaf is not None:
            (tally.declared_in if key in leaf[1].get(cls, set()) else tally.undeclared_in)[(cls, key)].add(leaf[0])
        if chunk is not None:
            a, b, c = _overlap(value, described, chunk)
            acc = tally.chars[(cls, key)]
            acc[0] += a
            acc[1] += b
            acc[2] += c
        if len(tally.distinct[(cls, key)]) < DISTINCT_CAP:
            tally.distinct[(cls, key)].add(_normalised(value))


def _scan(folders: list[Path], leaf_schema: Callable[[str], dict] | None = None) -> _Tally:
    tally = _Tally()
    leaves: dict[str, dict[str, set[str]]] = {}
    for folder in folders:
        leaf = None
        if leaf_schema is not None:
            name = folder.parent.name
            if name not in leaves:
                leaves[name] = declared_property_keys(leaf_schema(name) or {})
            leaf = (name, leaves[name])
        chunk_rows = _load(folder / "chunks.json", [])
        chunks = {c.get("id"): set(_words(c.get("text") or "")) for c in chunk_rows if isinstance(c, dict)} \
            if isinstance(chunk_rows, list) else {}
        observations = _load(folder / "observations.json", [])
        for obs in observations if isinstance(observations, list) else []:
            if not isinstance(obs, dict) or obs.get("source_kind") == "table" or not obs.get("entity_class"):
                continue
            chunk_id = obs.get("chunk_id")
            _scan_observation(obs, chunks.get(chunk_id, set()) if chunk_id else None, tally, leaf)
    return tally


def _declarations(schema: dict) -> dict[str, dict[str, dict]]:
    """{class: {key: its declaration mapping}} -- keys as `declared_property_keys` reads them."""
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    types = types if isinstance(types, dict) else {}
    out = {}
    for cls, keys in declared_property_keys(schema).items():
        props = (types.get(cls) or {}).get("properties") or {}
        out[cls] = {k: (props.get(k) if isinstance(props.get(k), dict) else {}) for k in keys}
    return out


def _row(cls: str, key: str, decl: dict | None, tally: _Tally, depended_on: dict, conflicts: dict | None) -> dict:
    a, b, c = tally.chars[(cls, key)]
    total = (a + b + c) or 1
    flags: dict = {}
    if decl is not None:
        if decl.get("temporal"):
            flags["temporal"] = decl["temporal"]
        if decl.get("required") is True:
            flags["required"] = True
        if key in OBSERVATION_OWN_KEYS:
            flags["reserved"] = True
    if depended_on.get((cls, key)):
        flags["depended_on"] = sorted(depended_on[(cls, key)])
    n = tally.per_class[cls]
    undeclared_in = sorted(tally.undeclared_in.get((cls, key), ()))
    row = {"class": cls, "key": key, "declared": decl is not None and not undeclared_in,
           "fill": round(tally.filled[(cls, key)] / n, 3) if n else 0.0,
           "in_description": round(a / total, 3), "text_only": round(b / total, 3),
           "inferred": round(c / total, 3), "distinct_values": len(tally.distinct[(cls, key)]), "flags": flags}
    if len(tally.distinct[(cls, key)]) >= DISTINCT_CAP:
        row["distinct_capped"] = True
    if conflicts is not None:
        row["conflicts"] = conflicts.get((cls, key), 0)
    if decl is not None and undeclared_in:
        # Declared in the family but not by the leaf schema these observations were extracted
        # under: retime of those domains drops it.
        row["undeclared_in"] = undeclared_in
        where = ", ".join(undeclared_in)
        wanted = (decl.get("required") is True or decl.get("keep_reason") or decl.get("temporal")
                  or flags.get("depended_on"))
        if wanted:
            row["proposal"], row["reason"] = "review", f"declare it under {where}, or drop it"
        elif tally.declared_in.get((cls, key)):
            row["proposal"], row["reason"] = "review", f"not declared under {where}: retime there drops it"
        else:
            row["proposal"], row["reason"] = "drop", f"not declared under {where}"
        return row
    row["proposal"], row["reason"] = _propose(row, decl, observed=n)
    return row


#: The `ingest date-audit` columns a `temporal: valid_from` key's row carries under `dates`.
DATE_COLUMNS = ("dated_by_property", "precision", "ranges", "unparsed")


def _type_rows(schema: dict, tally: _Tally) -> list[dict]:
    """One `type` row per class that declares a closed `types:` list (spec 2026-11-15 §5.3):
    `outside_types` is how many observations carry a `type` outside it, `outside_distinct` how
    many different values those are (counts only, never the values), `fill` the share of the
    class's observations with a `type` at all. Always `keep`: it is an observation field."""
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    out = []
    for cls, decl in (types.items() if isinstance(types, dict) else []):
        listed = {type_key(t) for t in (decl.get("types") or [])} if isinstance(decl, dict) \
            and isinstance(decl.get("types"), list) else set()
        if not listed or not tally.per_class.get(cls):
            continue
        values = tally.type_values.get(cls, Counter())
        outside = {v: n for v, n in values.items() if type_key(v) not in listed}
        out.append({"class": cls, "key": "type", "declared": True,
                    "fill": round(sum(values.values()) / tally.per_class[cls], 3),
                    "distinct_values": min(len(values), DISTINCT_CAP), "outside_types": sum(outside.values()),
                    "outside_distinct": len(outside), "flags": {"reserved": True},
                    "proposal": "keep", "reason": "the class's declared type list"})
        if len(values) >= DISTINCT_CAP:
            out[-1]["distinct_capped"] = True
    return out


def audit(domain: str, schema: dict, *, kg_dir: Path, depended_on: dict, conflicts: dict | None = None,
          leaf_schema: Callable[[str], dict] | None = None) -> dict:
    """One row per (class, key) declared by `schema` (the domain's `family_schema`) or found in
    the staged document observations of the domain and its children (table observations and
    observations without a class are skipped). `conflicts` None leaves the column out.

    `leaf_schema(name)` -> the schema extraction and retime read for the folder domain `name`
    (`temporal.load_schema`). A key `schema` declares but an observation's own leaf schema does
    not is `declared: false` with `undeclared_in: [domains]`, and `proposal: drop` (or `review`
    when another observing domain does declare it). None judges every observation against
    `schema`.

    Each `temporal: valid_from` key's row carries `dates`, its class's `ingest date-audit` counts
    (`DATE_COLUMNS`): class-level, so a class with two such keys shows the same counts on both,
    and `unparsed` counts every tagged key of the class."""
    from artmind.date_audit import audit_folders

    decls = _declarations(schema)
    folders = document_folders(kg_dir, domain)
    tally = _scan(folders, leaf_schema)
    keys = {(c, k) for c, ks in decls.items() for k in ks} | set(tally.filled)
    rows = [_row(cls, key, (decls.get(cls) or {}).get(key), tally, depended_on, conflicts)
            for cls, key in sorted(keys)]
    have = {(r["class"], r["key"]): r for r in rows}
    for extra in _type_rows(schema, tally):
        row = have.get((extra["class"], extra["key"]))
        if row is None:
            rows.append(extra)
        else:  # the schema also declares a `type` property: its row gains the counts
            row.update({k: extra[k] for k in ("outside_types", "outside_distinct")})
    dated = {r["class"]: r for r in audit_folders(folders, leaf_schema or schema)}
    for row in rows:
        # The class's date fill (`ingest date-audit`'s row) on the key that dates it.
        if row["flags"].get("temporal") == "valid_from" and row["class"] in dated:
            row["dates"] = {k: v for k, v in dated[row["class"]].items() if k in DATE_COLUMNS}
    return {"domain": domain, "warnings": _warnings(domain, tally), "rows": rows,
            "observations": dict(tally.per_class),
            "rules": {"required_fill": REQUIRED_FILL, "inferred_drop": INFERRED_DROP,
                      "text_only_max": TEXT_ONLY_MAX, "rare_fill": RARE_FILL}}


def _warnings(domain: str, tally: _Tally) -> list[str]:
    from artmind.schema_validate import domain_graph_extraction

    out = []
    if not domain_graph_extraction(domain):
        out.append(f"{domain} is chunks-only (graph_extraction: false): it has no entity properties to audit")
    if not tally.per_class:
        out.append(f"no staged document observations under {domain}: every proposal is `review`")
    return out


def _propose(row: dict, decl: dict | None, *, observed: int) -> tuple[str, str]:
    """First match wins (spec §3, with the plan's question 10 ordering). A declared key that
    collides with an observation field, or whose class has no observations, is not judged."""
    if decl is None:
        return "drop", "undeclared"
    if row["flags"].get("reserved"):
        return "review", "collides with an observation field"
    if not observed:
        return "review", "no document observations of this class"
    if decl.get("required") is True:
        return "required", "declared required"
    if decl.get("keep_reason"):
        return "keep", f"keep_reason: {decl['keep_reason']}"
    if row["flags"].get("temporal"):
        return "keep", "temporal"
    if row["flags"].get("depended_on"):
        return "keep", "depended on: " + ", ".join(row["flags"]["depended_on"])
    if row["fill"] >= REQUIRED_FILL:
        return "required", f"filled on at least {REQUIRED_FILL:.0%} of observations"
    if row["inferred"] >= INFERRED_DROP and row["text_only"] < TEXT_ONLY_MAX:
        return "drop", "mostly the model's interpretation"
    if row["fill"] < RARE_FILL:
        return "drop", "rarely stated"
    return "review", "no rule decides"


def depended_on_keys(domain: str, schema: dict) -> dict[tuple[str, str], list[str]]:
    """{(class, key): [mart | insights | view:<name> | skill:view:<name> | skill:plan:<slug>]} --
    what names each key (plan question 11). `schema` is the domain's `family_schema`. The mart
    and insights readers are `schema_validate`'s, shared with `domains validate`."""
    from artmind.insights import config as insights_config
    from artmind.insights.model import InsightsError
    from artmind.kg_views import store as views_store
    from artmind.mart import declaration as mart_declaration
    from artmind.schema_validate import (
        insights_property_refs, mart_property_refs, view_bound_classes, view_property_refs,
    )

    declared = declared_property_keys(schema)
    out: dict = defaultdict(set)
    try:
        decl = mart_declaration.load_declaration(domain)
    except Exception:
        decl = None  # `domains validate` reports a broken declaration
    for _label, classes, key in mart_property_refs(decl):
        for cls in classes:
            out[(cls, key)].add("mart")
    try:
        cfg = insights_config.load_config(domain)
    except InsightsError:
        cfg = None
    for _label, cls, key in insights_property_refs(cfg):
        out[(cls, key)].add("insights")
    views, _invalid = views_store.list_views([domain])
    for view in views:
        for classes, key in view_property_refs(view.spec.cypher, declared):
            for cls in classes:
                out[(cls, key)].add(f"view:{view.spec.name}")
        _skill_refs(view.skill_md, declared, out, f"skill:view:{view.spec.name}",
                    bound=view_bound_classes(view.spec.cypher, declared))
    for slug, text in _topic_plan_skills(domain):
        _skill_refs(text, declared, out, f"skill:plan:{slug}")
    return {k: sorted(v) for k, v in out.items()}


def _skill_refs(text: str, declared: dict, out: dict, source: str, *, bound: set[str] = frozenset()) -> None:
    """Backticked references in a SKILL.md (plan question 11). `CLASS.key` counts for that class
    when it declares the key. A bare `key` counts for the one class that declares it, or, when
    several do, for those of them the same view's Cypher binds (`bound`); otherwise it is
    ambiguous and not counted."""
    for cls, key in _BACKTICKED.findall(text or ""):
        if cls:
            targets = [cls] if key in declared.get(cls, set()) else []
        else:
            owners = [c for c, keys in declared.items() if key in keys]
            targets = owners if len(owners) == 1 else [c for c in owners if c in bound]
        for c in targets:
            out[(c, key)].add(source)


def _topic_plan_skills(domain: str) -> list[tuple[str, str]]:
    """SKILL.md texts of topic plans whose `domains` (plan.yaml, a list of strings; see
    `artmind/topics/plan.py`) cover `domain`: the same domain, a child or a parent. [(slug, text)]."""
    from artmind.topics import plan_store

    root = plan_store.plans_dir()
    texts = []
    for skill in sorted(root.glob("*/SKILL.md")) if root.is_dir() else []:
        plan = _load_yaml(skill.parent / "plan.yaml")
        domains = plan.get("domains") if isinstance(plan, dict) else None
        domains = [str(d) for d in domains] if isinstance(domains, list) else []
        if any(d == domain or d.startswith(domain + ".") or domain.startswith(d + ".") for d in domains):
            try:
                texts.append((skill.parent.name, skill.read_text(encoding="utf-8")))
            except OSError:
                continue
    return texts


def _load_yaml(path: Path):
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def graph_conflicts(domain: str) -> dict[tuple[str, str], int]:
    """{(class, key): open projection conflicts} for `domain` and its children (one read query)."""
    from artmind.graph_query import domain_predicate, normalize_domains, read_session

    cypher = f"""
    MATCH (c:Conflict {{_source: 'projection', status: 'open'}})-[:CONFLICT_OF]->(e:Entity)
    WHERE {domain_predicate("e")}
    RETURN e.entity_class AS cls, c.property AS key, count(*) AS n"""
    with read_session() as session:
        return {(r["cls"], r["key"]): r["n"] for r in session.run(cypher, domains=normalize_domains(domain))}
