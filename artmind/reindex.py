"""Rebuild the registry (`document_registry.db`'s path <-> id cache) from
vault frontmatter (docs/document-identity.md; docs/redesign-phase-plan.md,
Phase 5 "D"). The registry is never authoritative — `docs/document-
identity.md` says so directly — so wiping and rebuilding it is always safe.

Buildable now that binaries carry `_artmind_id` too, via their converted
markdown's own frontmatter (`_ingest_binary_derived`, docs/vault.md, "The
ownership rule"): before that, a binary had nothing in its own frontmatter to
rebuild an identity from, so this command had nothing to read.

csv/xlsx stay unrebuildable by design ("accepted limitation",
docs/document-identity.md, "Sources that cannot carry frontmatter") — a
tabular source's identity is path-only, and losing the registry loses it for
good. `reindex` reports these as a known gap (any `.md` with no `_artmind_id`
is reported too, for the same reason) rather than silently doing nothing
about them.

Identity (`_artmind_id`, `_domain`) comes from each note's frontmatter; the
content hash from the staging folder's `document.json` (spec 2026-09-26 R6;
stem lookup only -- a reindex over thousands of notes must not scan a domain
per note), a pre-R6 note's own `_content_sha256` as the fallback, else the
body itself.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from artmind.db import _get_db
from artmind.document_identity import canonical_path, compute_content_sha256
from paths import ARTMIND_VAULT_DIR

_ACCEPTED_LIMITATION_NOTE = (
    "csv/xlsx sources are path-only and cannot be rebuilt from anything "
    "(accepted limitation, docs/document-identity.md) -- re-ingest them "
    "directly to re-register. Any .md listed under skipped_no_id is either "
    "not an artmind document, or lost its frontmatter and needs a `heal` "
    "re-ingest instead of a reindex."
)


def _iter_vault_markdown(vault_dir: Path):
    for path in sorted(vault_dir.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault_dir).parts):
            continue
        yield path


def reindex() -> dict:
    """Scan every markdown file in the vault for `_artmind_id` frontmatter
    and rewrite the registry's id-bearing rows from what's found. Path-only
    rows (binaries still on the pre-Phase-5 `logical_id` path, or csv/xlsx)
    are left untouched — reindex has nothing to rebuild them from either way.
    """
    if ARTMIND_VAULT_DIR is None:
        raise RuntimeError("ARTMIND_VAULT_DIR is not configured -- nothing to reindex from")

    from artmind.document_identity import ingest_baseline
    from artmind.ingest import _parse_md_frontmatter, staged_document  # local: avoid a cycle

    registered = 0
    skipped_no_id: list[str] = []
    now = datetime.now().isoformat()

    conn = _get_db()
    try:
        conn.execute("DELETE FROM documents WHERE artmind_id IS NOT NULL")
        for path in _iter_vault_markdown(ARTMIND_VAULT_DIR):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("reindex: could not read {}: {}", path, e)
                continue
            meta, body = _parse_md_frontmatter(text)
            artmind_id = meta.get("_artmind_id")
            if not artmind_id:
                skipped_no_id.append(str(path))
                continue
            # `_domain` is hand-editable: only a plain directory name may reach
            # the staging lookup (`kg/<domain>/`), and only a string can be
            # stored. Anything else is "no domain": the body hash below.
            domain = meta.get("_domain")
            domain = domain if isinstance(domain, str) else ""
            safe_domain = domain if domain and "/" not in domain and ".." not in domain else None
            staged = staged_document(safe_domain, [path.stem], artmind_id)
            content_sha256 = ingest_baseline(staged, meta)["_content_sha256"] or compute_content_sha256(body)
            conn.execute(
                """
                INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artmind_id) DO UPDATE SET
                    domain = excluded.domain,
                    path = excluded.path,
                    content_sha256 = excluded.content_sha256,
                    last_ingested_at = excluded.last_ingested_at
                """,
                (artmind_id, domain, canonical_path(path), content_sha256, now),
            )
            registered += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "docs reindex: {} document(s) registered from vault frontmatter, {} "
        "markdown file(s) skipped (no _artmind_id)", registered, len(skipped_no_id),
    )
    return {
        "registered": registered,
        "skipped_no_id": skipped_no_id,
        "note": _ACCEPTED_LIMITATION_NOTE,
    }
