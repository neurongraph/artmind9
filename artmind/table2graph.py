"""`ingest table2graph` -- project a structured table's rows into the graph.

A document reaches the graph through an LLM: chunk, extract, canonicalize.
A table does not need one. Its columns already *are* the properties, so the
translation from row to entity is declared once, in a **table mapping** YAML
under `domains/table_mappings/`, and executed deterministically -- the same
table and the same mapping always produce byte-identical observations.

What it writes is exactly what a document commit writes, through the same
transaction (`ingest._commit_document_tx`) -- except that the projection
rebuild runs afterwards in batches, because a table's thousands of keys
overrun Neo4j's per-transaction memory (see `_rebuild_in_batches`):

- the table is a `:Document` (`id = table:<domain>:<table>`), and each row it
  contributes is a `:DocChunk` carrying the row rendered as text -- so every
  provenance query that walks `Entity -> Observation -> DocChunk -> Document`
  works on table-sourced entities unchanged, and vector search finds rows;
- each mapped entity in a row is an `:Observation`; each mapped relationship
  is an `ASSERTS_RELATION` edge between two of them;
- a re-run demotes the previous run's observations to history and rebuilds
  the affected keys, so an edited mapping or a refreshed table *replaces*
  what the last run said rather than accreting beside it.

**Identity is declared, not inferred.** A mapping's `name` template renders
the entity name *and* its aggregate-key identity (`build_observation(...,
identity=...)`). The distinction matters: the extraction path's
`normalize_name` strips a trailing digit-bearing parenthetical (it's usually a
measurement), which would fold `"Pooja Jain (002PZE744)"` and `"Pooja Jain
(003SZR744)"` onto one entity. A declared identity is only case/whitespace
normalized.

**Temporal (SCD-2) tables.** Every row version becomes its own chunk, valid
from the version's `_valid_from`. Each mapping entity then picks which of its
versions the graph keeps (`versions:`): `latest` (default) keeps one
observation per identity -- the most recent version that produced it -- so a
2025 performance segment survives a 2026 refresh with its last-known values,
without raising a conflict over metrics that drifted between exports; `all`
keeps every version, which a `recurrent` class records as temporal variation.

The mapping format is documented in the `artmind-create-schema` skill
(`references/table_mapping.md`), which is also how one gets authored.
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cached_property
from pathlib import Path

import yaml
from loguru import logger

#: Pseudo-columns a field spec may read in place of a real column.
PSEUDO_COLUMNS = frozenset({"$valid_from", "$ingested_at", "$table"})

VERSION_POLICIES = ("latest", "all")
UNRESOLVED_POLICIES = ("stub", "skip")
AMBIGUOUS_POLICIES = ("skip", "stub", "first")

#: Report at most this many distinct unresolved/ambiguous lookup values.
_SAMPLE_LIMIT = 25

#: `{var}`, `{alias.var}`; a trailing `?` (`{var?}`) makes the variable optional.
_TEMPLATE_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)(\?)?\}")
_REL_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_YEAR_RE = re.compile(r"^\s*(\d{4})(?!\d)")
_ISO_DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")


class MappingError(ValueError):
    """A table mapping that cannot be loaded, or fails validation."""


# ── transforms ───────────────────────────────────────────────────────────────


def _smart_title(value: str) -> str:
    """Title-case words that are all-caps or all-lower; leave mixed case alone.

    `"DAS, SURJIT"` -> `"Das, Surjit"`, while `"McDonald"` stays `"McDonald"`
    (plain `str.title()` would make it `"Mcdonald"`). Hyphen/apostrophe parts
    are cased independently: `"O'BRIEN-SMITH"` -> `"O'Brien-Smith"`.
    """
    def _word(word: str) -> str:
        parts = re.split(r"([-'])", word)
        return "".join(
            p[:1].upper() + p[1:].lower() if (p.isupper() or p.islower()) else p
            for p in parts
        )
    return " ".join(_word(w) for w in value.split())


def _to_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else value
    try:
        return float(str(value).replace(",", "").strip().rstrip("%").strip())
    except ValueError:
        return None


def _to_integer(value):
    number = _to_number(value)
    return int(number) if number is not None else None


def _to_boolean(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def _to_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    match = _ISO_DATE_RE.match(str(value))
    if match:
        return match.group(1)
    from artmind.temporal import parse_iso

    parsed = parse_iso(str(value))
    return parsed if parsed and len(parsed) == 10 else None


def _to_year(value):
    if isinstance(value, (date, datetime)):
        return value.year
    match = _YEAR_RE.match(str(value))
    return int(match.group(1)) if match else None


def _str_only(fn):
    def wrapped(value):
        return fn(value) if isinstance(value, str) else value
    return wrapped


_SIMPLE_TRANSFORMS = {
    "strip": _str_only(str.strip),
    "lower": _str_only(str.lower),
    "upper": _str_only(str.upper),
    "title": _str_only(_smart_title),
    "string": lambda v: str(v),
    "number": _to_number,
    "integer": _to_integer,
    "boolean": _to_boolean,
    "date": _to_date,
    "year": _to_year,
}


def _split_transform(arg):
    if not isinstance(arg, dict) or "sep" not in arg or "index" not in arg:
        raise MappingError("transform 'split' takes {sep: <str>, index: <int>}")
    sep, index = str(arg["sep"]), int(arg["index"])

    def apply(value):
        parts = str(value).split(sep)
        if -len(parts) <= index < len(parts):
            return parts[index].strip() or None
        return None
    return apply


def _round_transform(arg):
    digits = int(arg)

    def apply(value):
        number = _to_number(value)
        return round(number, digits) if number is not None else None
    return apply


def _replace_transform(arg):
    if not isinstance(arg, dict) or not arg:
        raise MappingError("transform 'replace' takes a {old: new} map")
    pairs = [(str(k), str(v)) for k, v in arg.items()]

    def apply(value):
        text = str(value)
        for old, new in pairs:
            text = text.replace(old, new)
        return text
    return apply


_PARAM_TRANSFORMS = {
    "split": _split_transform,
    "round": _round_transform,
    "replace": _replace_transform,
}


def _compile_transforms(spec) -> list:
    """`transform:` -- one name, or a list of names / single-key `{name: arg}`
    maps -- to a list of callables applied in order. Raises `MappingError`."""
    if spec is None:
        return []
    items = spec if isinstance(spec, list) else [spec]
    compiled = []
    for item in items:
        if isinstance(item, str):
            if item not in _SIMPLE_TRANSFORMS:
                raise MappingError(
                    f"unknown transform {item!r} (known: "
                    f"{', '.join(sorted(_SIMPLE_TRANSFORMS) + sorted(_PARAM_TRANSFORMS))})"
                )
            compiled.append(_SIMPLE_TRANSFORMS[item])
        elif isinstance(item, dict) and len(item) == 1:
            (name, arg), = item.items()
            if name not in _PARAM_TRANSFORMS:
                raise MappingError(f"unknown parameterised transform {name!r} (known: {', '.join(sorted(_PARAM_TRANSFORMS))})")
            compiled.append(_PARAM_TRANSFORMS[name](arg))
        else:
            raise MappingError(f"a transform is a name or a single-key {{name: arg}} map, got {item!r}")
    return compiled


# ── templates ────────────────────────────────────────────────────────────────


def template_vars(template: str) -> list[str]:
    """The `{var}` / `{alias.var}` names a template references (`{{` escapes)."""
    text = str(template).replace("{{", "").replace("}}", "")
    return [name for name, _ in _TEMPLATE_VAR_RE.findall(text)]


def render_template(template: str, context: dict) -> tuple[str | None, str | None]:
    """Render `template` against `context`. Returns `(text, None)`, or
    `(None, missing_var)` when a required value is absent -- a name built
    from a partial row is worse than no entity at all.

    An optional variable (`{last_name?}`) renders as nothing when absent, and
    the result's whitespace is collapsed, so `"{first} {last?} ({id})"` gives
    `"Srajana (000ZE0744)"` for a person with no surname.
    """
    text = str(template).replace("{{", "\x00").replace("}}", "\x01")

    def _absent(name: str) -> bool:
        value = context.get(name)
        return value is None or value == ""

    for name, optional in _TEMPLATE_VAR_RE.findall(text):
        if not optional and _absent(name):
            return None, name
    rendered = _TEMPLATE_VAR_RE.sub(
        lambda m: "" if _absent(m.group(1)) else _display(context[m.group(1)]), text
    )
    if any(optional for _, optional in _TEMPLATE_VAR_RE.findall(text)):
        rendered = " ".join(rendered.split())
    return rendered.replace("\x00", "{").replace("\x01", "}"), None


def _display(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(_display(v) for v in value)
    return str(value)


# ── the compiled mapping ─────────────────────────────────────────────────────


@dataclass
class FieldSpec:
    column: str | None = None
    value: object = None
    template: str | None = None
    transforms: list = field(default_factory=list)
    value_map: dict | None = None
    default: object = None          # a literal, or a nested FieldSpec
    required: bool = False

    def columns(self) -> set[str]:
        out = {self.column} if self.column else set()
        if isinstance(self.default, FieldSpec):
            out |= self.default.columns()
        return out


@dataclass
class Condition:
    column: str
    equals: object = None
    in_values: list | None = None
    not_in: list | None = None
    present: bool | None = None


@dataclass
class LookupSpec:
    entity: str
    column: str
    match_column: str
    prefer: list[Condition] = field(default_factory=list)
    on_unresolved: str = "stub"
    on_ambiguous: str = "skip"


@dataclass
class EntitySpec:
    alias: str
    entity_class: str
    name: str | None
    properties: dict[str, FieldSpec]
    type: str | None = None
    description: str | None = None
    versions: str = "latest"
    when: list[Condition] = field(default_factory=list)
    lookup: LookupSpec | None = None


@dataclass
class RelationshipSpec:
    source: str
    rel_type: str
    target: str

    @property
    def label(self) -> str:
        return f"{self.source}-{self.rel_type}->{self.target}"


@dataclass
class TableMapping:
    path: Path | None
    tables: list[str]
    domain: str | None
    description: str | None
    null_values: set[str]
    row_key: list[str]
    row_text_include: list[str] | None
    row_text_exclude: set[str]
    valid_from: str
    entities: dict[str, EntitySpec]
    relationships: list[RelationshipSpec]

    def matches(self, table_name: str, domain: str | None = None) -> bool:
        if domain and self.domain and self.domain != domain:
            return False
        return any(fnmatch.fnmatchcase(table_name, pattern) for pattern in self.tables)


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _parse_field(raw, where: str) -> FieldSpec:
    if isinstance(raw, str):
        return FieldSpec(column=raw)
    if not isinstance(raw, dict):
        raise MappingError(f"{where}: a field is a column name or a map, got {raw!r}")
    sources = [k for k in ("column", "value", "template") if k in raw]
    if len(sources) != 1:
        raise MappingError(f"{where}: exactly one of column / value / template is required")
    unknown = set(raw) - {"column", "value", "template", "transform", "map", "default", "required"}
    if unknown:
        raise MappingError(f"{where}: unknown key(s) {sorted(unknown)}")
    default = raw.get("default")
    if isinstance(default, dict):
        default = _parse_field(default, f"{where}.default")
    value_map = raw.get("map")
    if value_map is not None and not isinstance(value_map, dict):
        raise MappingError(f"{where}.map: must be a {{from: to}} map")
    try:
        transforms = _compile_transforms(raw.get("transform"))
    except MappingError as e:
        raise MappingError(f"{where}: {e}") from None
    return FieldSpec(
        column=raw.get("column"),
        value=raw.get("value"),
        template=raw.get("template"),
        transforms=transforms,
        value_map={str(k): v for k, v in value_map.items()} if value_map else None,
        default=default,
        required=bool(raw.get("required", False)),
    )


def _parse_conditions(raw, where: str) -> list[Condition]:
    out = []
    for i, item in enumerate(_as_list(raw)):
        if not isinstance(item, dict) or "column" not in item:
            raise MappingError(f"{where}[{i}]: a condition is a map with a 'column'")
        tests = [k for k in ("equals", "in", "not_in", "present") if k in item]
        if len(tests) != 1:
            raise MappingError(f"{where}[{i}]: exactly one of equals / in / not_in / present is required")
        out.append(Condition(
            column=item["column"],
            equals=item.get("equals"),
            in_values=[str(v) for v in _as_list(item["in"])] if "in" in item else None,
            not_in=[str(v) for v in _as_list(item["not_in"])] if "not_in" in item else None,
            present=bool(item["present"]) if "present" in item else None,
        ))
    return out


def parse_mapping(data: dict, path: Path | None = None) -> TableMapping:
    """Structural parse of a mapping document. Raises `MappingError`.

    Checks only what can be checked without the table or the schema; see
    `validate_mapping` for the rest.
    """
    where = str(path) if path else "mapping"
    if not isinstance(data, dict):
        raise MappingError(f"{where}: top level must be a map")
    tables = [str(t) for t in _as_list(data.get("table"))]
    if not tables:
        raise MappingError(f"{where}: 'table' (a table name or glob, or a list of them) is required")
    raw_entities = data.get("entities")
    if not isinstance(raw_entities, dict) or not raw_entities:
        raise MappingError(f"{where}: 'entities' must be a non-empty map of alias -> entity")

    entities: dict[str, EntitySpec] = {}
    for alias, raw in raw_entities.items():
        ew = f"{where}: entities.{alias}"
        if not isinstance(raw, dict):
            raise MappingError(f"{ew}: must be a map")
        if not raw.get("class"):
            raise MappingError(f"{ew}: 'class' is required")
        raw_props = raw.get("properties") or {}
        if not isinstance(raw_props, dict):
            raise MappingError(f"{ew}.properties: must be a map")
        lookup = None
        if raw.get("lookup") is not None:
            lk = raw["lookup"]
            lw = f"{ew}.lookup"
            if not isinstance(lk, dict):
                raise MappingError(f"{lw}: must be a map")
            for required in ("entity", "column", "match_column"):
                if not lk.get(required):
                    raise MappingError(f"{lw}: '{required}' is required")
            lookup = LookupSpec(
                entity=str(lk["entity"]),
                column=str(lk["column"]),
                match_column=str(lk["match_column"]),
                prefer=_parse_conditions(lk.get("prefer"), f"{lw}.prefer"),
                on_unresolved=str(lk.get("on_unresolved", "stub")),
                on_ambiguous=str(lk.get("on_ambiguous", "skip")),
            )
            if lookup.on_unresolved not in UNRESOLVED_POLICIES:
                raise MappingError(f"{lw}.on_unresolved: one of {', '.join(UNRESOLVED_POLICIES)}")
            if lookup.on_ambiguous not in AMBIGUOUS_POLICIES:
                raise MappingError(f"{lw}.on_ambiguous: one of {', '.join(AMBIGUOUS_POLICIES)}")
        name = raw.get("name")
        needs_name = lookup is None or "stub" in (lookup.on_unresolved, lookup.on_ambiguous)
        if needs_name and not name:
            raise MappingError(
                f"{ew}: 'name' (a template, e.g. \"{{first_name}} {{last_name}} ({{id}})\") is required"
                + (" for the stub a lookup creates" if lookup else "")
            )
        versions = str(raw.get("versions", "latest"))
        if versions not in VERSION_POLICIES:
            raise MappingError(f"{ew}.versions: one of {', '.join(VERSION_POLICIES)}")
        entities[str(alias)] = EntitySpec(
            alias=str(alias),
            entity_class=str(raw["class"]),
            name=str(name) if name else None,
            properties={str(p): _parse_field(spec, f"{ew}.properties.{p}") for p, spec in raw_props.items()},
            type=str(raw["type"]) if raw.get("type") else None,
            description=str(raw["description"]) if raw.get("description") else None,
            versions=versions,
            when=_parse_conditions(raw.get("when"), f"{ew}.when"),
            lookup=lookup,
        )

    relationships = []
    for i, raw in enumerate(_as_list(data.get("relationships"))):
        rw = f"{where}: relationships[{i}]"
        if not isinstance(raw, dict) or not all(raw.get(k) for k in ("source", "rel_type", "target")):
            raise MappingError(f"{rw}: needs source, rel_type and target")
        relationships.append(RelationshipSpec(str(raw["source"]), str(raw["rel_type"]), str(raw["target"])))

    row_text = data.get("row_text") or {}
    document = data.get("document") or {}
    null_values = {str(v).strip() for v in _as_list(data.get("null_values"))} | {""}
    return TableMapping(
        path=path,
        tables=tables,
        domain=str(data["domain"]) if data.get("domain") else None,
        description=data.get("description"),
        null_values=null_values,
        row_key=[str(c) for c in _as_list(data.get("row_key"))],
        row_text_include=[str(c) for c in _as_list(row_text.get("include"))] or None,
        row_text_exclude={str(c) for c in _as_list(row_text.get("exclude"))},
        valid_from=str(document.get("valid_from", "$ingested_at")),
        entities=entities,
        relationships=relationships,
    )


def load_mapping(path: Path) -> TableMapping:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise MappingError(f"{path}: invalid YAML: {e}") from None
    return parse_mapping(data, Path(path))


def find_mappings(table_name: str, domain: str | None, mappings_dir: Path | None = None) -> list[TableMapping]:
    """Every mapping under `mappings_dir` whose `table` pattern matches.

    A mapping that fails to parse is an error, not a skip: silently ignoring
    it would report "no mapping for this table" about a file that plainly
    names it.
    """
    if mappings_dir is None:
        from paths import TABLE_MAPPINGS_DIR

        mappings_dir = TABLE_MAPPINGS_DIR
    if not mappings_dir.is_dir():
        return []
    found = []
    for path in sorted(mappings_dir.glob("*.yaml")):
        mapping = load_mapping(path)
        if mapping.matches(table_name, domain):
            found.append(mapping)
    return found


# ── validation against the schema and the table ──────────────────────────────


def _declared_rel_types(schema: dict, class_a: str, class_b: str) -> set[str]:
    """rel_types either class declares for the pair (relates_to is one-sided)."""
    types = schema.get("entity_types") or {}
    declared: set[str] = set()
    for src, dst in ((class_a, class_b), (class_b, class_a)):
        for entry in ((types.get(src) or {}).get("relates_to") or {}).get(dst) or []:
            # An entry may carry an inline hint: 'of (properties: {...})'.
            declared.add(str(entry).split("(", 1)[0].strip())
    return declared


def validate_mapping(mapping: TableMapping, schema: dict, columns: list[str]) -> tuple[list[str], list[str]]:
    """Check a mapping against the domain schema and the table's columns.

    Returns `(errors, warnings)`. An error means the mapping cannot run as
    written; a warning means it runs, but writes something the schema does
    not declare -- worth fixing in one file or the other, since the schema
    is the vocabulary the rest of the graph (and every query prompt) uses.
    """
    from artmind.ingest import RESERVED_REL_TYPES
    from artmind.observations import RESERVED_PREFIX, _STRUCTURAL_KEYS

    errors: list[str] = []
    warnings: list[str] = []
    known_columns = set(columns) | PSEUDO_COLUMNS
    classes = schema.get("entity_types") or {}

    def _check_columns(cols, where):
        for col in cols:
            if col not in known_columns:
                errors.append(f"{where}: column {col!r} is not in the table")

    def _check_conditions(conditions, where):
        _check_columns([c.column for c in conditions], where)

    _check_columns(mapping.row_key, "row_key")
    _check_columns(mapping.row_text_include or [], "row_text.include")
    if mapping.valid_from.startswith("$"):
        if mapping.valid_from != "$ingested_at":
            errors.append(f"document.valid_from: must be $ingested_at or an ISO date, got {mapping.valid_from!r}")
    elif not _to_date(mapping.valid_from):
        errors.append(f"document.valid_from: {mapping.valid_from!r} is not an ISO date (YYYY-MM-DD)")

    seen: dict[str, EntitySpec] = {}
    non_lookup = {a: e for a, e in mapping.entities.items() if e.lookup is None}
    for alias, spec in mapping.entities.items():
        where = f"entities.{alias}"
        declared = classes.get(spec.entity_class)
        if declared is None:
            errors.append(f"{where}: class {spec.entity_class!r} is not declared in the schema")
            declared = {}
        declared_props = set((declared.get("properties") or {}).keys())
        _check_conditions(spec.when, f"{where}.when")

        available = set()   # own properties computed so far
        for prop, fspec in spec.properties.items():
            pw = f"{where}.properties.{prop}"
            if prop.startswith(RESERVED_PREFIX) or prop in _STRUCTURAL_KEYS:
                errors.append(f"{pw}: {prop!r} is reserved (artmind writes it itself)")
            elif declared and prop not in declared_props:
                warnings.append(f"{pw}: property {prop!r} is not declared on {spec.entity_class} in the schema")
            _check_columns(fspec.columns(), pw)
            if fspec.template:
                for var in template_vars(fspec.template):
                    if var not in available and not _is_alias_ref(var, non_lookup if spec.lookup else seen):
                        errors.append(f"{pw}: template references {{{var}}}, which is not an earlier property of {alias}")
            available.add(prop)

        context_ok = available | {"name"}
        # A lookup runs after every row's other entities exist, so its stub may
        # name any of them; everything else sees only the entities above it.
        visible = non_lookup if spec.lookup else seen
        for label, template in (("name", spec.name), ("type", spec.type), ("description", spec.description)):
            for var in template_vars(template or ""):
                if var not in context_ok and not _is_alias_ref(var, visible):
                    errors.append(f"{where}.{label}: template references {{{var}}}, which is neither a property of {alias} nor an earlier entity")

        if spec.lookup:
            lk, lw = spec.lookup, f"{where}.lookup"
            target = mapping.entities.get(lk.entity)
            if target is None:
                errors.append(f"{lw}.entity: {lk.entity!r} is not an entity alias")
            elif target.lookup is not None:
                errors.append(f"{lw}.entity: {lk.entity!r} is itself a lookup")
            elif target.entity_class != spec.entity_class:
                errors.append(f"{lw}.entity: {lk.entity!r} is a {target.entity_class}, not a {spec.entity_class}")
            _check_columns([lk.column, lk.match_column], lw)
            _check_conditions(lk.prefer, f"{lw}.prefer")
        seen[alias] = spec

    for spec in mapping.entities.values():
        # A lookup entity exists only once the lookup pass has run, so nothing
        # but a relationship may refer to it.
        if spec.lookup:
            continue
        refs = [spec.name or "", spec.type or "", spec.description or ""]
        refs += [f.template for f in spec.properties.values() if f.template]
        for template in refs:
            for var in template_vars(template):
                alias = var.split(".", 1)[0]
                if "." in var and alias in mapping.entities and mapping.entities[alias].lookup:
                    errors.append(f"entities.{spec.alias}: templates cannot reference lookup entity {alias!r}")

    for rel in mapping.relationships:
        where = f"relationships[{rel.label}]"
        for alias in (rel.source, rel.target):
            if alias not in mapping.entities:
                errors.append(f"{where}: {alias!r} is not an entity alias")
        if not _REL_TYPE_RE.match(rel.rel_type):
            errors.append(f"{where}: rel_type {rel.rel_type!r} must be an identifier (letters, digits, _)")
        elif rel.rel_type.upper() in RESERVED_REL_TYPES:
            errors.append(f"{where}: rel_type {rel.rel_type!r} is reserved for artmind's own machinery")
        if rel.source in mapping.entities and rel.target in mapping.entities:
            src_cls = mapping.entities[rel.source].entity_class
            dst_cls = mapping.entities[rel.target].entity_class
            if src_cls in classes and dst_cls in classes and rel.rel_type not in _declared_rel_types(schema, src_cls, dst_cls):
                warnings.append(f"{where}: rel_type {rel.rel_type!r} is not declared between {src_cls} and {dst_cls} in the schema")

    return errors, warnings


def _is_alias_ref(var: str, seen: dict) -> bool:
    """`{alias}`/`{alias.name}`/`{alias.prop}` naming an earlier entity."""
    alias, _, prop = var.partition(".")
    spec = seen.get(alias)
    if spec is None or spec.lookup is not None:
        return False
    return prop in ("", "name") or prop in spec.properties


# ── evaluation ───────────────────────────────────────────────────────────────


@dataclass
class _Row:
    index: int
    values: dict
    valid_from: str | None
    chunk_id: str
    row_key: str


@dataclass
class _Built:
    """One entity one row produced -- before the versions policy picks."""
    alias: str
    entity_class: str
    name: str
    props: dict
    type: str | None
    description: str | None
    row: _Row

    @cached_property
    def identity(self) -> str:
        from artmind.observations import normalize_identity

        return normalize_identity(self.name)


class _Evaluator:
    def __init__(self, mapping: TableMapping, pseudo: dict):
        self.mapping = mapping
        self.pseudo = pseudo

    def cell(self, row: _Row, column: str):
        if column == "$valid_from":
            return row.valid_from
        if column in PSEUDO_COLUMNS:
            return self.pseudo.get(column)
        return self.clean(row.values.get(column))

    def clean(self, value):
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        if isinstance(value, str):
            value = value.strip()
            if value in self.mapping.null_values:
                return None
        return value

    def field(self, spec: FieldSpec, row: _Row, context: dict):
        if spec.template is not None:
            value, _ = render_template(spec.template, context)
        elif spec.column is not None:
            value = self.cell(row, spec.column)
        else:
            value = spec.value
        for transform in spec.transforms:
            if value is None:
                break
            value = self.clean(transform(value))
        if value is not None and spec.value_map is not None:
            key = _display(value)
            if key in spec.value_map:
                value = spec.value_map[key]
            else:
                folded = {k.casefold(): v for k, v in spec.value_map.items()}
                value = folded.get(key.casefold(), value)
        if value is None and spec.default is not None:
            value = (
                self.field(spec.default, row, context)
                if isinstance(spec.default, FieldSpec) else spec.default
            )
        return value

    def condition(self, cond: Condition, row: _Row) -> bool:
        value = self.cell(row, cond.column)
        if cond.present is not None:
            return (value is not None) == cond.present
        if value is None:
            return cond.not_in is not None
        text = _display(value)
        if cond.equals is not None:
            return text == str(cond.equals)
        if cond.in_values is not None:
            return text in cond.in_values
        return text not in (cond.not_in or [])

    def build(self, spec: EntitySpec, row: _Row, aliases: dict) -> tuple[_Built | None, str | None]:
        """One entity for one row, or `(None, reason)`."""
        context = dict(aliases)
        props: dict = {}
        for prop, fspec in spec.properties.items():
            value = self.field(fspec, row, {**context, **props})
            if value is None:
                if fspec.required:
                    return None, f"required property {prop!r} is empty"
                continue
            props[prop] = value
        name, missing = render_template(spec.name, {**context, **props})
        if name is None:
            return None, f"name template needs {{{missing}}}, which is empty"
        text_ctx = {**context, **props, "name": name}
        return _Built(
            alias=spec.alias,
            entity_class=spec.entity_class,
            name=name,
            props=props,
            type=render_template(spec.type, text_ctx)[0] if spec.type else None,
            description=render_template(spec.description, text_ctx)[0] if spec.description else None,
            row=row,
        ), None


def _norm_match(value) -> str:
    return " ".join(str(value).casefold().split()) if value is not None else ""


def _alias_context(built: _Built) -> dict:
    context = {built.alias: built.name, f"{built.alias}.name": built.name}
    for prop, value in built.props.items():
        context[f"{built.alias}.{prop}"] = value
    return context


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return _to_date(text) or (text[:4] if _YEAR_RE.match(text) else None)


def row_text(mapping: TableMapping, table_name: str, row: _Row, columns: list[str]) -> str:
    """A row rendered as `Column: value` lines -- the DocChunk's text, which is
    what vector/fulltext search and every provenance citation show."""
    evaluator = _Evaluator(mapping, {})
    include = mapping.row_text_include or columns
    lines = [f"Table {table_name}, row {row.row_key}"]
    for column in include:
        if column in mapping.row_text_exclude or column.startswith("_"):
            continue
        value = evaluator.clean(row.values.get(column))
        if value is None:
            continue
        lines.append(f"{column}: {_display(value)}")
    return "\n".join(lines)


def build_staged(
    table: dict,
    rows: list[dict],
    columns: list[str],
    mapping: TableMapping,
    schema: dict,
) -> tuple[dict, dict]:
    """Rows + mapping -> the `staged` dict `_commit_document_tx` consumes, plus
    a report of what was built, skipped, and why. Pure -- no session, no I/O.

    `table` is the registry row (`table_name`, `domain`, `version`,
    `refresh_mode`, `business_key`, `ingested_at`, `source_file`).
    """
    from artmind.observations import build_observation, class_kind
    from artmind.temporal import _entity_temporal_mapping, parse_iso

    domain = table["domain"]
    table_name = table["table_name"]
    temporal = table.get("refresh_mode") == "temporal"
    doc_id = f"table:{domain}:{table_name}"
    ingested_at = _iso(table.get("ingested_at"))
    pseudo = {"$ingested_at": table.get("ingested_at"), "$table": table_name}
    evaluator = _Evaluator(mapping, pseudo)

    # ── rows, their keys and their valid time ──────────────────────────────
    # The registry stores a temporal table's business key comma-joined.
    key_columns = mapping.row_key or [
        c.strip() for c in (table.get("business_key") or "").split(",") if c.strip()
    ]
    # A replace-mode table is one snapshot: every row is valid from the same
    # date (`document.valid_from` -- the ingest date unless the mapping pins
    # one). A temporal table's rows each carry their own `_valid_from`.
    doc_valid_from = None
    if not temporal:
        source = mapping.valid_from
        doc_valid_from = _iso(pseudo.get(source) if source.startswith("$") else source) or ingested_at

    parsed_rows: list[_Row] = []
    seen_chunks: dict[str, int] = {}
    for index, values in enumerate(rows, start=1):
        valid_from = _iso(values.get("_valid_from")) if temporal else doc_valid_from
        parts = [_display(evaluator.clean(values.get(c))) for c in key_columns if evaluator.clean(values.get(c)) is not None]
        row_key = "|".join(parts) if key_columns and len(parts) == len(key_columns) else f"row{index}"
        chunk_id = f"{doc_id}#{row_key}" + (f"@{valid_from}" if temporal else "")
        if chunk_id in seen_chunks:
            # Duplicate business key within one version: keep both rows, each
            # its own chunk, rather than letting the second overwrite the first.
            seen_chunks[chunk_id] += 1
            chunk_id = f"{chunk_id}~{seen_chunks[chunk_id]}"
        else:
            seen_chunks[chunk_id] = 1
        parsed_rows.append(_Row(index, values, valid_from, chunk_id, row_key))

    report: dict = {
        "table": table_name,
        "domain": domain,
        "mapping": str(mapping.path) if mapping.path else None,
        "doc_id": doc_id,
        "refresh_mode": table.get("refresh_mode") or "replace",
        "rows": len(parsed_rows),
        "entities": {},
        "lookups": {},
        "relationships": {},
    }
    for alias, spec in mapping.entities.items():
        report["entities"][alias] = {"class": spec.entity_class, "built": 0, "kept": 0, "skipped": {}}
        if spec.lookup:
            report["lookups"][alias] = {
                "resolved": 0, "stubbed": 0, "skipped_unresolved": 0, "skipped_ambiguous": 0,
                "empty": 0, "unresolved_values": [], "ambiguous_values": [],
            }

    def _skip(alias: str, reason: str) -> None:
        bucket = report["entities"][alias]["skipped"]
        bucket[reason] = bucket.get(reason, 0) + 1

    # ── pass 1: every non-lookup entity, row by row ─────────────────────────
    built_by_row: dict[int, dict[str, _Built]] = {}
    for row in parsed_rows:
        local: dict[str, _Built] = {}
        context: dict = {}
        for alias, spec in mapping.entities.items():
            if spec.lookup:
                continue
            if spec.when and not all(evaluator.condition(c, row) for c in spec.when):
                _skip(alias, "when condition not met")
                continue
            built, reason = evaluator.build(spec, row, context)
            if built is None:
                _skip(alias, reason)
                continue
            local[alias] = built
            context.update(_alias_context(built))
            report["entities"][alias]["built"] += 1
        built_by_row[row.index] = local

    # ── pass 2: lookups (resolved against pass 1's entities) ────────────────
    #: row index -> alias -> ("key", identity) for a resolved lookup
    resolved_by_row: dict[int, dict[str, str]] = {r.index: {} for r in parsed_rows}
    for alias, spec in mapping.entities.items():
        lk = spec.lookup
        if lk is None:
            continue
        stats = report["lookups"][alias]
        # match value -> identity -> the latest row that built it
        index: dict[str, dict[str, _Row]] = {}
        for row in parsed_rows:
            target = built_by_row[row.index].get(lk.entity)
            if target is None:
                continue
            match = _norm_match(evaluator.cell(row, lk.match_column))
            if not match:
                continue
            current = index.setdefault(match, {}).get(target.identity)
            if current is None or (row.valid_from or "", row.index) > (current.valid_from or "", current.index):
                index[match][target.identity] = row

        for row in parsed_rows:
            if spec.when and not all(evaluator.condition(c, row) for c in spec.when):
                _skip(alias, "when condition not met")
                continue
            raw = evaluator.cell(row, lk.column)
            if raw is None:
                stats["empty"] += 1
                continue
            candidates = index.get(_norm_match(raw), {})
            chosen = _choose_candidate(candidates, lk, evaluator)
            if chosen:
                resolved_by_row[row.index][alias] = chosen
                stats["resolved"] += 1
                continue
            outcome = "unresolved" if not candidates else "ambiguous"
            policy = lk.on_unresolved if outcome == "unresolved" else lk.on_ambiguous
            samples = stats[f"{outcome}_values"]
            if raw not in samples and len(samples) < _SAMPLE_LIMIT:
                samples.append(raw)
            if outcome == "ambiguous" and policy == "first":
                resolved_by_row[row.index][alias] = sorted(candidates)[0]
                stats["resolved"] += 1
                continue
            if policy == "skip":
                stats[f"skipped_{outcome}"] += 1
                continue
            context: dict = {}
            for other in built_by_row[row.index].values():
                context.update(_alias_context(other))
            built, reason = evaluator.build(spec, row, context)
            if built is None:
                _skip(alias, f"stub: {reason}")
                continue
            built_by_row[row.index][alias] = built
            stats["stubbed"] += 1
            report["entities"][alias]["built"] += 1

    # ── the versions policy: which observation each identity keeps ──────────
    entity_dates = _entity_temporal_mapping(schema)
    all_built = [b for row in parsed_rows for b in built_by_row[row.index].values()]
    kept: dict[tuple[str, str], list[_Built]] = {}
    for built in all_built:
        kept.setdefault((built.entity_class, built.identity), []).append(built)
    keep_ids: set[int] = set()
    latest_for: dict[tuple[str, str], _Built] = {}
    for ident, group in kept.items():
        ordered = sorted(group, key=lambda b: (b.row.valid_from or "", b.row.index))
        latest_for[ident] = ordered[-1]
        policy = mapping.entities[ordered[-1].alias].versions
        survivors = ordered[-1:] if policy == "latest" else ordered
        keep_ids.update(id(b) for b in survivors)

    observations: list[dict] = []
    obs_by_built: dict[int, str] = {}
    for built in all_built:
        if id(built) not in keep_ids:
            continue
        fact_from, fact_to = None, None
        for canon, prop in (entity_dates.get(built.entity_class) or {}).items():
            raw = built.props.get(prop)
            parsed = parse_iso(_display(raw)) if raw is not None else None
            if canon == "valid_from":
                fact_from = parsed
            elif canon == "valid_to":
                fact_to = parsed
        entity = {"name": built.name, "entity_class": built.entity_class, "_domain": domain}
        if built.type:
            entity["type"] = built.type
        if built.description:
            entity["description"] = built.description
        observation = build_observation(
            entity,
            canonical_name=built.name,
            domain_props=built.props,
            doc_id=doc_id,
            doc_version=int(table.get("version") or 1),
            chunk_id=built.row.chunk_id,
            kind=class_kind(schema, built.entity_class),
            doc_valid_from=built.row.valid_from,
            valid_from=fact_from,
            valid_to=fact_to,
            valid_time_source="property" if fact_from else ("table_row" if temporal else "table"),
            identity=built.name,
        )
        # Same provenance marker `update confirm` sets ("user_chat"). Which
        # table and row is carried by doc_id/chunk_id, as for any document.
        observation["source_kind"] = "table"
        observations.append(observation)
        obs_by_built[id(built)] = observation["id"]
        report["entities"][built.alias]["kept"] += 1

    def _obs_for(ident: tuple[str, str], row: _Row) -> str | None:
        """This row's own kept observation of `ident`, else the latest kept one."""
        own = built_by_row[row.index]
        for built in own.values():
            if (built.entity_class, built.identity) == ident and id(built) in obs_by_built:
                return obs_by_built[id(built)]
        latest = latest_for.get(ident)
        return obs_by_built.get(id(latest)) if latest is not None else None

    # ── relationships ───────────────────────────────────────────────────────
    # The newest version of each row key: the only one allowed to assert an
    # edge whose endpoints are both lookups (it owns no observation of its own
    # to decide by).
    newest_version: dict[str, _Row] = {}
    for row in parsed_rows:
        current = newest_version.get(row.row_key)
        if current is None or (row.valid_from or "", row.index) > (current.valid_from or "", current.index):
            newest_version[row.row_key] = row

    relationships: list[dict] = []
    for rel in mapping.relationships:
        stats = {"written": 0, "skipped": {}}
        report["relationships"][rel.label] = stats

        def _rskip(reason: str) -> None:
            stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1

        for row in parsed_rows:
            endpoints = []
            row_local = owns_a_kept_observation = False
            for alias in (rel.source, rel.target):
                spec = mapping.entities[alias]
                built = built_by_row[row.index].get(alias)
                if built is not None:
                    ident = (built.entity_class, built.identity)
                    row_local = True
                    owns_a_kept_observation |= id(built) in obs_by_built
                elif alias in resolved_by_row[row.index]:
                    ident = (spec.entity_class, resolved_by_row[row.index][alias])
                else:
                    ident = None
                endpoints.append(ident)
            if None in endpoints:
                _rskip("an endpoint is missing in this row")
                continue
            current = owns_a_kept_observation if row_local else newest_version[row.row_key] is row
            if not current:
                # A superseded version of every row-local endpoint: the kept
                # version's row asserts this edge (or its replacement) instead.
                _rskip("superseded row version")
                continue
            if endpoints[0] == endpoints[1]:
                _rskip("self-reference")
                continue
            source_obs, target_obs = _obs_for(endpoints[0], row), _obs_for(endpoints[1], row)
            if not source_obs or not target_obs:
                _rskip("an endpoint has no kept observation")
                continue
            relationships.append({
                "source_observation_id": source_obs,
                "target_observation_id": target_obs,
                "source_name": endpoints[0][1],
                "target_name": endpoints[1][1],
                "rel_type": rel.rel_type,
                "chunk_id": row.chunk_id,
                "doc_id": doc_id,
            })
            stats["written"] += 1

    # ── chunks: only rows that contributed something ────────────────────────
    contributing = {o["chunk_id"] for o in observations} | {r["chunk_id"] for r in relationships}
    chunks = [
        {
            "id": row.chunk_id,
            "name": f"{table_name} row {row.row_key}",
            "doc_id": doc_id,
            "text": row_text(mapping, table_name, row, columns),
            "_domain": domain,
            "row_key": row.row_key,
            "row_index": row.index,
            **({"_valid_from": row.valid_from} if row.valid_from else {}),
        }
        for row in parsed_rows if row.chunk_id in contributing
    ]

    valid_froms = [r.valid_from for r in parsed_rows if r.valid_from]
    document = {
        "id": doc_id,
        "artmind_id": doc_id,
        "version": int(table.get("version") or 1),
        "name": table_name,
        "path": table.get("source_file") or table_name,
        "_domain": domain,
        "source_kind": "table",
        "table_name": table_name,
        "refresh_mode": table.get("refresh_mode") or "replace",
        "_valid_from": doc_valid_from or (max(valid_froms) if valid_froms else ingested_at),
        "_valid_time_source": "table_rows" if temporal else "table",
    }
    if mapping.path:
        document["table_mapping"] = mapping.path.name

    report["chunks"] = len(chunks)
    report["observations"] = len(observations)
    report["relationship_observations"] = len(relationships)
    staged = {
        "domain": domain,
        "document": document,
        "chunks": chunks,
        "observations": observations,
        "relationships": relationships,
    }
    return staged, report


def _choose_candidate(candidates: dict[str, _Row], lookup: LookupSpec, evaluator: _Evaluator) -> str | None:
    """The one identity a lookup value resolves to, or None (0 or >1 left)."""
    if len(candidates) == 1:
        return next(iter(candidates))
    remaining = dict(candidates)
    for cond in lookup.prefer:
        narrowed = {ident: row for ident, row in remaining.items() if evaluator.condition(cond, row)}
        if narrowed:
            remaining = narrowed
        if len(remaining) == 1:
            return next(iter(remaining))
    return None


# ── orchestration ────────────────────────────────────────────────────────────


def read_table_rows(table: dict, as_of: str | None = None) -> tuple[list[dict], list[str]]:
    """Every row (every version, for a temporal table) and the column order."""
    from artmind.structured import registry, view_name
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.scd2 import asof_view_sql

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    view = view_name(table["domain"], table["table_name"])
    if as_of and table.get("refresh_mode") == "temporal":
        sql = asof_view_sql(view, as_of)
    else:
        sql = f'SELECT * FROM "{view}"'
    rows = ds.run_sql(sql)
    # The result's own column order is the table's; the registry lists
    # columns alphabetically, which reads badly in a row's chunk text.
    if rows:
        columns = list(rows[0].keys())
    else:
        columns = [c["name"] for c in registry.get_columns(table["id"])] if table.get("id") else []
    return rows, columns


def stage_dir(domain: str, table_name: str) -> Path:
    from paths import KG_DIR

    return KG_DIR / domain / f"table__{table_name}"


def write_staged(staged: dict, report: dict, directory: Path) -> None:
    """The same staged files a document's extract leaves behind, so
    `ingest write-to-graph --folder` can replay a table's contribution too."""
    directory.mkdir(parents=True, exist_ok=True)

    def _write(name: str, obj) -> None:
        (directory / name).write_text(
            json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

    _write("document.json", staged["document"])
    _write("chunks.json", staged["chunks"])
    _write("observations.json", staged["observations"])
    _write("relationships.json", staged["relationships"])
    _write("table2graph_report.json", report)


#: Keys rebuilt per transaction. A table's commit is thousands of keys, and
#: Neo4j caps a transaction's memory (`dbms.memory.transaction.total.max`) --
#: found live: a 1223-row table, 2450 keys, failed at commit on a 343 MiB cap
#: once its entities carried embeddings.
REBUILD_BATCH = 400


def batch_keys(keys, groups: list[list[tuple]], size: int = REBUILD_BATCH) -> list[list[tuple]]:
    """Split `keys` into rebuild batches of about `size`. Pure.

    A same-as group never straddles two batches: `projection._plan_groups`
    only folds a group whose members are all in the keys it is given, so the
    group is kept whole even when that overfills a batch. Deterministic
    (sorted), so a rerun rebuilds in the same order.
    """
    group_of: dict[tuple, int] = {}
    for index, group in enumerate(groups):
        for member in group:
            group_of.setdefault(tuple(member), index)
    units: dict[object, list[tuple]] = {}
    for key in sorted({tuple(k) for k in keys}):
        units.setdefault(("group", group_of[key]) if key in group_of else ("key", key), []).append(key)
    batches: list[list[tuple]] = []
    current: list[tuple] = []
    for unit in units.values():
        if current and len(current) + len(unit) > size:
            batches.append(current)
            current = []
        current.extend(unit)
    if current:
        batches.append(current)
    return batches


def _rebuild_in_batches(keys: list) -> dict:
    """The projection rebuild for a committed table, one transaction per batch.

    This departs from a document commit, whose rebuild runs inside the same
    transaction as its observations; a table is simply too big for one. The
    cost is a window between the observation commit and the last batch in
    which part of the projection is stale -- and if a batch fails, it stays
    stale until repaired. Every batch is idempotent, so the repair is to run
    the same `ingest table2graph` again (or `artmind projection rebuild`).
    """
    from artmind import projection, same_as
    from artmind.graph_query import neo4j_session

    groups = same_as.load_groups()
    batches = batch_keys(keys, groups, REBUILD_BATCH)
    totals = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": len(batches)}
    with neo4j_session() as session:
        for number, batch in enumerate(batches, start=1):
            try:
                summary = session.execute_write(
                    lambda tx: projection.rebuild(
                        tx, batch, same_as_groups=groups,
                        synthesis_loader=lambda k: projection.load_synthesis(tx, k),
                    )
                )
            except Exception as e:
                raise RuntimeError(
                    f"projection rebuild failed on batch {number}/{len(batches)} ({e}); the "
                    "observations are committed but part of the projection is stale -- "
                    "re-run the same `ingest table2graph` (idempotent) or `artmind projection rebuild`"
                ) from e
            for field_name in ("rebuilt", "deleted", "absent", "keys"):
                totals[field_name] += summary.get(field_name, 0)
            logger.info("table2graph: rebuilt batch {}/{} ({} key(s))", number, len(batches), len(batch))
    return totals


def table_to_graph(
    table: dict,
    mapping: TableMapping,
    *,
    schema: dict,
    as_of: str | None = None,
    dry_run: bool = False,
    embed: bool = True,
) -> dict:
    """Validate, build, stage and commit one table. Returns the report.

    Raises `MappingError` when the mapping fails validation -- nothing is
    written. A dry run validates and builds (so its counts are real) but
    stages and commits nothing.
    """
    from artmind import ingest

    if mapping.domain and mapping.domain != table["domain"]:
        raise MappingError(
            f"{mapping.path}: mapping is for domain {mapping.domain!r}, "
            f"table {table['table_name']!r} is in {table['domain']!r}"
        )
    rows, columns = read_table_rows(table, as_of)
    errors, warnings = validate_mapping(mapping, schema, columns)
    if errors:
        raise MappingError(
            f"{mapping.path or 'mapping'} is invalid for table {table['table_name']!r}:\n  - "
            + "\n  - ".join(errors)
        )
    staged, report = build_staged(table, rows, columns, mapping, schema)
    report["warnings"] = warnings
    report["as_of"] = as_of
    report["dry_run"] = dry_run
    if dry_run:
        return report

    directory = stage_dir(table["domain"], table["table_name"])
    write_staged(staged, report, directory)
    report["staged_at"] = str(directory)
    # Phase 1: the document transaction, rebuild deferred. Phase 2: the
    # rebuild, in batches (see `_rebuild_in_batches` for why not one).
    summary = ingest._write_to_neo4j(directory, table["domain"], defer_rebuild=True)
    if summary is None:
        raise RuntimeError(f"could not read the staged files back from {directory}")
    keys = summary.get("deferred_keys") or []
    report["commit"] = {
        "chunks": summary.get("chunks"),
        "observations": summary.get("observations"),
        "relationships": summary.get("relationships"),
        "retracted": summary.get("retracted"),
        "affected_keys": len(keys),
    }
    report["commit"]["projection"] = _rebuild_in_batches(keys)
    if embed:
        report["commit"]["entities_embedded"] = ingest._sweep_embeddings(table["domain"], keys)
        report["commit"]["chunks_embedded"] = ingest._sweep_chunk_embeddings(
            summary.get("unembedded_chunk_ids") or []
        )
    logger.info(
        "table2graph: {} -> {} observation(s), {} relationship(s), {} chunk(s)",
        table["table_name"], report["observations"], report["relationship_observations"], report["chunks"],
    )
    return report
