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


def select_evidence_spans(spans: list[dict]) -> list[dict]:
    """Every successful, productive query span with renderable provenance,
    newest first (so the first is the final, main evidence)."""
    out = []
    for span in sorted(spans, key=lambda s: s.get("started_at") or 0, reverse=True):
        if span.get("exit_code") != 0 or not (span.get("rows") or 0):
            continue
        command = str(span.get("command") or "")
        if not command.startswith("query") or command == "query domains-overview":
            continue
        provenance = (span.get("evidence") or {}).get("provenance")
        if isinstance(provenance, dict) and (provenance.get("documents") or provenance.get("tables")):
            out.append(span)
    return out


def select_final_evidence_span(spans: list[dict]) -> dict | None:
    """Newest successful, productive query span with renderable provenance."""
    found = select_evidence_spans(spans)
    return found[0] if found else None


def merge_provenance(provenances: list[dict]) -> dict:
    """One provenance from several spans' (main evidence first).  A document
    seen again keeps its first position; its chunks are combined, first seen
    first, and a missing path/title/domain is filled from the later sighting."""
    documents: dict[Any, dict] = {}
    tables: dict[tuple, dict] = {}
    for provenance in provenances:
        for doc in provenance.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            key = doc.get("doc_id") or doc.get("source_path") or doc.get("path")
            if key is None:
                continue
            kept = documents.get(key)
            if kept is None:
                documents[key] = {**doc, "chunks": [dict(c) for c in doc.get("chunks") or [] if isinstance(c, dict)]}
                continue
            for field in ("path", "source_path", "domain", "title"):
                if kept.get(field) is None and doc.get(field) is not None:
                    kept[field] = doc[field]
            seen = {c.get("chunk_id") for c in kept["chunks"]}
            for chunk in doc.get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("chunk_id") not in seen:
                    kept["chunks"].append(dict(chunk))
                    seen.add(chunk.get("chunk_id"))
        for table in provenance.get("tables") or []:
            if isinstance(table, dict):
                tables.setdefault((table.get("source_file"), table.get("domain")), table)
    return {"documents": list(documents.values()), "tables": list(tables.values())}


def render_for_trace(spans: list[dict], vault: Path | None = None) -> RenderedSources:
    """Render one source block for a completed turn: the final productive
    query's documents first (the main evidence), then those of the turn's
    other productive queries."""
    selected = select_evidence_spans(spans)
    if not selected:
        return RenderedSources(None, 0, 0, [])
    return render(merge_provenance([(s.get("evidence") or {}).get("provenance") or {} for s in selected]), vault=vault)


def render(provenance: dict, *, vault: Path | None = None) -> RenderedSources:
    """Turn compact trace provenance into safe, vault-relative Markdown."""
    vault = Path(vault or paths.ARTMIND_VAULT_DIR) if (vault or paths.ARTMIND_VAULT_DIR) else None
    diagnostics: list[str] = []
    lines: list[str] = []
    documents = provenance.get("documents") if isinstance(provenance, dict) else []
    cited_documents = cited_chunks = 0
    staged = _StagedIndex(vault)
    for doc in documents if isinstance(documents, list) else []:
        if not isinstance(doc, dict):
            continue
        path = doc.get("source_path") or doc.get("path")
        if not isinstance(path, str):
            # A `--shape brief` row names the document by id and title only.
            path = _source_path_for(doc, vault)
            if path is None:
                recorded = staged.get(doc)
                if recorded is not None:
                    path = recorded.get("source_path") or recorded.get("path")
                    # The chunk folder is found from the path the document recorded.
                    doc = {**doc, "path": recorded.get("path") or path}
        domain = doc.get("domain")
        suffix = f" — {domain}" if isinstance(domain, str) and domain else ""
        if isinstance(path, str):
            lines.append(f"- [{Path(path).name or path}](<{path}>)" + suffix)
        elif isinstance(doc.get("title"), str) and doc["title"]:
            # Cited, but its path is unknown here: name it without a link.
            lines.append(f"- {doc['title']}" + suffix)
        else:
            continue
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


class _StagedIndex:
    """`id -> document.json` of a domain's staging folders, for a document the
    registry has no row for (a registry that was rebuilt or only partly
    filled while the staging folders and graph are whole).  Read lazily, once
    per render and domain; any unreadable folder is skipped."""

    def __init__(self, vault: Path | None) -> None:
        self._root = (vault / ".artmind" / "data" / "kg") if vault is not None else None
        self._by_domain: dict[str, dict[str, dict]] = {}

    def get(self, document: dict) -> dict | None:
        doc_id, domain = document.get("doc_id"), document.get("domain")
        if self._root is None or not isinstance(doc_id, str) or not isinstance(domain, str) or not domain:
            return None
        if domain not in self._by_domain:
            self._by_domain[domain] = self._scan(domain)
        return self._by_domain[domain].get(doc_id)

    def _scan(self, domain: str) -> dict[str, dict]:
        import json

        found: dict[str, dict] = {}
        try:
            folders = sorted((self._root / domain).iterdir())
        except OSError:
            return found
        for folder in folders:
            try:
                record = json.loads((folder / "document.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            ident = record.get("artmind_id") or record.get("id")
            if isinstance(ident, str) and (isinstance(record.get("path"), str) or isinstance(record.get("source_path"), str)):
                found[ident] = record
        return found


def _source_path_for(document: dict, vault: Path | None) -> str | None:
    """The vault-relative path a cited document is shown under, for a document
    the trace recorded by id only.  Same answer a full-shape row gives: the
    original a binary was converted from, else the registered note.  Optional:
    any miss returns None and the caller cites the title."""
    doc_id = document.get("doc_id")
    if vault is None or not isinstance(doc_id, str):
        return None
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
        from artmind.ingest import _converted_owner, _is_derived_markdown

        if _is_derived_markdown(registered):
            owner = _converted_owner(registered.stem, row["domain"])
            return owner[0] if owner else None
        return row["path"]
    except Exception:
        return None


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
