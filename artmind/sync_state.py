"""What the graph records about `vault sync` (spec 2026-09-26 §6 A2, A3).

Two things live in Neo4j so that every machine sharing one graph sees them:

- **The graph bookmark** -- one `(:ArtmindSyncState {vault_id,
  last_applied_commit, applied_at})` node per vault: "this graph reflects that
  vault's commits up to `last_applied_commit`". Keyed by the `vault_id` in the
  committed `.artmind/vault.yaml`, so two vaults sharing one AuraDB never share
  a bookmark, and a separate local Neo4j per machine carries its own.
- **Document fingerprints** -- `Document.fingerprint`, a digest of the staging
  folder a document was committed from (`staging_fingerprint`). `vault sync`
  skips a folder whose committed fingerprint already equals the graph's: on a
  shared AuraDB the ingesting machine already wrote it.

Every Cypher statement that touches either lives in this module and nowhere
else. The structured store's bookmark is machine-local (DuckDB is always a
local file) and lives in `state.json` -- see `artmind.vault_sync`.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

#: Created by `setup._setup_neo4j` (so by `artmind setup`, ingest's schema
#: check and every graph restore). One node per vault; the MERGE below keys on it.
CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT artmind_sync_state_vault_id IF NOT EXISTS "
    "FOR (n:ArtmindSyncState) REQUIRE n.vault_id IS UNIQUE"
)
CONSTRAINT_NAME = "artmind_sync_state_vault_id"

#: The staging files `ingest._load_staged` reads into a graph write, in the
#: fixed order they are hashed. The embedding sidecar is excluded (gitignored,
#: derived); so is anything else in the folder (the chunk cache, debug output),
#: since none of it reaches the graph.
FINGERPRINTED_FILES = ("document.json", "chunks.json", "observations.json", "relationships.json")

_READ_BOOKMARK = (
    "MATCH (s:ArtmindSyncState {vault_id: $vault_id}) "
    "RETURN s.last_applied_commit AS commit"
)
_WRITE_BOOKMARK = (
    "MERGE (s:ArtmindSyncState {vault_id: $vault_id}) "
    "SET s.last_applied_commit = $commit, s.applied_at = $applied_at"
)
_SET_FINGERPRINT = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"
_READ_FINGERPRINTS = (
    "MATCH (d:Document) WHERE d.id IN $doc_ids "
    "RETURN d.id AS id, d.fingerprint AS fingerprint"
)


def staging_fingerprint(read: Callable[[str], bytes | None]) -> str | None:
    """The fingerprint of one staging folder, or None when it has no
    `observations.json` (nothing to commit, so nothing can match).

    `read(name)` returns a file's raw bytes, or None when it is absent. The
    digest is sha256 over, for each name in `FINGERPRINTED_FILES` in order:
    the name, a NUL, then either `b"absent"` and a NUL, or the byte length in
    ASCII decimal, a NUL and the bytes themselves. Raw bytes, never parsed
    JSON: ingest hashes exactly what it wrote to disk, and `vault sync` hashes
    the committed blobs (`git cat-file`, no filters applied), which are the
    same bytes -- artmind's `.gitattributes` sets no text/eol/filter rule for
    `.artmind/data/**`. When a user's own rule does change them, the two
    digests differ and the folder is replayed: a mismatch can only cost a
    replay, never skip one."""
    if read("observations.json") is None:
        return None
    digest = hashlib.sha256()
    for name in FINGERPRINTED_FILES:
        digest.update(name.encode("ascii") + b"\0")
        data = read(name)
        if data is None:
            digest.update(b"absent\0")
        else:
            digest.update(str(len(data)).encode("ascii") + b"\0")
            digest.update(data)
    return digest.hexdigest()


def folder_fingerprint(doc_dir: Path) -> str | None:
    """`staging_fingerprint` of a staging folder on disk."""
    def _read(name: str) -> bytes | None:
        path = Path(doc_dir) / name
        return path.read_bytes() if path.is_file() else None

    return staging_fingerprint(_read)


def set_document_fingerprint(tx, doc_id: str, fingerprint: str | None) -> None:
    """Record `fingerprint` on `(:Document {id: doc_id})`, inside the commit
    transaction (`ingest._commit_document_tx`). None removes the property, so
    a document committed without one can never be skipped by `vault sync`."""
    tx.run(_SET_FINGERPRINT, doc_id=doc_id, fingerprint=fingerprint)


def _session(timeout: float | None):
    from artmind import graph_query

    return graph_query.neo4j_session(timeout=timeout)


def read_document_fingerprints(doc_ids: list[str], *, timeout: float | None = None) -> dict[str, str | None]:
    """`{doc_id: fingerprint}` for every id in `doc_ids` that is a live
    `:Document` (not `:DocumentHistory`), in ONE query. A document the graph
    does not have is absent from the result; one committed before fingerprints
    existed maps to None."""
    if not doc_ids:
        return {}
    with _session(timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, doc_ids=sorted(set(doc_ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def read_graph_bookmark(vault_id: str, *, timeout: float | None = None) -> str | None:
    """The commit this graph reflects for vault `vault_id`, or None."""
    with _session(timeout) as session:
        record = session.run(_READ_BOOKMARK, vault_id=vault_id).single()
    return record["commit"] if record else None


def write_graph_bookmark(vault_id: str, commit: str) -> None:
    """Advance (or create) vault `vault_id`'s graph bookmark to `commit`."""
    applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _session(None) as session:
        session.run(_WRITE_BOOKMARK, vault_id=vault_id, commit=commit, applied_at=applied_at)
