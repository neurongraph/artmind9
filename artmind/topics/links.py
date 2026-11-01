"""Where a cited chunk's derived markdown lives, for the page footnotes.

The path rule lives in `artmind.provenance` (passage-provenance spec): the key
comes from the registry through `ingest._chunks_key`, the file must exist under
`.artmind/data/documents/markdowns/<key>_chunks/chunk_NNN.md`. This is a thin
wrapper so footnotes and chat-ui/admin-ui citations resolve identically."""
from __future__ import annotations

from pathlib import Path

from artmind import provenance


def chunk_file(doc_id: str, chunk_id: str, vault: Path | None) -> str | None:
    """Vault-relative POSIX path of the chunk's markdown, or None."""
    if vault is None or not doc_id or not chunk_id:
        return None
    return provenance._chunk_target({"doc_id": doc_id}, {"chunk_id": chunk_id}, Path(vault))
