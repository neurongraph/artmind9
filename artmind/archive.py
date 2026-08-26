"""`docs archive` / `restore-from-archive` / `archived` (docs/redesign-phase-
plan.md, Phase 5 "A"). The only removal artmind has — there is deliberately
no `purge`.

Archive produces a self-contained, portable bundle under
`ARTMIND_ARCHIVE_DIR/<artmind_id>/`: the staged KG JSON, the vault markdown,
the original binary (if the document came from one), and a manifest.
"Portable" and "the original is elsewhere" cannot both be true — everything
needed to reconstruct the document lives in the bundle. It then removes
every trace from the graph and from the vault (a real `git rm` + commit —
the one operation where artmind deletes human-authored content from the
user's repo), deletes the data-dir working copy of the original binary if
there was one, and appends one line to `index.jsonl`: the ONLY thing left
that still knows the document ever existed once the vault file is gone and
the graph is empty.

Restoring replays the bundle and lands the document back as
`_status=history`, never `latest` — archiving was a deliberate act, and
un-archiving must not silently change every query's answers. `docs restore`
promotes it afterwards if that's really wanted.

Deleting a bundle is a filesystem act, not an artmind command. That means
artmind has no single command satisfying right-to-erasure end to end — a
deliberate choice, not an oversight: `docs archive` is always recoverable via
`restore-from-archive` precisely because nothing here permanently destroys
the bundle itself.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind import vault_git
from artmind.db import _get_db
from artmind.document_identity import compute_content_sha256, resolve_canonical_path
from artmind.graph_query import neo4j_session, read_session
from artmind.lifecycle import resolve_document_id
from paths import ARTMIND_ARCHIVE_DIR, ARTMIND_VAULT_DIR, KG_DIR, ORIGINALS_DIR

INDEX_FILENAME = "index.jsonl"


class ArchiveCollision(Exception):
    """A restore-from-archive collision — the target vault path now holds a
    different file, or the id is already live. The same two-claimant refusal
    as `document_identity.IdentityConflict`; resolved with `--toPath` /
    `--newId`, mirroring `fork`/`adopt` there."""


# ── the index (the only record once a bundle's contents are gone) ──────────


def _index_path() -> Path:
    return ARTMIND_ARCHIVE_DIR / INDEX_FILENAME


def _append_index(entry: dict) -> None:
    ARTMIND_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with _index_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_archived() -> list[dict]:
    """Every bundle ever archived, read from the index — never the
    filesystem. That's the whole point of the index: once a vault file and
    its graph rows are gone, this is the only thing that still knows."""
    path = _index_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ── graph read/write ─────────────────────────────────────────────────────────


def _document_info(doc_id: str) -> dict:
    """Whatever the graph currently knows about `doc_id`, whichever label
    (`Document` or `DocumentHistory`) currently holds it. Empty if neither."""
    with read_session() as session:
        rec = session.run(
            "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $id "
            "RETURN d {.*} AS props LIMIT 1",
            id=doc_id,
        ).single()
    return dict(rec["props"]) if rec else {}


def _observation_valid_time_span(doc_id: str) -> tuple[str | None, str | None]:
    """The full valid-time window this document ever asserted, across every
    observation it ever contributed (both statuses) — not just the current
    version's own `_valid_from`/`_valid_to`, which only covers the latest."""
    with read_session() as session:
        rec = session.run(
            "MATCH (o) WHERE (o:Observation OR o:ObservationHistory) AND o.doc_id = $doc_id "
            "RETURN min(o._valid_from) AS valid_from, max(o._valid_to) AS valid_to",
            doc_id=doc_id,
        ).single()
    if not rec:
        return None, None
    return rec.get("valid_from"), rec.get("valid_to")


def _delete_document_tx(tx, doc_id: str) -> dict:
    """Remove `doc_id` from the graph entirely — both labels, Document,
    DocChunk and Observation alike — then rebuild whatever keys it fed.

    Not a label swap: archive REMOVES, it does not relabel (retire does
    that). Do not add a `:DocumentArchived` label here — see the module
    docstring and docs/document-identity.md's vocabulary.
    """
    from artmind import projection

    keys = projection.keys_for_document(tx, doc_id)

    obs = tx.run(
        "MATCH (o) WHERE (o:Observation OR o:ObservationHistory) AND o.doc_id = $doc_id "
        "DETACH DELETE o RETURN count(o) AS n",
        doc_id=doc_id,
    ).single()
    tx.run(
        "MATCH (c) WHERE (c:DocChunk OR c:DocChunkHistory) AND c.doc_id = $doc_id "
        "DETACH DELETE c",
        doc_id=doc_id,
    )
    tx.run(
        "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
        "DETACH DELETE d",
        doc_id=doc_id,
    )

    summary = projection.rebuild(tx, keys)
    return {
        "doc_id": doc_id,
        "observations_deleted": int(obs["n"]) if obs and obs.get("n") is not None else 0,
        "keys": sorted(keys),
        "projection": summary,
    }


# ── archive ───────────────────────────────────────────────────────────────────


def archive_document(domain: str, document_name: str) -> dict:
    """Archive one document: bundle it, remove it from the graph, remove its
    vault file (git rm + commit), remove its data-dir original if any, and
    record it in the index. Raises `ValueError` if it can't be found."""
    doc_id = resolve_document_id(document_name, domain)
    if not doc_id:
        raise ValueError(f"No document matching {document_name!r} in domain {domain!r}")

    info = _document_info(doc_id)
    if not info:
        raise ValueError(f"{doc_id!r} was resolved but is no longer in the graph")

    vault_rel_path = info.get("path")
    vault_path: Path | None = None
    if vault_rel_path:
        try:
            vault_path = resolve_canonical_path(str(vault_rel_path))
        except ValueError:
            vault_path = Path(str(vault_rel_path))

    from artmind.ingest import _parse_md_frontmatter  # local: avoid a cycle

    meta: dict = {}
    if vault_path is not None and vault_path.exists():
        meta, _ = _parse_md_frontmatter(vault_path.read_text(encoding="utf-8"))

    stem = vault_path.stem if vault_path is not None else Path(str(info.get("name") or doc_id)).stem
    source_type = meta.get("_source_type", "md")
    original_path: Path | None = None
    if source_type and source_type != "md":
        candidate = ORIGINALS_DIR / f"{stem}.{source_type}"
        if candidate.exists():
            original_path = candidate

    bundle_dir = ARTMIND_ARCHIVE_DIR / doc_id
    if bundle_dir.exists():
        raise ValueError(
            f"a bundle already exists at {bundle_dir} -- remove it by hand first "
            "if you mean to re-archive this id"
        )
    bundle_dir.mkdir(parents=True)

    if vault_path is not None and vault_path.exists():
        shutil.copy2(vault_path, bundle_dir / "document.md")
    if original_path is not None:
        shutil.copy2(original_path, bundle_dir / f"original.{source_type}")

    kg_dir = KG_DIR / domain / stem
    if kg_dir.exists():
        shutil.copytree(kg_dir, bundle_dir / "kg" / domain / stem)

    valid_from, valid_to = _observation_valid_time_span(doc_id)
    manifest = {
        "_artmind_id": doc_id,
        "title": meta.get("title") or info.get("title") or info.get("name") or stem,
        "domain": domain,
        "version": info.get("version"),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "original_vault_path": str(vault_rel_path) if vault_rel_path else None,
        "source_type": source_type,
        "has_original_binary": original_path is not None,
        "vault_commit": vault_git.current_commit(),
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _append_index({**manifest, "bundle_dir": str(bundle_dir)})

    with neo4j_session() as session:
        graph_result = session.execute_write(_delete_document_tx, doc_id)

    git_committed = False
    if vault_path is not None and vault_path.exists():
        git_committed = vault_git.remove_paths([vault_path], f"artmind: archive {stem} ({domain})")
        if not git_committed:
            # No vault/git repo, or the file wasn't tracked -- it still must
            # be gone from the vault; a plain delete is the fallback. Loud,
            # because there is no commit recording it, unlike every other
            # path through this function.
            vault_path.unlink(missing_ok=True)
            logger.warning(
                "archive {}: vault file removed WITHOUT a git commit (not a "
                "git repo, or the file wasn't tracked) -- no commit records "
                "this removal", stem,
            )

    if original_path is not None:
        original_path.unlink(missing_ok=True)

    conn = _get_db()
    try:
        conn.execute("DELETE FROM documents WHERE artmind_id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()

    logger.info("Archived {} ({}) -> {}", stem, doc_id, bundle_dir)
    return {
        "artmind_id": doc_id,
        "bundle_dir": str(bundle_dir),
        "manifest": manifest,
        "graph": graph_result,
        "git_committed": git_committed,
    }


# ── restore-from-archive ─────────────────────────────────────────────────────


def restore_from_archive(
    archive_id: str, *, to_path: str | None = None, new_id: str | None = None
) -> dict:
    """Replay a bundle: restore the vault file (and original binary, if any)
    and recommit the KG staging JSON to the graph, then immediately retire it
    (`_status=history`, never `latest` — see the module docstring for why).

    Refuses on either collision the spec calls out (same two-claimant rule
    as `document_identity.resolve_identity`'s `refuse` row): the target vault
    path already holds a *different* file, or `archive_id` is already live
    in the graph. `to_path` / `new_id` resolve either, matching
    `--fork`/`--adopt` there.
    """
    bundle_dir = ARTMIND_ARCHIVE_DIR / archive_id
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No archive bundle for {archive_id!r} at {bundle_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    domain = manifest["domain"]
    restore_id = new_id or archive_id

    if not new_id:
        existing = _document_info(restore_id)
        if existing:
            raise ArchiveCollision(
                f"{restore_id!r} is already live in the graph -- pass new_id= to "
                "restore under a fresh id instead"
            )

    target_rel = to_path or manifest.get("original_vault_path")
    if not target_rel:
        raise ValueError(
            "the archive manifest has no recorded vault path and no to_path was given"
        )
    if ARTMIND_VAULT_DIR is not None and not Path(target_rel).is_absolute():
        target_path = ARTMIND_VAULT_DIR / target_rel
    else:
        target_path = Path(target_rel)

    doc_md = bundle_dir / "document.md"
    if target_path.exists() and not to_path:
        if not doc_md.exists() or target_path.read_bytes() != doc_md.read_bytes():
            raise ArchiveCollision(
                f"{target_path} already holds a different file -- pass to_path= "
                "to restore elsewhere"
            )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if doc_md.exists():
        shutil.copy2(doc_md, target_path)

    if new_id and target_path.exists():
        from artmind.ingest import _parse_md_frontmatter
        from artmind.document_identity import render_document

        meta, body = _parse_md_frontmatter(target_path.read_text(encoding="utf-8"))
        meta["_artmind_id"] = new_id
        target_path.write_text(render_document(meta, body), encoding="utf-8")

    vault_git.commit_paths([target_path], f"artmind: restore-from-archive {archive_id}")

    source_type = manifest.get("source_type", "md")
    original_bundle_path = bundle_dir / f"original.{source_type}"
    if manifest.get("has_original_binary") and original_bundle_path.exists():
        ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original_bundle_path, ORIGINALS_DIR / f"{target_path.stem}.{source_type}")

    orig_stem = Path(str(manifest.get("original_vault_path") or archive_id)).stem
    bundle_kg_dir = bundle_dir / "kg" / domain / orig_stem
    doc_kg_dir = KG_DIR / domain / target_path.stem
    restored_kg = False
    if bundle_kg_dir.exists():
        if doc_kg_dir.exists():
            shutil.rmtree(doc_kg_dir)
        shutil.copytree(bundle_kg_dir, doc_kg_dir)
        restored_kg = True
        if new_id or to_path:
            # A fork/rename restore -- the staged JSON's own identity fields
            # still point at the archived original; re-point them so the
            # commit below writes under `restore_id`/`target_path`, not a
            # collision with the id/path this bundle was archived from.
            doc_json_path = doc_kg_dir / "document.json"
            if doc_json_path.exists():
                doc_json = json.loads(doc_json_path.read_text(encoding="utf-8"))
                doc_json["id"] = restore_id
                if "artmind_id" in doc_json:
                    doc_json["artmind_id"] = restore_id
                doc_json["path"] = str(target_path)
                doc_json["name"] = target_path.name
                doc_json_path.write_text(
                    json.dumps(doc_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    committed = False
    retire_result = None
    if restored_kg:
        from artmind.ingest import commit_to_graph
        from artmind.lifecycle import retire_document

        committed = commit_to_graph(doc_kg_dir, domain)
        if committed:
            retire_result = retire_document(restore_id, domain)
    else:
        logger.warning(
            "restore-from-archive {}: no KG staging JSON in the bundle -- the "
            "vault file (and original binary, if any) were restored, but "
            "nothing was recommitted to the graph", archive_id,
        )

    if target_path.exists():
        from artmind.ingest import _parse_md_frontmatter as _pmf

        _, restored_body = _pmf(target_path.read_text(encoding="utf-8"))
        from artmind.ingest import _register_document

        _register_document(
            domain, target_path, restore_id, content_sha256=compute_content_sha256(restored_body)
        )

    logger.info(
        "Restored {} from archive -> {} (status=history, committed={})",
        archive_id, target_path, committed,
    )
    return {
        "artmind_id": restore_id,
        "restored_path": str(target_path),
        "committed": committed,
        "retire": retire_result,
    }
