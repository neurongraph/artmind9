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

`redo_properties=True` is the one LLM path: it runs `backfill.backfill_domain` once
over every class that declares a `temporal:` property, targeting only those
`temporal:`-tagged keys, so a property such as `date_or_time` is re-asked with the
document's date in the prompt (`temporal.relative_anchor`); the class's other
declared keys are neither asked nor changed. One call per staged chunk
(`chunks.json`, no chunk cache needed) that holds an observation of such a class;
every other observation is never shown to the model, names and ids never change,
and a key the model does not answer keeps its value.

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
from artmind.observations import normalize_property_keys, property_key_rules

#: The fields a retime owns -- everything else in a staging folder is left
#: byte-for-byte alone.
_DOCUMENT_TIME_KEYS = ("_valid_from", "_valid_to", "_valid_time_source", "_valid_precision", "_revised_at")
_OBSERVATION_TIME_KEYS = (
    "_valid_from", "_valid_to", "_valid_time_source", "_valid_precision", "_valid_time_of_day",
    "_doc_valid_from", "_due", "_review",
)


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
    if document.get("path"):
        recorded = resolve_canonical_path(str(document["path"]))
        if recorded.exists():
            return recorded
    return ingest._markdown_path(Path(str(document.get("name") or "")).stem)


def retime_observations(observations: list[dict], document: dict, schema: dict) -> tuple[list[dict], dict]:
    """(`observations` with valid time re-derived and undeclared keys dropped, {observation id:
    {dropped key: value}}): the decisions `ingest._build_observations` + `build_observation` make.

    A fact's own date (its class's `temporal:` property) wins, else the document's;
    `_doc_valid_from` is always the document's. Property keys are normalized
    (`observations.normalize_property_keys`) and a key the observation's class does not declare
    is dropped (spec 2026-11-15 §2.4). A table row's observation (`source_kind: table`) keeps its
    mapping's keys (table2graph is out of scope, spec 2026-11-15)."""
    from artmind.observations import declared_property_keys, filter_declared
    from artmind.temporal import _entity_temporal_mapping

    entity_dates = _entity_temporal_mapping(schema)
    declared = declared_property_keys(schema)
    key_rules = property_key_rules(schema)
    doc_from, doc_to = document.get("_valid_from"), document.get("_valid_to")
    out, dropped = [], {}
    for obs in observations:
        cls = obs.get("entity_class") or ""
        obs = normalize_property_keys(obs, *key_rules)
        if obs.get("source_kind") != "table":
            obs, gone = filter_declared(obs, cls, declared)
            if gone:
                # An id-less observation is keyed by its key, else its position: never "" (two
                # such observations would collide in the sidecar).
                dropped[str(obs.get("id") or obs.get("key") or f"#{len(out)}")] = gone
        fact = ingest._fact_valid_time(entity_dates, cls, obs)
        new = {k: v for k, v in obs.items() if k not in _OBSERVATION_TIME_KEYS}
        times = {
            "_valid_from": fact.valid_from or doc_from,
            "_valid_to": fact.valid_to or doc_to,
            "_valid_time_source": "property" if fact.valid_from else document.get("_valid_time_source"),
            "_valid_precision": (
                fact.precision if fact.valid_from else ingest._document_precision(document, schema)
            ),
            "_valid_time_of_day": fact.time_of_day,
            "_doc_valid_from": doc_from,
            "_due": fact.due,
            "_review": fact.review,
        }
        new.update({k: v for k, v in times.items() if v})
        out.append(new)
    return out, dropped


def _keep_dropped(doc_kg_dir: Path, name: str, dropped: dict) -> None:
    """Move dropped values beside the run (private, gitignored), merged per observation with
    earlier runs, so a later run dropping another key keeps the first run's values."""
    import os
    import tempfile

    sidecar = ingest.LOGS_DIR / "retime-dropped" / doc_kg_dir.parent.name / f"{doc_kg_dir.name}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_json(sidecar, {}) or {}
    merged = dict(prior.get("values") or {})
    for oid, vals in dropped.items():
        merged[oid] = {**(merged.get(oid) or {}), **vals}
    text = json.dumps({"document": name, "values": merged}, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".retime-dropped-", suffix=".tmp", dir=sidecar.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, sidecar)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def retime_folder(doc_kg_dir: Path, schema: dict, *, write: bool = True) -> dict:
    """Re-derive one staging folder's valid time and drop undeclared property keys; write it
    back if it changed (dropped values go to `logs/retime-dropped/` first).

    Returns `{"document", "before", "after", "source", "changed", "dropped"}` (plus `"dates"`
    when the markdown is missing and the staged dates are kept), or `{"document", "skipped":
    reason}` for a folder it cannot retime."""
    document = _load_json(doc_kg_dir / "document.json")
    name = (document or {}).get("name") or doc_kg_dir.name
    if not isinstance(document, dict) or not document.get("id"):
        return {"document": name, "skipped": "no readable document.json"}
    if document.get("source_kind") == ingest.USER_CHAT_SOURCE_KIND:
        return {"document": name, "skipped": "update-session folder (dated by the chat)"}
    if document.get("source_kind") == "table":
        # table2graph staging: no markdown, each row dated by its own data.
        return {"document": name, "skipped": "table folder (dated by its rows)"}
    try:
        md_file = staged_markdown_path(document)
    except ValueError as e:
        return {"document": name, "skipped": str(e)}
    dates_note = None
    if not md_file.exists():
        # Plan question 16: the key filter still applies; the staged dates stand.
        new_document = document
        dates_note = "kept (markdown not found)"
    else:
        meta, _ = ingest._parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
        dates = ingest._document_valid_time(md_file, meta, schema, filename=name, prior=document)
        new_document = {k: v for k, v in document.items() if k not in _DOCUMENT_TIME_KEYS}
        new_document.update(dates)

    observations = _load_json(doc_kg_dir / "observations.json", [])
    new_observations, dropped = retime_observations(observations, new_document, schema)
    counts = Counter(k for d in dropped.values() for k in d)

    # Chunks carry the document's window and their own day (spec 2026-11-15 §5.1).
    chunks = _load_json(doc_kg_dir / "chunks.json", None)
    new_chunks = ingest._date_chunks(chunks, new_document) if isinstance(chunks, list) else chunks

    changed = new_document != document or new_observations != observations or new_chunks != chunks
    if changed and write:
        if dropped:
            _keep_dropped(doc_kg_dir, name, dropped)
        files = {
            "document.json": new_document,
            "observations.json": new_observations,
        }
        if new_chunks != chunks:
            files["chunks.json"] = new_chunks
        ingest.write_staging(doc_kg_dir, files)
    result = {
        "document": name,
        "before": document.get("_valid_from"),
        "after": new_document.get("_valid_from"),
        "source": new_document.get("_valid_time_source"),
        "changed": changed,
        "dropped": dict(counts),
    }
    if dates_note:
        result["dates"] = dates_note
    return result


def staging_folders(domain: str) -> list[Path]:
    root = ingest.KG_DIR / domain
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not is_scratch(d) and (d / "document.json").exists()
    )


def _effect(
    domain: str, folders: list[Path], redo_properties: bool, chunks: int, model: str | None,
    dropped_keys: Counter | None = None,
) -> dict:
    """The `effect` of retiming `folders` (the documents whose staging changes).

    Without `--redoProperties` there is no LLM call. With it, `chunks` is the backfill's call
    count: one per staged chunk with an observation of a `temporal:`-tagged class.
    """
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching

    documents = [_load_json(f / "document.json", {}) or {} for f in folders]
    names = [d.get("name") or f.name for d, f in zip(documents, folders)]
    calls = chunks if redo_properties else 0
    dropped_keys = dropped_keys or Counter()
    changes = [Change("document_reextract", len(folders), names)] if folders else []
    drop_note = ""
    if dropped_keys:
        total = sum(dropped_keys.values())
        changes.append(Change("property_values_dropped", total, [k for k, _ in dropped_keys.most_common()]))
        drop_note = f"; drop {total} undeclared property value(s), kept under logs/retime-dropped/"
    return Effect(
        summary=(f"Re-date {len(folders)} staged document(s) of {domain}"
                 + (f"; re-ask date properties, {calls} LLM call(s), each chunk's text sent to the configured provider once" if redo_properties else "; no LLM call")
                 + drop_note
                 + ", then rebuild the projection once."),
        changes=changes,
        stale_topics=topics_touching(doc_ids=[d["id"] for d in documents if d.get("id")], doc_names=names),
        llm_calls=calls,
        llm_model=model if calls else None,
        reversible=False,
        travels=True,
        records=[f".artmind/data/kg/{domain}"] if folders else [],
    ).to_json()


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
        # A folder is named by its document's stem (a document staged before
        # keys) or its key `<stem>~<hash>`: match "2026-01-07", "2026-01-07.md"
        # or the whole key.
        wanted = {Path(d).stem for d in documents}
        folders = [f for f in folders if f.name in wanted or f.name.rsplit("~", 1)[0] in wanted]
    if limit is not None:
        folders = folders[:limit]
    # Every precondition of the redo is checked before retime writes anything.
    targets: dict[str, list[str]] = {}
    if redo_properties:
        from artmind import backfill
        from artmind.temporal import _entity_temporal_mapping

        if not text_model:
            raise ValueError("--redoProperties needs a model")
        targets = {cls: sorted(set(m.values())) for cls, m in _entity_temporal_mapping(schema).items()}
        if targets:
            backfill.check_ready(schema, targets, dry_run=dry_run)

    results, failed, warnings = [], [], []
    committed_names: set[str] = set()
    retimed: list[Path] = []
    dropped_keys: Counter = Counter()
    for folder in folders:
        try:
            result = retime_folder(folder, schema, write=not dry_run)
        except Exception as e:  # one bad folder must not stop the domain
            logger.error("retime: {} failed: {}", folder.name, e)
            result = {"document": folder.name, "skipped": f"error: {e}"}
        results.append(result)
        dropped_keys.update(result.get("dropped") or {})
        if result.get("changed"):
            retimed.append(folder)

    # After the re-dating is written, so the backfill's date anchor is the document's new date.
    redo, attempted = None, set()
    if targets:
        try:
            redo = backfill.backfill_domain(domain, targets=targets, model=text_model, dry_run=dry_run,
                                            documents=documents, limit=limit, rebuild=False)
        except Exception as e:  # retime's own folders are still committed below
            logger.error("retime: the properties redo failed: {}", e)
            warnings.append(f"the properties redo failed: {e}; the re-dated documents are still committed")
        else:
            warnings += redo["warnings"]
            failed += redo["commit_failed"]
            attempted = {r["folder"] for r in redo["results"] if r.get("changed")}
            committed_names |= {r["folder"] for r in redo["results"] if r.get("committed")}
    redone = {r["folder"]: r for r in (redo or {}).get("results") or []}
    planned = set((redo or {}).get("planned") or [])

    retimed_names = {f.name for f in retimed}
    for i, folder in enumerate(folders):
        if folder.name in redone and folder.name not in retimed_names:
            r = redone[folder.name]
            results[i] = {"document": r["document"], "before": r.get("before"), "after": r.get("after"),
                          "source": r.get("source"), "changed": r["changed"]}
    try:
        if not dry_run:
            for folder, result in zip(folders, results):
                # A folder the backfill wrote was committed by it (its commit carries retime's staging,
                # written first); matched by folder name, never by document name.
                if folder in retimed and folder.name not in attempted:
                    try:
                        ok = ingest.commit_to_graph(folder, domain, defer_rebuild=True)
                    except Exception as e:
                        logger.error("retime: commit of {} failed: {}", folder.name, e)
                        ok = False
                    if ok:
                        committed_names.add(folder.name)
                    else:
                        failed.append(result["document"])
    finally:
        committed = len(committed_names)
        rebuild = ingest.rebuild_projection(domain) if committed else None
    # Retime's changed folders plus every folder the backfill planned: the same set dry or real.
    changed_folders = [f for f in folders if f in retimed or f.name in planned]
    redo_chunks = redo["effect"]["llm"]["calls"] if redo else 0

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
        "dropped": dict(dropped_keys),
        "warnings": warnings,
        "failed_chunks": (redo or {}).get("failed_chunks") or [],
        "redo_run_id": (redo or {}).get("run_id"),
        "redo_commit_failed": (redo or {}).get("commit_failed") or [],
        "effect": _effect(domain, changed_folders, redo_properties, redo_chunks, text_model, dropped_keys),
    }
