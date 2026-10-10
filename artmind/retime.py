"""`artmind ingest retime`: re-derive the valid time of already-staged documents.

A document's date is resolved once, at extraction (`ingest._document_valid_time`),
and frozen into its staging folder: `document.json`'s `_valid_from` /
`_valid_time_source`, and every observation's `_valid_from` / `_doc_valid_from`.
A schema change that would date the documents differently -- a new
`temporal.document_patterns` entry, say -- therefore reaches nothing already
ingested. This walks a domain's staging folders and recomputes those fields
with the same functions extraction uses, with no LLM call: the document's date
from its markdown, each fact's own date from its `temporal:`-tagged property
(already on the observation), falling back to the document's.

An undated document keeps the date first recorded for it (`prior=`), so a
retime never re-stamps "today".

`redo_properties=True` is the one LLM path: it re-runs only the properties step
of each document's chunks (one call per chunk, plus the per-document
canonicalization call), so a `temporal:`-tagged property such as
`date_or_time` is re-extracted with the document's date in the prompt
(`temporal.relative_anchor`). It needs the document's per-chunk extraction
cache on this machine, and skips a document without one rather than let
`extract_kg` treat the missing cache as "no entities".

Changed folders are committed through `commit_to_graph` (the one write path)
with the rebuild deferred, then the domain's projection is rebuilt once.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from loguru import logger

from artmind import ingest
from artmind.atomic_dir import is_scratch
from artmind.document_identity import resolve_canonical_path

#: The fields a retime owns -- everything else in a staging folder is left
#: byte-for-byte alone.
_DOCUMENT_TIME_KEYS = ("_valid_from", "_valid_to", "_valid_time_source")
_OBSERVATION_TIME_KEYS = ("_valid_from", "_valid_to", "_valid_time_source", "_doc_valid_from")


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def staged_markdown_path(document: dict) -> Path:
    """Where a staged document's markdown lives: the note itself for a
    vault-native document (`artmind_id`), the conversion output otherwise --
    `document_identity.markdown_path_for`'s rule, from `document.json` alone."""
    if document.get("artmind_id"):
        return resolve_canonical_path(str(document.get("path") or ""))
    return ingest.MARKDOWNS_DIR / f"{Path(str(document.get('name') or '')).stem}.md"


def retime_observations(observations: list[dict], document: dict, schema: dict) -> list[dict]:
    """`observations` with their valid time re-derived against `document`.

    The same decision `ingest._build_observations` + `build_observation` make:
    a fact's own date (its class's `temporal:` property) wins, else the
    document's; `_doc_valid_from` is always the document's."""
    from artmind.temporal import _entity_temporal_mapping

    entity_dates = _entity_temporal_mapping(schema)
    doc_from, doc_to = document.get("_valid_from"), document.get("_valid_to")
    out = []
    for obs in observations:
        fact_from, fact_to = ingest._fact_valid_time(entity_dates, obs.get("entity_class") or "", obs)
        new = {k: v for k, v in obs.items() if k not in _OBSERVATION_TIME_KEYS}
        times = {
            "_valid_from": fact_from or doc_from,
            "_valid_to": fact_to or doc_to,
            "_valid_time_source": "property" if fact_from else document.get("_valid_time_source"),
            "_doc_valid_from": doc_from,
        }
        new.update({k: v for k, v in times.items() if v})
        out.append(new)
    return out


def retime_folder(doc_kg_dir: Path, schema: dict, *, write: bool = True) -> dict:
    """Re-derive one staging folder's valid time; write it back if it changed.

    Returns `{"document", "before", "after", "source", "changed"}`, or
    `{"document", "skipped": reason}` for a folder it cannot retime."""
    document = _load_json(doc_kg_dir / "document.json")
    name = (document or {}).get("name") or doc_kg_dir.name
    if not isinstance(document, dict) or not document.get("id"):
        return {"document": name, "skipped": "no readable document.json"}
    if document.get("source_kind") == ingest.USER_CHAT_SOURCE_KIND:
        return {"document": name, "skipped": "update-session folder (dated by the chat)"}
    try:
        md_file = staged_markdown_path(document)
    except ValueError as e:
        return {"document": name, "skipped": str(e)}
    if not md_file.exists():
        return {"document": name, "skipped": f"markdown not found at {md_file}"}

    meta, _ = ingest._parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    dates = ingest._document_valid_time(md_file, meta, schema, filename=name, prior=document)
    new_document = {k: v for k, v in document.items() if k not in _DOCUMENT_TIME_KEYS}
    new_document.update(dates)

    observations = _load_json(doc_kg_dir / "observations.json", [])
    new_observations = retime_observations(observations, new_document, schema)

    changed = new_document != document or new_observations != observations
    if changed and write:
        ingest.write_staging(doc_kg_dir, {
            "document.json": new_document,
            "observations.json": new_observations,
        })
    return {
        "document": name,
        "before": document.get("_valid_from"),
        "after": new_document.get("_valid_from"),
        "source": new_document.get("_valid_time_source"),
        "changed": changed,
    }


def _reset_properties_step(doc_sha256: str) -> int:
    """Mark the properties step pending for every chunk whose entities are
    done, so `extract_kg` re-runs only that step. Returns the rows reset."""
    conn = ingest._get_db()
    try:
        cur = conn.execute(
            "UPDATE kg_chunk_status SET properties_status = 'pending'"
            " WHERE doc_sha256 = ? AND entities_status = 'ok'",
            (doc_sha256,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _has_complete_cache(doc_kg_dir: Path, sha: str) -> bool:
    """Every chunk's entities step done, and its cached output on disk."""
    statuses = ingest._get_chunk_statuses(sha)
    chunk_cache = doc_kg_dir / "chunks" / sha
    return bool(statuses) and all(
        s["entities_status"] == "ok" and (chunk_cache / f"chunk_{seq:03d}.json").exists()
        for seq, s in statuses.items()
    )


def _redo_properties(doc_kg_dir: Path, domain: str, text_model: str, embed_model: str) -> dict:
    """Re-extract one document's properties with its date in the prompt."""
    document = _load_json(doc_kg_dir / "document.json") or {}
    name = document.get("name") or doc_kg_dir.name
    file_result = ingest._build_file_result_from_db(name, domain)
    if file_result is None:
        return {"document": name, "skipped": "not in this machine's registry"}
    # A miss would not "redo properties": it would re-run every step,
    # entities included, against an empty cache.
    sha = file_result.get("sha256") or ""
    if not _has_complete_cache(doc_kg_dir, sha):
        return {"document": name, "skipped": "no complete per-chunk extraction cache on this machine"}
    _reset_properties_step(sha)
    before = document.get("_valid_from")
    if ingest.extract_kg(file_result, domain, text_model, embed_model) is None:
        return {"document": name, "skipped": "extract_kg failed -- see the log"}
    after = (_load_json(doc_kg_dir / "document.json") or {})
    return {
        "document": name,
        "before": before,
        "after": after.get("_valid_from"),
        "source": after.get("_valid_time_source"),
        "changed": True,
    }


def staging_folders(domain: str) -> list[Path]:
    root = ingest.KG_DIR / domain
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not is_scratch(d) and (d / "document.json").exists()
    )


def retime_domain(
    domain: str,
    *,
    dry_run: bool = False,
    redo_properties: bool = False,
    limit: int | None = None,
    documents: list[str] | None = None,
    text_model: str | None = None,
    embed_model: str | None = None,
) -> dict:
    """Retime every staged document of `domain`; commit and rebuild what changed."""
    schema_file = ingest.DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not schema_file.exists():
        raise FileNotFoundError(f"Domain schema not found: {schema_file}")
    from artmind.temporal import load_schema

    schema = load_schema(domain, schemas_dir=ingest.DOMAIN_SCHEMAS_DIR)  # inherits a parent's temporal

    folders = staging_folders(domain)
    if documents:
        # A folder is named by its document's stem: match "2026-01-07" or "2026-01-07.md".
        wanted = {Path(d).stem for d in documents}
        folders = [f for f in folders if f.name in wanted]
    if limit is not None:
        folders = folders[:limit]
    results, committed, failed = [], 0, []
    for folder in folders:
        try:
            if redo_properties and not dry_run:
                result = _redo_properties(folder, domain, text_model, embed_model)
            else:
                result = retime_folder(folder, schema, write=not dry_run)
        except Exception as e:  # one bad folder must not stop the domain
            logger.error("retime: {} failed: {}", folder.name, e)
            result = {"document": folder.name, "skipped": f"error: {e}"}
        results.append(result)
        if result.get("changed") and not dry_run:
            if ingest.commit_to_graph(folder, domain, defer_rebuild=True):
                committed += 1
            else:
                failed.append(result["document"])

    rebuild = ingest.rebuild_projection(domain) if committed else None
    changed = [r for r in results if r.get("changed")]
    return {
        "domain": domain,
        "dry_run": dry_run,
        "redo_properties": redo_properties,
        "scanned": len(results),
        "changed": len(changed),
        "committed": committed,
        "commit_failed": failed,
        "by_source": dict(Counter(r.get("source") for r in results if "skipped" not in r)),
        "skipped": [r for r in results if "skipped" in r],
        "sample": changed[:10],
        "rebuild": rebuild,
    }
