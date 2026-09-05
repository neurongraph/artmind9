"""Three-tier delta classifier for re-ingest (ADR 0006 (f), A4).

Given an incoming re-ingest of a document that already exists in the graph,
decide which of three tiers applies:

- ``metadata_only`` — body content matches the prior version block-for-block
  (same sequence of ``block_hash``es). Only frontmatter (title/project/area/tags/
  created_on/modified_on) or filesystem timestamps differ. The fast path is a
  bare Cypher SET on the Document node — no chunking, no LLM extraction, no
  supersede.
- ``content`` — body content differs (new/removed/reordered blocks, or any
  block_hash mismatch). Runs the full idempotent re-ingest pipeline (A1d).
- ``domain`` — the caller declares a domain change. Counts as content because
  the domain re-selects the extraction schema; must re-extract with the new
  schema. Same pipeline as ``content``.

For a document new to the graph, the tier is ``initial`` — a plain first ingest.

The classifier reads only what it needs from Neo4j and does NOT mutate the
graph; callers apply the fast path (metadata_only) or fall through to
``ingest_to_kg`` (content/domain/initial).
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Sequence

from loguru import logger

from artmind.ingest import _parse_md_frontmatter, _split_markdown


# ── data types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeltaResult:
    """The classifier's verdict.

    ``tier`` is one of: ``metadata_only``, ``content``, ``domain``, ``initial``.
    ``doc_id`` / ``version`` are the identity of the prior document node when
    one exists (``None`` for ``initial``). ``metadata`` is the frontmatter dict
    the caller can hand straight to the metadata-only fast path. ``reason`` is
    a one-line explanation for logs.
    """
    tier: str
    doc_id: str | None
    version: int | None
    metadata: dict
    reason: str


# ── fields the fast path may SET on the Document node ─────────────────────────
# Deliberately narrow: only fields A2 already reads from frontmatter, plus the
# filesystem-derived temporal defaults. Property names not listed here fall
# through to a content re-ingest so the pipeline stays authoritative.
METADATA_FIELDS = frozenset(
    {"title", "project", "area", "tags", "created_on", "modified_on"}
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _compute_body_block_hashes(md_text: str, chunk_size: int = 6000) -> list[str]:
    """The list of block_hash values that a fresh chunking would produce.

    This exercises the same _split_markdown path that ingestion uses, so a
    body identical to the prior version yields byte-identical hashes.
    """
    _, body = _parse_md_frontmatter(md_text)
    chunks = _split_markdown(body, chunk_size)
    return [c["block_hash"] for c in chunks]


def _read_prior_document(logical_id: str, domain: str) -> dict | None:
    """Look up the current Document node's identity and filing metadata.

    Returns None when no prior version exists or the graph is unreachable
    (either way, the caller treats it as ``initial`` and takes the normal path).
    """
    from artmind.graph_query import neo4j_session

    try:
        with neo4j_session() as session:
            rec = session.run(
                """
                MATCH (d:Document {logical_id: $lid, _domain: $domain})
                RETURN d.id AS id, d.version AS version, d.title AS title,
                       d.project AS project, d.area AS area, d.tags AS tags,
                       d.created_on AS created_on, d.modified_on AS modified_on
                ORDER BY coalesce(d.version, 1) DESC LIMIT 1
                """,
                lid=logical_id,
                domain=domain,
            ).single()
    except Exception as e:
        logger.warning("delta: prior-document lookup failed ({}); falling back to content path", e)
        return None
    if rec is None or not rec.get("id"):
        return None
    return dict(rec)


def _read_prior_block_hashes(doc_id: str) -> list[str] | None:
    """The block_hash sequence for the current version's chunks, ordered by
    chunk id (reading order — chunk ids are zero-padded so lexical sort ==
    document order). Returns None on lookup failure — same fallback semantics
    as _read_prior_document."""
    from artmind.graph_query import neo4j_session

    try:
        with neo4j_session() as session:
            rows = session.run(
                """
                MATCH (c:DocChunk {doc_id: $doc_id})
                WHERE c.block_hash IS NOT NULL
                RETURN c.block_hash AS h
                ORDER BY c.id
                """,
                doc_id=doc_id,
            ).data()
    except Exception as e:
        logger.warning("delta: prior-block-hash lookup failed ({}); falling back to content path", e)
        return None
    return [r["h"] for r in rows]


def _extract_metadata(meta: dict, source_path: Path) -> dict:
    """Project a frontmatter dict onto the METADATA_FIELDS surface, with the
    A2 defaults for created_on/modified_on. Mirrors extraction in ingest.py so
    a metadata-only apply matches what a full re-ingest would have written."""
    import datetime as _dt

    out: dict = {}
    if meta.get("title"):
        out["title"] = str(meta["title"])
    if meta.get("project"):
        out["project"] = str(meta["project"])
    if meta.get("area"):
        out["area"] = str(meta["area"])
    if meta.get("tags"):
        tags = meta["tags"]
        if isinstance(tags, str):
            out["tags"] = [t.strip() for t in tags.split(",")]
        elif isinstance(tags, list):
            out["tags"] = [str(t) for t in tags]
    if meta.get("created_on"):
        out["created_on"] = str(meta["created_on"])
    elif source_path.exists():
        st = source_path.stat()
        ctime = getattr(st, "st_birthtime", st.st_ctime)
        out["created_on"] = _dt.datetime.fromtimestamp(ctime, tz=_dt.timezone.utc).isoformat()
    if meta.get("modified_on"):
        out["modified_on"] = str(meta["modified_on"])
    elif source_path.exists():
        out["modified_on"] = _dt.datetime.fromtimestamp(
            source_path.stat().st_mtime, tz=_dt.timezone.utc
        ).isoformat()
    return out


# ── classifier ──────────────────────────────────────────────────────────────


def classify_reingest(
    md_path: Path,
    domain: str,
    logical_id: str,
    *,
    prior_domain: str | None = None,
    chunk_size: int = 6000,
) -> DeltaResult:
    """Classify an incoming re-ingest.

    ``md_path`` is the markdown source (the ``.md`` file in the data dir or the
    Vault). ``domain`` is the target domain of this ingest; ``prior_domain`` is
    the domain the file was last ingested under (defaults to ``domain`` — pass
    the old value explicitly when the caller knows the domain has changed).

    Never mutates the graph. Never raises for graph-lookup failures — those
    downgrade to ``initial`` so the normal pipeline stays in charge.
    """
    md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    meta, _body = _parse_md_frontmatter(md_text)
    incoming_metadata = _extract_metadata(meta, md_path)

    # Domain change short-circuits: schema re-selection forces re-extraction.
    if prior_domain and prior_domain != domain:
        prior = _read_prior_document(logical_id, prior_domain) or _read_prior_document(logical_id, domain)
        return DeltaResult(
            tier="domain",
            doc_id=prior.get("id") if prior else None,
            version=prior.get("version") if prior else None,
            metadata=incoming_metadata,
            reason=f"domain changed {prior_domain!r} → {domain!r}",
        )

    prior = _read_prior_document(logical_id, domain)
    if prior is None:
        return DeltaResult(
            tier="initial",
            doc_id=None,
            version=None,
            metadata=incoming_metadata,
            reason="no prior version",
        )

    prior_hashes = _read_prior_block_hashes(prior["id"])
    if prior_hashes is None:
        # Lookup failed — degrade to content path (safe default: re-extract).
        return DeltaResult(
            tier="content",
            doc_id=prior["id"],
            version=prior.get("version"),
            metadata=incoming_metadata,
            reason="prior block-hash lookup failed",
        )

    incoming_hashes = _compute_body_block_hashes(md_text, chunk_size=chunk_size)

    if incoming_hashes == prior_hashes and prior_hashes:
        return DeltaResult(
            tier="metadata_only",
            doc_id=prior["id"],
            version=prior.get("version"),
            metadata=incoming_metadata,
            reason=f"body unchanged ({len(prior_hashes)} block(s) match)",
        )

    # Any hash mismatch or count difference is a content change.
    return DeltaResult(
        tier="content",
        doc_id=prior["id"],
        version=prior.get("version"),
        metadata=incoming_metadata,
        reason=f"body changed (prior {len(prior_hashes)} blocks, new {len(incoming_hashes)})",
    )


# ── metadata-only fast path ─────────────────────────────────────────────────


def apply_metadata_only(
    doc_id: str,
    domain: str,
    metadata: dict,
) -> dict:
    """Fast path for a ``metadata_only`` classification.

    Writes the incoming filing metadata onto the existing Document node and
    denormalizes project/area/tags onto its DocChunks (ADR 0010 keeps the
    chunks in sync so filing filters stay index-backed). No re-extraction, no
    supersede, no history capture — the body is byte-identical to the prior
    version so there is nothing new to attribute or invalidate.

    Returns a summary suitable for logging / test assertions.
    """
    from artmind.graph_query import neo4j_session
    from artmind.ingest import _flatten_props

    incoming = _flatten_props({k: v for k, v in metadata.items() if k in METADATA_FIELDS})

    # Clear-out semantics: a field the user removed from frontmatter should
    # come off the node too. Union the incoming keys with the metadata surface
    # and REMOVE the ones not present in this update.
    to_remove = sorted(METADATA_FIELDS - set(incoming.keys()))
    remove_clause = ", ".join(f"d.{k} = null" for k in to_remove) if to_remove else ""

    set_clause = "d += $props" if incoming else ""
    if not set_clause and not remove_clause:
        return {"tier": "metadata_only", "doc_id": doc_id, "applied": {}, "removed": []}

    # Neo4j SET/REMOVE need commas between clauses on the same node.
    clauses = [c for c in (f"SET {set_clause}" if set_clause else "", f"SET {remove_clause}" if remove_clause else "") if c]
    doc_set = " ".join(clauses)

    # DocChunks carry the denormalized project/area/tags copy (ADR 0010 A2).
    chunk_fields = {k: incoming.get(k) for k in ("project", "area", "tags")}
    chunk_set = {k: v for k, v in chunk_fields.items() if v is not None}
    chunk_remove = [k for k in ("project", "area", "tags") if k not in chunk_set]

    with neo4j_session() as session:
        session.run(
            f"""
            MATCH (d:Document {{id: $doc_id, domain: $domain}})
            {doc_set}
            """,
            doc_id=doc_id,
            domain=domain,
            props=incoming,
        )

        if chunk_set:
            session.run(
                """
                MATCH (c:DocChunk {doc_id: $doc_id})
                SET c += $props
                """,
                doc_id=doc_id,
                props=chunk_set,
            )
        if chunk_remove:
            chunk_remove_clause = ", ".join(f"c.{k}" for k in chunk_remove)
            session.run(
                f"""
                MATCH (c:DocChunk {{doc_id: $doc_id}})
                REMOVE {chunk_remove_clause}
                """,
                doc_id=doc_id,
            )

    return {
        "tier": "metadata_only",
        "doc_id": doc_id,
        "applied": incoming,
        "removed": to_remove,
    }
