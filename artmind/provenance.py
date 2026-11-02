"""Deterministic source citations for completed web-UI query traces.

This module deliberately works from the small, location-only trace evidence
record.  It neither reads corpus text nor asks Neo4j/LLMs to reconstruct a
citation.  Failure to resolve an optional cache file is represented as a
diagnostic, never an exception that could spoil an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paths
from artmind.db import _registry_row_by_artmind_id

_CHUNK_ID = re.compile(r"^(?P<doc_id>.+)_(?P<sequence>[0-9]{3,})$")


@dataclass(frozen=True)
class RenderedSources:
    markdown: str | None
    cited_documents: int
    cited_chunks: int
    diagnostics: list[str]


def select_final_evidence_span(spans: list[dict]) -> dict | None:
    """Newest successful, productive query span with renderable provenance."""
    for span in sorted(spans, key=lambda s: s.get("started_at") or 0, reverse=True):
        if span.get("exit_code") != 0 or not (span.get("rows") or 0):
            continue
        command = str(span.get("command") or "")
        if not command.startswith("query") or command in {"query domains-overview", "query plan"}:
            continue
        provenance = (span.get("evidence") or {}).get("provenance")
        if isinstance(provenance, dict) and (provenance.get("documents") or provenance.get("tables")):
            return span
    return None


def render_for_trace(spans: list[dict], vault: Path | None = None) -> RenderedSources:
    """Select, resolve and render one final source block for a completed turn."""
    span = select_final_evidence_span(spans)
    if span is None:
        return RenderedSources(None, 0, 0, [])
    provenance = (span.get("evidence") or {}).get("provenance") or {}
    return render(provenance, vault=vault)


def render(provenance: dict, *, vault: Path | None = None) -> RenderedSources:
    """Turn compact trace provenance into safe, vault-relative Markdown."""
    vault = Path(vault or paths.ARTMIND_VAULT_DIR) if (vault or paths.ARTMIND_VAULT_DIR) else None
    diagnostics: list[str] = []
    lines: list[str] = []
    documents = provenance.get("documents") if isinstance(provenance, dict) else []
    cited_documents = cited_chunks = 0
    for doc in documents if isinstance(documents, list) else []:
        if not isinstance(doc, dict):
            continue
        path = doc.get("source_path") or doc.get("path")
        if not isinstance(path, str):
            continue
        display = Path(path).name or path
        domain = doc.get("domain")
        suffix = f" — {domain}" if isinstance(domain, str) and domain else ""
        lines.append(f"- [{display}](<{path}>)" + suffix)
        cited_documents += 1
        for chunk in doc.get("chunks") or []:
            target = _chunk_target(doc, chunk, vault)
            if target is None:
                if isinstance(chunk, dict) and chunk.get("chunk_id"):
                    diagnostics.append(f"chunk cache unavailable for {chunk['chunk_id']}")
                continue
            label = _chunk_label(chunk)
            lines.append(f"  - [{label}](<{target}>)")
            cited_chunks += 1
    for table in provenance.get("tables") if isinstance(provenance, dict) else []:
        if not isinstance(table, dict) or not isinstance(table.get("source_file"), str):
            continue
        path = table["source_file"]
        domain = table.get("domain")
        suffix = f" — {domain} (table)" if isinstance(domain, str) and domain else " (table)"
        lines.append(f"- [{Path(path).name or path}](<{path}>)" + suffix)
        cited_documents += 1
    markdown = "### Sources\n" + "\n".join(lines) if lines else None
    return RenderedSources(markdown, cited_documents, cited_chunks, diagnostics)


def _chunk_label(chunk: dict) -> str:
    sequence = chunk.get("sequence")
    base = f"Passage: Chunk {sequence}" if isinstance(sequence, int) else "Passage"
    header = chunk.get("header_path")
    if isinstance(header, str) and header:
        base += f" — {header}"
    passages = chunk.get("passages") or []
    if passages and isinstance(passages[0], dict):
        start, end = passages[0].get("char_start"), passages[0].get("char_end")
        if isinstance(start, int) and isinstance(end, int):
            base += f" (characters {start}–{end})"
    return base


def _chunk_target(document: dict, chunk: Any, vault: Path | None) -> str | None:
    if vault is None or not isinstance(chunk, dict):
        return None
    chunk_id, doc_id = chunk.get("chunk_id"), document.get("doc_id")
    if not isinstance(chunk_id, str) or not isinstance(doc_id, str):
        return None
    match = _CHUNK_ID.match(chunk_id)
    if match is None or match.group("doc_id") != doc_id:
        return None
    sequence = int(match.group("sequence"))
    key = _chunk_key(doc_id, vault) or _recorded_chunk_key(document, doc_id, vault)
    if key is None:
        return None
    cache_root = (vault / ".artmind" / "data" / "documents" / "markdowns").resolve()
    candidate = cache_root / f"{key}_chunks" / f"chunk_{sequence:03d}.md"
    try:
        resolved = candidate.resolve()
        rel = resolved.relative_to(vault.resolve())
    except (OSError, ValueError):
        return None
    if resolved.is_file() and resolved.is_relative_to(cache_root):
        return rel.as_posix()
    return None


def _recorded_chunk_key(document: dict, doc_id: str, vault: Path) -> str | None:
    """The chunk key from the path the document itself recorded, for when the
    registry has no row for it (a rebuilt or partial registry, a vault whose
    graph outlived its registry).  Not a guess from the citation text: the
    path is the Document node's own, and ``_chunks_key`` applies the same rule
    the registry route does -- a converted binary's markdown is named by its
    key, a note's key comes from its vault path."""
    path, domain = document.get("path"), document.get("domain")
    if not isinstance(path, str) or not isinstance(domain, str) or not domain:
        return None
    try:
        registered = Path(path)
        if not registered.is_absolute():
            registered = vault / registered
        from artmind.ingest import _chunks_key

        return _chunks_key(domain, registered, doc_id)
    except Exception:
        return None


def _chunk_key(doc_id: str, vault: Path) -> str | None:
    """Find a document's one established cache key without path guessing.

    The registry supplies the registered path that owns ``doc_id``.  The
    ingestion helper then preserves all of its compatibility decisions: a
    moved source's previous key, legacy bare-stem folders, derived binary
    markdown, and collision-safe full keys.  A stale or absent registry is
    deliberately not recoverable from the human-facing citation path.
    """
    # It is only valid when the registry belongs to this live vault; a
    # query-only host, or a runtime configured to keep the registry elsewhere,
    # must simply omit the optional generated-chunk link.
    try:
        expected_db = (vault / ".artmind" / "data" / "document_registry.db").resolve()
        if paths.DB_PATH.resolve() != expected_db:
            return None
        row = _registry_row_by_artmind_id(doc_id)
        if not row or not isinstance(row.get("path"), str) or not isinstance(row.get("domain"), str):
            return None
        registered = Path(row["path"])
        if not registered.is_absolute():
            registered = vault / registered
        from artmind.ingest import _chunks_key

        return _chunks_key(row["domain"], registered, doc_id)
    except Exception:
        return None
