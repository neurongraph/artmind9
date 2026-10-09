"""Turn `--param k=v` strings into typed Cypher bindings (spec section 7).

Entity params are the interesting case: a name is resolved to an `_id` with
`vector_query.entity_resolve` under a deterministic, threshold-free rule; an
`@<_id>` value skips resolution but is still verified against the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from artmind import vector_query
from artmind.graph_query import read_session
from artmind.kg_views.model import ParamSpec, ViewError, ViewSpec, coerce_value

ENTITY_RESOLVE_TOP_K = 10
MAX_CANDIDATES = 5


@dataclass
class Resolution:
    """Outcome of binding a view's parameters.

    status "ok": `bindings` are the Cypher parameters (entity params as `_id`)
    and `echo` is what the envelope reports. Otherwise `param` names the
    entity parameter that stopped the run, with `candidates` or `input`.
    """

    status: str = "ok"
    bindings: dict = field(default_factory=dict)
    echo: dict = field(default_factory=dict)
    param: str | None = None
    input: str | None = None
    candidates: list = field(default_factory=list)


def parse_param_args(pairs: tuple[str, ...] | list[str]) -> dict[str, str]:
    """`k=v` strings (repeatable) -> dict. Split on the FIRST '='."""
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ViewError(f"--param {pair!r} must look like name=value")
        if key in out:
            raise ViewError(f"--param {key!r} was given more than once")
        out[key] = value
    return out


def _slim(entity: dict) -> dict:
    return {
        "_id": entity.get("_id"),
        "name": entity.get("name"),
        "entity_class": entity.get("entity_class"),
        "observation_count": entity.get("observation_count"),
    }


def _class_matches(entity: dict, wanted: str | None) -> bool:
    return wanted is None or str(entity.get("entity_class") or "").casefold() == wanted.casefold()


def _lookup_entity_id(spec: ParamSpec, entity_id: str, domains: list[str]) -> dict:
    """`@<_id>`: the node must exist, sit in the requested domains, match the class."""
    with read_session() as session:
        record = session.run(
            "MATCH (e:Entity {_id: $id}) "
            "RETURN e._id AS _id, e.name AS name, e.entity_class AS entity_class, e._domain AS domain",
            {"id": entity_id},
        ).single()
    if record is None:
        raise ViewError(f"param {spec.name!r}: no entity with _id {entity_id!r}")
    row = dict(record)
    domain = row.get("domain") or ""
    # Intentionally mirrors graph_query.domain_predicate: exact domain or child prefix.
    if not any(domain == d or domain.startswith(d + ".") for d in domains):
        raise ViewError(
            f"param {spec.name!r}: entity {entity_id!r} is in domain {domain!r}, "
            f"outside the requested domains {domains}"
        )
    if not _class_matches(row, spec.entity_class):
        raise ViewError(
            f"param {spec.name!r}: entity {entity_id!r} is a {row.get('entity_class')!r}, "
            f"expected {spec.entity_class!r}"
        )
    return row


def _resolve_entity_name(spec: ParamSpec, text: str, domains: list[str]) -> tuple[str, dict | list]:
    """Spec section 7 rule. Returns ("ok", entity) | ("needs_disambiguation", [..]) | ("no_match", [])."""
    result = vector_query.entity_resolve(domains, text, ENTITY_RESOLVE_TOP_K)
    candidates = [r["entity"] for r in result.get("rows", []) if r.get("entity")]
    candidates = [c for c in candidates if _class_matches(c, spec.entity_class)]
    exact = [c for c in candidates if str(c.get("name") or "").casefold() == text.casefold()]
    if len(exact) == 1:
        return "ok", exact[0]
    if len(candidates) == 1:
        return "ok", candidates[0]
    if candidates:
        return "needs_disambiguation", [_slim(c) for c in candidates[:MAX_CANDIDATES]]
    return "no_match", []


def resolve_params(spec: ViewSpec, raw: dict[str, str], domains: list[str]) -> Resolution:
    """Bind every declared param. `domains` is the family-expanded domain list."""
    unknown = sorted(set(raw) - set(spec.params))
    if unknown:
        raise ViewError(f"view {spec.name!r} has no parameter(s) {unknown}; it takes {sorted(spec.params)}")
    out = Resolution()
    entity_names = []
    for name, pspec in spec.params.items():
        if name not in raw:
            if pspec.required:
                raise ViewError(f"view {spec.name!r} needs --param {name}=<{pspec.describe()}>")
            value = coerce_value(pspec, pspec.default)
        elif pspec.type != "entity":
            value = coerce_value(pspec, raw[name])
        else:
            entity_names.append(name)
            continue
        out.bindings[name] = value
        out.echo[name] = value
    for name in entity_names:
        pspec = spec.params[name]
        text = raw[name].strip()
        if not text:
            raise ViewError(f"empty value for entity param {name}")
        if text.startswith("@"):
            entity = _lookup_entity_id(pspec, text[1:].strip(), domains)
        else:
            status, found = _resolve_entity_name(pspec, text, domains)
            if status != "ok":
                out.status, out.param, out.input = status, name, text
                out.candidates = found if status == "needs_disambiguation" else []
                return out
            entity = found
        out.bindings[name] = entity["_id"]
        out.echo[name] = {
            "input": text,
            "_id": entity["_id"],
            "name": entity.get("name"),
            "entity_class": entity.get("entity_class"),
        }
    return out
