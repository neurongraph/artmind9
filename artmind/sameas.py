"""Same-as PROPOSALS — the review queue between a proposer and `same_as.yaml`.

Two proposers feed one queue, both landing on `:SameAsProposal` nodes:

- `conflicts.py`'s `candidate_pairs` + `llm_adjudicate` (cross-domain,
  blocked by `entity_class` via the `entity_embedding` ANN index) — its
  `"same_entity_consistent"` verdict, previously discarded, now proposes a
  group instead of nothing. See `conflicts.materialize`.
- `refine_graph.py`'s intra-class name clustering + merge-resolution prompt
  (naming variants a per-document canonicalization pass didn't catch across
  documents) — its destructive apply (`apoc.mergeNodes`) is gone; the
  clustering and the prompt survive as this queue's other producer. See
  `refine_graph.propose_merges`.

A proposal names a canonical member and its group — the same shape
`same_as.yaml` itself uses (`same_as.py`'s `group[0]` = canonical
convention) — so approving one is a direct append, not a translation step.

This module owns the queue's lifecycle (`propose` / `list_proposals` /
`approve` / `reject`); it never generates candidates itself — "don't write a
second proposer."
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.observations import key_string


def _parse_key(value: str) -> tuple[str, str, str] | None:
    parts = str(value).split("|")
    return tuple(parts) if len(parts) == 3 else None  # type: ignore[return-value]


def proposal_id(canonical: str, members: list[str]) -> str:
    """Deterministic id from (canonical, sorted members) — re-proposing the
    identical group updates it in place (`MERGE`) rather than duplicating."""
    payload = canonical + "::" + "|".join(sorted(members))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def propose(
    session,
    canonical: tuple[str, str, str],
    members: list[tuple[str, str, str]],
    *,
    source: str,
    reason: str = "",
    model: str = "",
) -> str:
    """Write (`MERGE`) one same-as proposal. `members` need not already
    include `canonical` — it is added if missing."""
    all_members = sorted({key_string(canonical)} | {key_string(m) for m in members})
    canonical_str = key_string(canonical)
    pid = proposal_id(canonical_str, all_members)
    session.run(
        """
        MERGE (p:SameAsProposal {id: $id})
        ON CREATE SET p.status = 'open', p.detected_at = $now
        SET p.canonical = $canonical, p.members = $members, p.source = $source,
            p.reason = $reason, p.detected_by_model = $model
        """,
        id=pid, canonical=canonical_str, members=all_members,
        source=source, reason=reason, model=model,
        now=datetime.now(timezone.utc).isoformat(),
    )
    logger.info("sameas: proposed {} <- {} (source={})", canonical_str, all_members, source)
    return pid


def list_proposals(status: str | None = "open") -> list[dict]:
    """The review queue. `status='all'` (or `None`) lists every proposal
    regardless of status; otherwise filters to it (`open`, `approved`,
    `rejected`)."""
    clause = "" if not status or status == "all" else "WHERE p.status = $status"
    with neo4j_session() as session:
        rows = session.run(
            f"MATCH (p:SameAsProposal) {clause} "
            "RETURN properties(p) AS p ORDER BY p.detected_at DESC",
            status=status,
        ).data()
    return [r.get("p") for r in rows]


def get_proposal(proposal_id_: str) -> dict | None:
    with neo4j_session() as session:
        rec = session.run(
            "MATCH (p:SameAsProposal {id: $id}) RETURN properties(p) AS p", id=proposal_id_
        ).single()
    return rec.get("p") if rec else None


def approve(proposal_id_: str, *, canonical: str | None = None) -> dict:
    """Approve a proposal: append its group to `same_as.yaml`, mark it
    approved, and run a projection rebuild scoped to the touched top-level
    domain families.

    A full (not incremental) rebuild, scoped by domain family — `sameas
    approve` is rare and human-triggered, not per-document, so correctness
    over incrementality: an incremental rebuild seeded from a hand-picked key
    set risks missing a member whose own document commit predates the group
    (see `projection._plan_groups`'s docstring on what a caller must
    guarantee).

    `canonical` overrides which member becomes canonical (must be one of the
    proposal's members); omitted, the proposer's own suggestion is used.

    This does NOT clear `:ProjectionState`'s drift flag — that only happens
    on a truly full (`--domain` omitted) `projection rebuild`, since this
    rebuild is scoped to the domains this one group touches, not everything
    `same_as.yaml` as a whole could affect.
    """
    from artmind import projection, same_as

    proposal = get_proposal(proposal_id_)
    if not proposal:
        raise ValueError(f"No SameAsProposal with id {proposal_id_!r}")
    if proposal.get("status") != "open":
        raise ValueError(f"Proposal {proposal_id_!r} is already {proposal['status']!r}, not open")

    canonical_str = canonical or proposal["canonical"]
    members_str = list(proposal.get("members") or [])
    if canonical_str not in members_str:
        raise ValueError(
            f"canonical {canonical_str!r} is not among the proposal's members {members_str}"
        )
    canonical_key = _parse_key(canonical_str)
    member_keys = [k for m in members_str if (k := _parse_key(m))]
    if not canonical_key or len(member_keys) < 2:
        raise ValueError(f"Proposal {proposal_id_!r} has too few valid keys to form a group")
    group = [canonical_key] + [m for m in member_keys if m != canonical_key]

    groups = same_as.load_groups()
    groups.append(group)
    same_as.save_groups(groups)

    with neo4j_session() as session:
        session.run(
            """
            MATCH (p:SameAsProposal {id: $id})
            SET p.status = 'approved', p.resolved_at = $now, p.approved_canonical = $canonical
            """,
            id=proposal_id_, now=datetime.now(timezone.utc).isoformat(), canonical=canonical_str,
        )
        # The FULL domain string per touched key, not its top-level family.
        # `projection.full_rebuild`'s domain scoping already rolls a domain
        # UP to its descendants via `STARTS WITH (d + '.')` (see
        # `all_keys`/`domain_predicate`) — passing the truncated family name
        # (e.g. "banking") is therefore indistinguishable from a corpus-wide
        # rebuild whenever every real domain nests one level under it, which
        # is true of every domain in this schema set today. See
        # neurongraph/artmind9#12.
        touched_domains = sorted({k[2] for k in group if k[2]})
        summary = session.execute_write(
            lambda tx: projection.full_rebuild(
                tx, touched_domains or None,
                synthesis_loader=lambda k: projection.load_synthesis(tx, k),
            )
        )
    logger.info("sameas: approved {} -> group of {} member(s)", proposal_id_, len(group))
    return {
        "id": proposal_id_, "status": "approved",
        "canonical": canonical_str, "group": [key_string(k) for k in group],
        "projection": summary,
    }


def reject(proposal_id_: str, reason: str | None = None) -> dict:
    with neo4j_session() as session:
        rec = session.run(
            """
            MATCH (p:SameAsProposal {id: $id})
            SET p.status = 'rejected', p.resolved_at = $now, p.resolution_reason = $reason
            RETURN p.id AS id, p.status AS status
            """,
            id=proposal_id_, now=datetime.now(timezone.utc).isoformat(), reason=reason,
        ).single()
    if not rec:
        raise ValueError(f"No SameAsProposal with id {proposal_id_!r}")
    logger.info("sameas: rejected {} ({})", proposal_id_, reason or "no reason given")
    return {"id": rec["id"], "status": rec["status"], "reason": reason}
