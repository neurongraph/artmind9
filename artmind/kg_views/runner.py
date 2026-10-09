"""Execute a view: bind params, run read-only, truncate, build the envelope."""

from __future__ import annotations

import re

from neo4j.exceptions import Neo4jError

from artmind.graph_query import (
    expand_domain_family,
    read_session,
    serialize_record,
    strip_internal_props,
)
from artmind.kg_views import params as params_mod
from artmind.kg_views.model import ViewError, ViewSpec
from artmind.kg_views.render import render_markdown
from artmind.kg_views.store import domain_covered


def _failure(spec: ViewSpec, exc: Neo4jError) -> ViewError:
    detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return ViewError(f"view {spec.name} v{spec.version} failed: {detail}")


def _expand(domains: list[str]) -> list[str]:
    out: list[str] = []
    for domain in domains:
        for member in expand_domain_family(domain):
            if member not in out:
                out.append(member)
    return out


def _run(spec: ViewSpec, bindings: dict) -> list[dict]:
    # Parameters go in as ONE dict (not **kwargs): a view param named `query` or
    # `parameters` would otherwise collide with Session.run's own arguments.
    try:
        with read_session() as session:
            return [
                strip_internal_props(serialize_record(rec))
                for rec in session.run(spec.cypher, bindings)
            ]
    except Neo4jError as exc:
        raise _failure(spec, exc) from exc


def _plan_tree(plan: dict | None) -> dict | None:
    if not plan:
        return None
    return {
        "operator": plan.get("operatorType"),
        "children": [_plan_tree(c) for c in plan.get("children") or []],
    }


def explain_plan(spec: ViewSpec, bindings: dict) -> dict | None:
    """Operator tree from `EXPLAIN <cypher>` (keeps a leading `CYPHER 25` prefix first)."""
    prefix = re.match(r"\s*(?:CYPHER\s+\S+\s+)?", spec.cypher).group(0)
    query = f"{prefix}EXPLAIN {spec.cypher[len(prefix):]}"
    try:
        with read_session() as session:
            summary = session.run(query, bindings).consume()
    except Neo4jError as exc:
        raise _failure(spec, exc) from exc
    return _plan_tree(summary.plan)


def run_view(
    spec: ViewSpec,
    domains: list[str],
    raw_params: dict[str, str],
    *,
    render: bool = False,
    explain: bool = False,
) -> dict:
    """The result envelope of `query views run` (spec section 6.3)."""
    uncovered = [d for d in domains if not domain_covered(d, spec.domains)]
    if uncovered:
        raise ViewError(
            f"view {spec.name!r} covers {list(spec.domains)}; it cannot run for {uncovered}"
        )
    expanded = _expand(domains)
    resolution = params_mod.resolve_params(spec, raw_params, expanded)
    envelope: dict = {
        "view": spec.name,
        "version": spec.version,
        "domains": domains,
        "status": resolution.status,
        "params": resolution.echo,
    }
    if resolution.status == "needs_disambiguation":
        envelope.update(param=resolution.param, candidates=resolution.candidates)
        return envelope
    if resolution.status == "no_match":
        envelope.update(param=resolution.param, input=resolution.input)
        return envelope

    bindings = {**resolution.bindings, "domains": expanded}
    rows = _run(spec, bindings)
    total = len(rows)
    kept = rows[: spec.max_rows]
    truncated = total > spec.max_rows
    envelope.update(
        rows=kept,
        rows_total=total,
        truncated=truncated,
        presentation=spec.presentation.to_dict(),
    )
    if render:
        envelope["rendered"] = render_markdown(kept, spec.presentation, truncated, total)
    if explain:
        envelope["plan"] = explain_plan(spec, bindings)
    return envelope
