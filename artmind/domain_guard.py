"""Tell a typo'd or invented ``--domain`` from a domain that simply has no rows.

Every query command answers a domain nothing has ever been ingested under with an
empty success -- ``rows: []``, ``total_entities: 0`` -- which reads exactly like
"this corpus has no data" (query-routing study P9: an agent took a vault's folder
name for a domain, got zeros from every command, and told the user to ingest
documents that were already there). ``unknown_domain_warnings`` names the
offending values and the real domains, so the empty result can be read for what it
is. It only ever *adds* a warning: nothing here fails a command.

A domain is known when a schema, a structured table or graph data sits at that
name or below it (a query for ``banking`` rolls up ``banking.policy``). The first
two are local reads; the graph is asked only when they leave a name unexplained,
because a query-only host may have no schemas at all and must not be told that its
real domains are unknown. If that check cannot run (Neo4j down) no warning is made.
"""
from __future__ import annotations

import difflib

from loguru import logger

_LISTED = 12  # known domains named in a message before it says "…and N more"


def _schema_domains() -> set[str]:
    from artmind.cli import _get_available_domains

    return set(_get_available_domains())


def _table_domains() -> set[str]:
    from artmind.structured import registry as structured_registry

    return {row["domain"] for row in structured_registry.list_tables(None) if row.get("domain")}


def _graph_domains() -> set[str]:
    from artmind.graph_query import _run_read_query

    rows = _run_read_query(
        "MATCH (n) WHERE n:Document OR n:Entity RETURN DISTINCT n._domain AS domain", {}
    )
    out: set[str] = set()
    for row in rows:
        value = row["domain"]
        out.update(v for v in (value if isinstance(value, list) else [value]) if v)
    return out


def _covers(name: str, known: set[str]) -> bool:
    """Is `name` a known domain, or the root of one (the rollup a query does)?"""
    return name in known or any(k.startswith(name + ".") for k in known)


def _message(name: str, known: set[str]) -> str:
    names = sorted(known)
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    parts = [f"unknown domain '{name}': no schema, table or graph data exists under that name."]
    if close:
        parts.append(f"Did you mean {', '.join(repr(c) for c in close)}?")
    if names:
        shown = ", ".join(names[:_LISTED])
        more = f" and {len(names) - _LISTED} more" if len(names) > _LISTED else ""
        parts.append(f"Known domains: {shown}{more}.")
    parts.append("An empty result for it does not mean the corpus has no data; "
                 "run `artmind query domains-overview` to see the real domains.")
    return " ".join(parts)


def unknown_domain_warnings(domains: list[str]) -> list[str]:
    """One warning per name in `domains` that matches nothing; [] when all are
    known or the question cannot be settled."""
    try:
        known = _schema_domains()
        try:
            known |= _table_domains()
        except Exception as exc:  # no structured store yet is normal
            logger.debug("domain guard: table domains unavailable: {}", exc)
        unexplained = [d for d in domains if not _covers(d, known)]
        if not unexplained:
            return []
        known |= _graph_domains()  # raises when Neo4j is down: then no warning
        return [_message(d, known) for d in unexplained if not _covers(d, known)]
    except Exception as exc:
        logger.debug("domain guard skipped: {}", exc)
        return []
