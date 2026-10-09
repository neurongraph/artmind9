"""The view.yaml contract (spec 2026-10-09-kg-views-design.md, section 4).

Pure: no Neo4j, no filesystem. `parse_view` turns a loaded YAML mapping into a
frozen `ViewSpec` or raises `ViewError` naming the first broken rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from artmind.text2cypher import validate_domain_scoped, validate_read_only

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_MAX_ROWS = 200  # same cap as text2cypher.TEXT2CYPHER_MAX_ROWS

SCALAR_TYPES = ("string", "int", "float", "bool", "date")
PARAM_TYPES = SCALAR_TYPES + ("enum", "entity", "list")

_TOP_KEYS = {
    "name", "version", "domains", "summary", "examples", "params", "cypher",
    "max_rows", "presentation", "provenance",
}
_PARAM_KEYS = {"type", "description", "default", "min", "max", "entity_class", "values", "items"}

# format -> (required keys, optional keys) of its sub-block
_PRESENTATION_KEYS: dict[str, tuple[set[str], set[str]]] = {
    "table": ({"columns"}, {"headers"}),
    "list": ({"label"}, {"detail"}),
    "tree": ({"id", "parent", "label"}, set()),
    "mermaid_graph": ({"source", "target"}, {"edge_label"}),
    "mermaid_flow": ({"source", "target"}, {"edge_label"}),
}
_NO_DEFAULT = object()


class ViewError(ValueError):
    """A view (or a value given to it) breaks the contract."""


class ParamError(ViewError):
    """A parameter value is invalid for its declared spec."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str
    description: str = ""
    default: Any = _NO_DEFAULT
    min: float | None = None
    max: float | None = None
    entity_class: str | None = None
    values: tuple[str, ...] = ()
    items: str | None = None

    @property
    def required(self) -> bool:
        return self.default is _NO_DEFAULT

    def describe(self) -> str:
        parts = [self.type]
        if self.type == "enum":
            parts.append(f"one of {list(self.values)}")
        if self.type == "list":
            parts.append(f"of {self.items}")
        if self.min is not None:
            parts.append(f"min {self.min:g}")
        if self.max is not None:
            parts.append(f"max {self.max:g}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Presentation:
    format: str
    options: dict

    def to_dict(self) -> dict:
        return {"format": self.format, self.format: self.options}


@dataclass(frozen=True)
class ViewSpec:
    name: str
    version: int
    domains: tuple[str, ...]
    summary: str
    examples: tuple[str, ...]
    params: dict[str, ParamSpec]
    cypher: str
    max_rows: int
    presentation: Presentation
    provenance: dict
    raw: dict = field(repr=False, compare=False, default_factory=dict)

    def fingerprint(self) -> str:
        """Hash of what changes an answer: cypher + params + presentation."""
        canon = {
            "cypher": " ".join(self.cypher.split()),
            "params": jsonable(self.raw.get("params") or {}),
            "presentation": jsonable(self.raw.get("presentation")),
        }
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def to_dict(self) -> dict:
        return jsonable(self.raw)


def jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


# ── Cypher inspection ────────────────────────────────────────────────────────

_MASK_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|//[^\n]*|/\*.*?\*/", re.S)
_TERMINATOR_RE = re.compile(r"(ORDER\s+BY|SKIP|LIMIT)\b", re.I)


def _mask(cypher: str) -> str:
    """Blank out string literals and comments (same length) so keyword and
    `$param` scans never match inside them."""
    return _MASK_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), cypher)


def cypher_param_names(cypher: str) -> set[str]:
    return set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", _mask(cypher)))


def final_return_aliases(cypher: str) -> list[str]:
    """Column names of the LAST top-level RETURN clause.

    An item `expr AS alias` yields `alias`; an unaliased item yields its own
    text (which is how Neo4j names it). `RETURN *` is refused: its columns
    cannot be checked. Limitation: a `COLLECT { ... RETURN ... }` subquery
    inside the final RETURN would be mistaken for the final clause.
    """
    masked = _mask(cypher)
    matches = list(re.finditer(r"\bRETURN\b", masked, re.I))
    if not matches:
        raise ViewError("cypher has no RETURN clause")
    start = matches[-1].end()
    items: list[str] = []
    depth = 0
    item_start = start
    i = start
    while i < len(masked):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == ",":
            items.append(cypher[item_start:i])
            item_start = i + 1
        elif depth == 0 and (i == 0 or not (masked[i - 1].isalnum() or masked[i - 1] == "_")):
            if _TERMINATOR_RE.match(masked, i):
                break
        i += 1
    items.append(cypher[item_start:i])
    aliases: list[str] = []
    for n, raw in enumerate(items):
        text = " ".join(raw.split())
        if n == 0:
            text = re.sub(r"^DISTINCT\s+", "", text, flags=re.I)
        if text == "*":
            raise ViewError("RETURN * is not allowed: name every column with AS")
        m = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)$", text, re.I)
        aliases.append(m.group(1) if m else text)
    return aliases


# ── value coercion (shared by defaults here and CLI values in params.py) ─────

_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n"}


def _coerce_scalar(ptype: str, raw: Any) -> Any:
    if ptype == "string":
        if not isinstance(raw, str):
            raise ValueError(f"expected a string, got {raw!r}")
        return raw
    if ptype == "int":
        if isinstance(raw, bool):
            raise ValueError(f"expected an int, got {raw!r}")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            return int(raw.strip())
        raise ValueError(f"expected an int, got {raw!r}")
    if ptype == "float":
        if isinstance(raw, bool):
            raise ValueError(f"expected a number, got {raw!r}")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            return float(raw.strip())
        raise ValueError(f"expected a number, got {raw!r}")
    if ptype == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in _TRUE | _FALSE:
            return raw.strip().lower() in _TRUE
        raise ValueError(f"expected true/false, got {raw!r}")
    if ptype == "date":
        if isinstance(raw, datetime):
            return raw.date().isoformat()
        if isinstance(raw, date):
            return raw.isoformat()
        if isinstance(raw, str):
            return date.fromisoformat(raw.strip()).isoformat()
        raise ValueError(f"expected an ISO date, got {raw!r}")
    raise ValueError(f"unsupported scalar type {ptype!r}")


def coerce_value(spec: ParamSpec, raw: Any) -> Any:
    """Typed value for `raw` (a CLI string or a YAML default), or ParamError."""

    def fail(why: str) -> ParamError:
        return ParamError(f"param {spec.name!r} ({spec.describe()}): {why}")

    try:
        if spec.type in SCALAR_TYPES:
            value = _coerce_scalar(spec.type, raw)
        elif spec.type == "enum":
            value = str(raw)
            if value not in spec.values:
                raise ValueError(f"{value!r} is not one of {list(spec.values)}")
        elif spec.type == "list":
            parts = [p.strip() for p in raw.split(",") if p.strip()] if isinstance(raw, str) else raw
            if not isinstance(parts, (list, tuple)):
                raise ValueError(f"expected a list, got {raw!r}")
            return [_coerce_scalar(spec.items or "string", p) for p in parts]
        elif spec.type == "entity":
            return str(raw)
        else:
            raise ValueError(f"unsupported type {spec.type!r}")
    except (ValueError, TypeError) as exc:
        raise fail(str(exc)) from exc
    if spec.type in ("int", "float"):
        if spec.min is not None and value < spec.min:
            raise fail(f"{value} is below the minimum {spec.min:g}")
        if spec.max is not None and value > spec.max:
            raise fail(f"{value} is above the maximum {spec.max:g}")
    return value


# ── parsing ──────────────────────────────────────────────────────────────────


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ViewError(msg)


def _str_list(value: Any, what: str, *, lo: int = 1, hi: int | None = None) -> tuple[str, ...]:
    _need(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
          f"{what} must be a list of non-empty strings")
    _need(len(value) >= lo, f"{what} needs at least {lo} item(s)")
    _need(hi is None or len(value) <= hi, f"{what} allows at most {hi} items")
    return tuple(v.strip() for v in value)


def _parse_param(name: str, data: Any) -> ParamSpec:
    _need(NAME_RE.fullmatch(name) is not None, f"param name {name!r} must match {NAME_RE.pattern}")
    _need(name != "domains", "param name 'domains' is reserved: the runner always binds $domains")
    _need(isinstance(data, dict), f"param {name!r} must be a mapping")
    unknown = set(data) - _PARAM_KEYS
    _need(not unknown, f"param {name!r} has unknown keys {sorted(unknown)}")
    ptype = data.get("type")
    _need(ptype in PARAM_TYPES, f"param {name!r}: type must be one of {list(PARAM_TYPES)}")
    values = ()
    if ptype == "enum":
        values = _str_list(data.get("values"), f"param {name!r} values")
    else:
        _need("values" not in data, f"param {name!r}: values is only for enum")
    items = data.get("items")
    if ptype == "list":
        _need(items in SCALAR_TYPES, f"param {name!r}: list needs items: one of {list(SCALAR_TYPES)}")
    else:
        _need("items" not in data, f"param {name!r}: items is only for list")
    if ptype == "entity":
        ec = data.get("entity_class")
        _need(ec is None or isinstance(ec, str), f"param {name!r}: entity_class must be a string")
        _need("default" not in data, f"param {name!r}: an entity param cannot have a default")
    else:
        _need("entity_class" not in data, f"param {name!r}: entity_class is only for entity")
    lo, hi = data.get("min"), data.get("max")
    for label, bound in (("min", lo), ("max", hi)):
        if bound is not None:
            _need(ptype in ("int", "float"), f"param {name!r}: {label} is only for int/float")
            _need(isinstance(bound, (int, float)) and not isinstance(bound, bool),
                  f"param {name!r}: {label} must be a number")
    _need(lo is None or hi is None or lo <= hi, f"param {name!r}: min is above max")
    spec = ParamSpec(
        name=name, type=ptype, description=str(data.get("description") or ""),
        default=data["default"] if "default" in data else _NO_DEFAULT,
        min=lo, max=hi, entity_class=data.get("entity_class"), values=values, items=items,
    )
    if not spec.required:
        coerce_value(spec, spec.default)  # raises ParamError if the default breaks its own spec
    return spec


def _parse_presentation(data: Any, aliases: list[str]) -> Presentation:
    _need(isinstance(data, dict), "presentation must be a mapping")
    fmt = data.get("format")
    _need(fmt in _PRESENTATION_KEYS, f"presentation.format must be one of {sorted(_PRESENTATION_KEYS)}")
    extra = set(data) - {"format", fmt}
    _need(not extra, f"presentation: keys {sorted(extra)} do not match format {fmt!r}")
    block = data.get(fmt)
    _need(isinstance(block, dict), f"presentation.{fmt} block is required")
    required, optional = _PRESENTATION_KEYS[fmt]
    _need(required <= set(block), f"presentation.{fmt} needs {sorted(required)}")
    unknown = set(block) - required - optional
    _need(not unknown, f"presentation.{fmt} has unknown keys {sorted(unknown)}")
    columns: list[str] = []
    for key, value in block.items():
        if key == "columns":
            columns += list(_str_list(value, "presentation.table.columns"))
        elif key == "headers":
            _need(isinstance(value, dict) and all(isinstance(v, str) for v in value.values()),
                  "presentation.table.headers must map column -> label")
            _need(set(value) <= set(block["columns"]),
                  "presentation.table.headers names a column not in columns")
        else:
            _need(isinstance(value, str) and value, f"presentation.{fmt}.{key} must be a column name")
            columns.append(value)
    missing = [c for c in columns if c not in aliases]
    _need(not missing, f"presentation names columns {missing} that the final RETURN does not alias "
                       f"(RETURN has {aliases})")
    return Presentation(format=fmt, options=block)


def parse_view(data: Any, folder_name: str) -> ViewSpec:
    """Validate a loaded view.yaml mapping (spec section 4.4)."""
    _need(isinstance(data, dict), "view.yaml must be a mapping")
    _need("materialize" not in data, "`materialize` is not supported in v1 (views always run live)")
    unknown = set(data) - _TOP_KEYS
    _need(not unknown, f"unknown keys {sorted(unknown)}")
    missing = [k for k in ("name", "version", "domains", "summary", "examples", "cypher",
                           "presentation", "provenance") if k not in data]
    _need(not missing, f"missing required keys {missing}")

    name = data["name"]
    _need(isinstance(name, str) and NAME_RE.fullmatch(name) is not None,
          f"name must match {NAME_RE.pattern}")
    _need(name == folder_name, f"name {name!r} must equal the folder name {folder_name!r}")
    version = data["version"]
    _need(isinstance(version, int) and not isinstance(version, bool) and version > 0,
          "version must be a positive integer")
    domains = _str_list(data["domains"], "domains")
    summary = data["summary"]
    _need(isinstance(summary, str) and summary.strip() and "\n" not in summary.strip(),
          "summary must be a one-line string")
    examples = _str_list(data["examples"], "examples", hi=5)
    cypher = data["cypher"]
    _need(isinstance(cypher, str) and cypher.strip(), "cypher must be a non-empty string")
    max_rows = data.get("max_rows", DEFAULT_MAX_ROWS)
    _need(isinstance(max_rows, int) and not isinstance(max_rows, bool) and max_rows > 0,
          "max_rows must be a positive integer")

    try:
        validate_read_only(cypher)
        validate_domain_scoped(cypher)
    except ValueError as exc:
        raise ViewError(str(exc)) from exc

    raw_params = data.get("params") or {}
    _need(isinstance(raw_params, dict), "params must be a mapping")
    params = {pname: _parse_param(pname, pdata) for pname, pdata in raw_params.items()}

    used = cypher_param_names(cypher)
    undeclared = sorted(used - set(params) - {"domains"})
    _need(not undeclared, f"cypher uses undeclared parameters {['$' + u for u in undeclared]}")
    unused = sorted(set(params) - used)
    _need(not unused, f"declared parameters never used in cypher: {unused}")

    presentation = _parse_presentation(data["presentation"], final_return_aliases(cypher))

    prov = data["provenance"]
    _need(isinstance(prov, dict) and set(prov) <= {"created", "origin", "source_question"}
          and {"created", "origin"} <= set(prov),
          "provenance needs created and origin (and optionally source_question)")
    created = prov["created"]
    if isinstance(created, str):
        try:
            created = date.fromisoformat(created)
        except ValueError as exc:
            raise ViewError("provenance.created must be an ISO date") from exc
    _need(isinstance(created, date), "provenance.created must be an ISO date")
    _need(prov["origin"] in ("authored", "promoted"), "provenance.origin must be authored or promoted")
    sq = prov.get("source_question")
    _need(sq is None or isinstance(sq, str), "provenance.source_question must be a string or null")
    _need(prov["origin"] != "promoted" or bool(sq), "a promoted view needs provenance.source_question")

    return ViewSpec(
        name=name, version=version, domains=domains, summary=summary.strip(), examples=examples,
        params=params, cypher=cypher, max_rows=max_rows, presentation=presentation,
        provenance={"created": created.isoformat(), "origin": prov["origin"], "source_question": sq},
        raw=data,
    )
