"""`artmind ingest backfill-properties`: fill declared properties of chosen classes from the staged
chunk text, without re-running entities, relationships, canonicalisation or summaries (spec
2026-11-15 §4).

Each selected chunk's text is sent to the configured provider once (answers cached under
logs/backfill-cache/). Answers merge into the staged observations.json -- only the named keys of the
named classes; names and ids never change, and a key the model does not answer keeps its value. Only
an observation that got a new value is re-derived (`retime.retime_observations`: valid time, key
rules); every other observation stays byte-identical, and a folder where nothing was filled is not
written or committed.

Every run keeps a run folder `logs/backfill-stash/<run_id>/`: `manifest.json` (each written folder,
the chunk-cache files patched, whether it committed) and the pre-run copies of each written
observations.json and patched chunk file. `--undo RUN_ID` restores them and commits;
`--retryCommit RUN_ID` commits the folders whose commit failed. Changed folders are committed with
the rebuild deferred (`commit_to_graph`, which bumps catalogue state), and the projection is rebuilt
once when anything committed. A real run refuses while the ingest worker is running.

`retime --redoProperties` runs on this module: one pass over every class with a `temporal:`
property (`targets` holds them all, so a chunk is asked once)."""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from loguru import logger

from artmind import ingest
from artmind.observations import flatten_domain_props, normalize_property_keys, property_key_rules
from artmind.retime import _keep_dropped, _load_json, retime_observations, staging_folders


def _ask(step: str, model: str, prompt: str, debug_dir: Path | None = None):
    """One properties call: `(items, ok)`. Patched in tests."""
    from artmind.extraction import extract_with_retry

    return extract_with_retry(step, model, prompt, debug_dir)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def scoped_schema(schema: dict, targets: dict[str, list[str]]) -> dict:
    """`schema` cut down to the target classes and keys -- all the prompt renders.
    Raises ValueError for a class the schema lacks or a key the class does not declare."""
    types = schema.get("entity_types") or {}
    scoped = {}
    for cls, props in targets.items():
        decl = types.get(cls)
        if not isinstance(decl, dict):
            raise ValueError(f"{cls} is not a class of this schema")
        declared = decl.get("properties") or {}
        missing = [p for p in props if p not in declared]
        if missing:
            raise ValueError(f"{', '.join(missing)} not declared on {cls}; declare it in the schema first")
        scoped[cls] = {**decl, "properties": {p: declared[p] for p in props}}
    out = copy.deepcopy(schema)
    out["entity_types"] = scoped
    return out


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def plan(domain: str, targets: dict[str, list[str]], match: str | None = None,
         documents: list[str] | None = None, limit: int | None = None) -> tuple[list[dict], int]:
    """([{folder, document, chunks: [{id, seq, text, observations}], obs_sha, doc_sha}], skipped):
    the staged chunks (read from `chunks.json`, no chunk cache needed) holding an observation of a
    target class. `skipped` counts target-class observations that cannot be asked (no id or chunk,
    or no chunk text). Table and update-session folders are never planned. `obs_sha`/`doc_sha` let
    the run notice a folder written since planning."""
    folders = staging_folders(domain)
    if documents:
        # Same matching as retime: "2026-01-07", "2026-01-07.md" or the whole key.
        wanted = {Path(d).stem for d in documents}
        folders = [f for f in folders if f.name in wanted or f.name.rsplit("~", 1)[0] in wanted]
    if limit is not None:
        folders = folders[:limit]
    out, skipped = [], 0
    for folder in folders:
        if folder.name.startswith("table__") or (match and not fnmatch.fnmatch(folder.name, match)):
            continue
        document = _load_json(folder / "document.json", {}) or {}
        if document.get("source_kind") in ("table", ingest.USER_CHAT_SOURCE_KIND):
            continue
        texts = {c.get("id"): c.get("text") or "" for c in _load_json(folder / "chunks.json", []) or []}
        by_chunk: dict[str, list[dict]] = {}
        for o in _load_json(folder / "observations.json", []) or []:
            if o.get("entity_class") not in targets:
                continue
            if o.get("chunk_id") and o.get("id") and (texts.get(o["chunk_id"]) or "").strip():
                by_chunk.setdefault(o["chunk_id"], []).append(o)
            else:
                skipped += 1
        chunks = [{"id": cid, "seq": str(cid).rsplit("_", 1)[-1], "text": texts[cid], "observations": obs}
                  for cid, obs in by_chunk.items()]
        if chunks:
            out.append({"folder": folder, "document": document, "chunks": chunks,
                        "obs_sha": _sha(folder / "observations.json"), "doc_sha": _sha(folder / "document.json")})
    return out, skipped


def _prompt(chunk: dict, document: dict, schema: dict, scoped: dict, folder: Path) -> tuple[str, dict]:
    """The properties prompt for this chunk's target observations (canonical names, ids o1..oN), with
    the document's date anchor when a target key is temporal, and the document context block.
    Returns (prompt, {prompt id: observation id})."""
    from artmind import document_context as dc
    from artmind.extraction import build_properties_prompt
    from artmind.temporal import date_is_only_the_default, relative_anchor_date

    temporal = any(isinstance(pd, dict) and pd.get("temporal")
                   for decl in scoped["entity_types"].values() for pd in decl["properties"].values())
    anchor = relative_anchor_date(schema, document) if temporal else None
    ids = {f"o{i}": o["id"] for i, o in enumerate(chunk["observations"], 1)}
    entities = [{"id": k, "entity_class": o["entity_class"], "name": o.get("canonical_name") or o.get("name")}
                for k, o in zip(ids, chunk["observations"])]
    text = chunk["text"]
    settings = dc.context_settings(schema)
    if settings.enabled:
        ctx = ingest._read_document_context(folder) or {}
        path = str(document.get("path") or "")
        safe = {**document, "path": Path(path).name if Path(path).is_absolute() else path,  # never a filesystem path
                "_valid_from": None if date_is_only_the_default(schema, document) else document.get("_valid_from")}
        block = dc.render_context(metadata=dc.build_metadata(safe), outline="", summary=ctx.get("summary") or "",
                                  identifiers=ctx.get("identifiers") or [], cap=settings.cap)
        text = ingest.with_document_context(block, text)
    return build_properties_prompt(text, entities, scoped, anchor_date=anchor), ids


def _cache_path(prompt: str, model: str) -> Path:
    # The prompt holds the chunk text, classes, keys, template and anchor: any change is a new key.
    key = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return ingest.LOGS_DIR / "backfill-cache" / f"{key}.json"


def _answer_for(prompt: str, model: str, step: str, debug_dir: Path) -> list | None:
    path = _cache_path(prompt, model)
    try:
        cached = _load_json(path)
    except ValueError:
        cached = None
    if isinstance(cached, list):
        return cached
    answer, ok = _ask(step, model, prompt, debug_dir)
    if not ok:
        return None
    _write_atomic(path, json.dumps(answer, ensure_ascii=False))
    return answer


# ── the run folder: manifest + pre-run copies ──────────────────────────────

_RUN_ID = re.compile(r"^[0-9a-f]{12}$")


def run_dir(run_id: str) -> Path:
    if not _RUN_ID.match(run_id or ""):
        raise ValueError(f"not a backfill run id: {run_id!r} (12 hex characters, as reported in run_id)")
    return ingest.LOGS_DIR / "backfill-stash" / run_id


def _load_manifest(run_id: str) -> dict:
    path = run_dir(run_id) / "manifest.json"
    try:
        data = _load_json(path)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("folders"), dict):
        raise ValueError(f"no backfill run {run_id} under {path.parent}")
    return data


def _save_manifest(manifest: dict) -> None:
    _write_atomic(run_dir(manifest["run_id"]) / "manifest.json",
                  json.dumps(manifest, indent=2, ensure_ascii=False))


def _stash_file(run_id: str, domain: str, folder: Path, rel: str) -> None:
    """Copy `folder/rel` into the run folder once (the first copy is the pre-run state)."""
    target = run_dir(run_id) / domain / folder.name / rel
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(folder / rel, target)


def _patch_chunk_cache(folder: Path, changed: list[dict], values: dict[str, dict], stash) -> list[str]:
    """Best effort (plan question 14): put the filled values into the chunk cache too, so a rebuild
    of observations.json from `chunks/<sha>/chunk_NNN.json` keeps them. Matched by raw name and class.
    `stash(rel)` copies a file before it is first written. Returns the patched files, relative to
    `folder`."""
    patched: list[str] = []
    if not (folder / "chunks").is_dir():
        return patched
    for sub in sorted((folder / "chunks").glob("*")):
        if not sub.is_dir():
            continue
        for obs in changed:
            seq = str(obs.get("chunk_id") or "").rsplit("_", 1)[-1]
            path = sub / f"chunk_{seq}.json"
            try:
                data = _load_json(path)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                continue
            ent = next((e for e in data.get("entities") or [] if e.get("name") == obs.get("name")
                        and e.get("entity_class") == obs.get("entity_class")), None)
            if ent is None or not ent.get("id"):
                logger.debug("backfill: no cached entity for {} in {}", obs.get("id"), path.name)
                continue
            entry = next((p for p in data.get("properties") or [] if p.get("id") == ent.get("id")), None)
            if entry is None:
                entry = {"id": ent["id"], "name": ent.get("name"), "properties": {}}
                data.setdefault("properties", []).append(entry)
            entry.setdefault("properties", {}).update(values[obs["id"]])
            rel = str(path.relative_to(folder))
            stash(rel)
            _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))
            if rel not in patched:
                patched.append(rel)
    return patched


# ── preconditions ─────────────────────────────────────────────────────────

def _worker_running() -> bool:
    """The ingest worker's check, as `vault sync` makes it."""
    import paths
    from artmind.worker_pid import live_pid

    vault = getattr(ingest, "ARTMIND_VAULT_DIR", None)
    if vault:
        from artmind.vault_sync import worker_running

        return worker_running(Path(vault))
    return any(live_pid(p) is not None for p in {paths.WORKER_PID_FILE, paths.DATA_DIR / "worker.pid"})


def refuse_while_worker_runs() -> None:
    """The worker writes the same staging folders and graph keys; a backfill beside it could write
    over its work or commit half of it."""
    if _worker_running():
        raise ValueError("the ingest worker is running -- wait for it to finish (`artmind ingest job-status`), "
                         "then re-run")


def check_ready(schema: dict, targets: dict[str, list[str]], *, dry_run: bool) -> dict:
    """Everything that can refuse a backfill, before anything is written (retime calls it before
    its own writes). Returns the scoped schema."""
    if not targets or any(not keys for keys in targets.values()):
        raise ValueError("name at least one property to fill")
    scoped = scoped_schema(schema, targets)
    if not dry_run:
        refuse_while_worker_runs()
    return scoped


# ── the effect ────────────────────────────────────────────────────────────

def _effect(domain: str, work: list[dict], targets: dict, model: str) -> dict:
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching
    from artmind.topics.shape import estimate_cost

    names = [w["document"].get("name") or w["folder"].name for w in work]
    chunk_chars = [len(c["text"]) for w in work for c in w["chunks"]]
    pairs = ", ".join(f"{c}.{p}" for c, ps in targets.items() for p in ps)
    return Effect(
        summary=(f"Fill {pairs} in up to {len(work)} staged document(s) of {domain}: {len(chunk_chars)} LLM "
                 "call(s); each chunk's text is sent to the configured provider once. Only observations "
                 "that get a value change; documents where nothing is filled are not written. Then commit "
                 "the changed documents and rebuild the projection once."),
        changes=[Change("property_backfill", len(work), names)] if work else [],
        stale_topics=topics_touching(doc_ids=[w["document"]["id"] for w in work if w["document"].get("id")],
                                     doc_names=names),
        llm_calls=len(chunk_chars), llm_model=model if chunk_chars else None,
        est_cost_usd=estimate_cost(chunk_chars, 0) if chunk_chars else None,
        reversible=True,
        undo=("artmind ingest backfill-properties --undo RUN_ID (the run's run_id; restores each written "
              "observations.json and chunk-cache file from logs/backfill-stash/RUN_ID/, commits, rebuilds)"),
        travels=True, records=[f".artmind/data/kg/{domain}"] if work else [],
    ).to_json()


# ── the fill ──────────────────────────────────────────────────────────────

def _fill_folder(w: dict, *, domain: str, schema: dict, scoped: dict, targets: dict, model: str,
                 key_rules: tuple, manifest: dict, report: dict) -> dict:
    """Ask, merge and write one folder (no commit). Returns its result row."""
    folder, document = w["folder"], w["document"]
    name = document.get("name") or folder.name
    row = {"folder": folder.name, "document": name, "before": document.get("_valid_from"),
           "after": document.get("_valid_from"), "source": document.get("_valid_time_source"),
           "changed": False, "committed": False, "filled": 0}
    original = _load_json(folder / "observations.json", []) or []
    observations = copy.deepcopy(original)
    by_id = {o["id"]: o for o in observations if o.get("id")}
    values: dict[str, dict] = {}
    debug_dir = ingest._llm_debug_dir(folder)
    for chunk in w["chunks"]:
        prompt, ids = _prompt(chunk, document, schema, scoped, folder)
        answer = _answer_for(prompt, model, f"backfill_{chunk['seq']}_properties", debug_dir)
        if answer is None:
            report["failed_chunks"].append(chunk["id"])
            logger.warning("backfill: {} chunk {} failed; a re-run retries it", name, chunk["id"])
            continue
        seen: set[str] = set()
        for item in answer if isinstance(answer, list) else []:
            if not isinstance(item, dict):
                continue
            oid = ids.get(str(item.get("id")))
            if oid not in by_id or oid in seen:  # the first answer for an observation wins
                continue
            seen.add(oid)
            allowed = set(targets.get(by_id[oid]["entity_class"]) or [])
            raw = item.get("properties")
            props = normalize_property_keys(dict(raw) if isinstance(raw, dict) else {}, *key_rules)
            props = flatten_domain_props({k: v for k, v in props.items() if k in allowed})
            new_vals = {k: v for k, v in props.items() if by_id[oid].get(k) != v}
            if new_vals:
                by_id[oid].update(new_vals)
                values.setdefault(oid, {}).update(new_vals)
    if not values:
        return row
    # Only the observations that got a value are re-derived; every other one stays as it was.
    filled = [o for o in observations if o.get("id") in values]
    redone, dropped = retime_observations(filled, document, schema)
    redone_by_id = {o["id"]: o for o in redone}
    new = [redone_by_id.get(o.get("id"), o) if o.get("id") in values else o for o in observations]
    # Recheck right before the write: the worker or another command may have written the folder
    # since it was planned.
    if _sha(folder / "observations.json") != w["obs_sha"] or _sha(folder / "document.json") != w["doc_sha"]:
        logger.warning("backfill: {} changed since it was planned; skipped (re-run to fill it)", name)
        report["warnings"].append(f"{folder.name}: staging changed since planning; skipped -- re-run to fill it")
        return {**row, "skipped": "staging changed since planning"}
    run_id = manifest["run_id"]
    _stash_file(run_id, domain, folder, "observations.json")
    if dropped:
        _keep_dropped(folder, name, dropped)
    ingest.write_staging(folder, {"observations.json": new})
    # The entry exists only once the write succeeded: undo and retry act on written folders alone.
    entry = manifest["folders"][folder.name] = {
        "document": name, "chunk_files": [], "committed": False,
        "after_sha": _sha(folder / "observations.json")}
    _save_manifest(manifest)
    try:
        entry["chunk_files"] = _patch_chunk_cache(
            folder, [by_id[i] for i in values], values, lambda rel: _stash_file(run_id, domain, folder, rel))
    except Exception as e:  # best effort: staging is the record
        logger.warning("backfill: chunk cache of {} not patched: {}", name, e)
    _save_manifest(manifest)
    count = sum(len(v) for v in values.values())
    report["filled"] += count
    row.update(changed=True, filled=count,
               after=next((o.get("_valid_from") for o in redone), row["before"]),
               source=next((o.get("_valid_time_source") for o in redone), row["source"]))
    return row


def _commit(folder: Path, domain: str, manifest: dict, row: dict, report: dict) -> bool:
    try:
        ok = bool(ingest.commit_to_graph(folder, domain, defer_rebuild=True))
    except Exception as e:
        logger.error("backfill: commit of {} failed: {}", folder.name, e)
        ok = False
    row["committed"] = ok
    entry = manifest["folders"].get(folder.name)
    if entry is not None:
        entry["committed"] = ok
        _save_manifest(manifest)
    if not ok:
        report["commit_failed"].append(row["document"])
    return ok


def _finish(report: dict, domain: str, committed: int, rebuild: bool) -> None:
    report["committed"] = committed
    report["rebuild"] = ingest.rebuild_projection(domain) if committed and rebuild else None


def backfill_domain(domain: str, *, targets: dict[str, list[str]], model: str, match: str | None = None,
                    dry_run: bool = False, documents: list[str] | None = None, limit: int | None = None,
                    rebuild: bool = True) -> dict:
    """Fill `targets` ({class: [declared keys]}) in `domain`'s staged documents. `rebuild=False`
    leaves the projection rebuild to the caller (retime rebuilds once for both). One folder's failure
    is recorded in its row (`error`) and never stops the others; the rebuild runs whenever anything
    committed. Result rows carry `folder`, `changed` and `committed`."""
    from artmind.temporal import load_schema

    schema = load_schema(domain, schemas_dir=ingest.DOMAIN_SCHEMAS_DIR)
    if not schema:
        raise FileNotFoundError(f"Domain schema not found: {ingest.DOMAIN_SCHEMAS_DIR / f'{domain}_schema.yaml'}")
    scoped = check_ready(schema, targets, dry_run=dry_run)
    work, skipped_observations = plan(domain, targets, match, documents, limit)
    effect = _effect(domain, work, targets, model)
    report = {"domain": domain, "dry_run": dry_run, "targets": targets, "warnings": [],
              "planned": [w["folder"].name for w in work],
              "chunks": sum(len(w["chunks"]) for w in work), "skipped_observations": skipped_observations,
              "filled": 0, "committed": 0, "commit_failed": [], "failed_chunks": [], "errors": [],
              "results": [], "run_id": None, "rebuild": None}
    if dry_run:
        report["results"] = [{"folder": w["folder"].name, "document": w["document"].get("name") or w["folder"].name,
                              "before": w["document"].get("_valid_from"), "after": w["document"].get("_valid_from"),
                              "source": w["document"].get("_valid_time_source"), "changed": True,
                              "committed": False} for w in work]
        return {**report, "effect": effect}
    run_id = uuid.uuid4().hex[:12]
    manifest = {"run_id": run_id, "domain": domain, "targets": targets, "model": model, "folders": {}}
    _save_manifest(manifest)  # a reported run_id always resolves, even when nothing gets written
    report["run_id"] = run_id
    key_rules = property_key_rules(schema)
    committed = 0
    try:
        for w in work:
            folder = w["folder"]
            try:
                row = _fill_folder(w, domain=domain, schema=schema, scoped=scoped, targets=targets, model=model,
                                   key_rules=key_rules, manifest=manifest, report=report)
            except Exception as e:  # one bad folder must not stop the domain
                logger.error("backfill: {} failed: {}", folder.name, e)
                name = w["document"].get("name") or folder.name
                row = {"folder": folder.name, "document": name, "changed": False, "committed": False,
                       "error": str(e)}
                report["errors"].append(folder.name)
                if (manifest["folders"].get(folder.name) or {}).get("after_sha"):
                    row["changed"] = True  # written before it failed: commit what is staged
            if row.get("changed") and _commit(folder, domain, manifest, row, report):
                committed += 1
            report["results"].append(row)
    finally:
        _finish(report, domain, committed, rebuild)
    if report["failed_chunks"]:
        report["warnings"].append(f"{len(report['failed_chunks'])} chunk(s) failed; re-run to retry "
                                  "(answers already given are cached)")
    if report["commit_failed"]:
        report["warnings"].append(f"{len(report['commit_failed'])} document(s) failed to commit; "
                                  f"run `artmind ingest backfill-properties --retryCommit {run_id}`")
    if report["errors"]:
        report["warnings"].append(f"{len(report['errors'])} document(s) failed: {', '.join(report['errors'][:5])}")
    return {**report, "effect": effect}


# ── undo and retry ────────────────────────────────────────────────────────

def _run_effect(manifest: dict, folders: list[str], summary: str) -> dict:
    from artmind.cli_conventions import Change, Effect

    domain = manifest["domain"]
    names = [manifest["folders"][f].get("document") or f for f in folders]
    return Effect(
        summary=summary,
        changes=[Change("property_backfill", len(folders), names)] if folders else [],
        reversible=False, travels=True, records=[f".artmind/data/kg/{domain}"] if folders else [],
    ).to_json()


def _written(entry: dict) -> bool:
    """Whether the run wrote this folder (an entry without `after_sha` never got its write)."""
    return bool(entry.get("after_sha"))


def _restore_file(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.undo.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def undo_run(run_id: str, *, dry_run: bool = False) -> dict:
    """Put back what run `run_id` wrote -- each patched chunk-cache file, then observations.json, from
    the run folder -- and commit each restored folder, then rebuild once.

    Idempotent: a folder whose observations.json is already the pre-run copy (an earlier undo restored
    it, then failed) counts as restored and goes on to its commit. A folder written again since the run
    (neither the run's output nor the pre-run copy) is left alone and named, as is one already undone
    and committed."""
    manifest = _load_manifest(run_id)
    domain = manifest["domain"]
    base = run_dir(run_id) / domain
    folders, warnings = [], []
    for name, entry in manifest["folders"].items():
        folder = ingest.KG_DIR / domain / name
        stashed = base / name / "observations.json"
        current = _sha(folder / "observations.json")
        if not _written(entry):
            warnings.append(f"{name}: the run never wrote it; left alone")
        elif not stashed.exists():
            warnings.append(f"{name}: no stashed observations.json; left alone")
        elif not folder.is_dir():
            warnings.append(f"{name}: staging folder is gone; left alone")
        elif entry.get("undone") and entry.get("undo_committed"):
            warnings.append(f"{name}: already undone and committed")
        elif current not in (entry["after_sha"], _sha(stashed)):
            warnings.append(f"{name}: staging written again since the run; left alone")
        else:
            folders.append(name)
    effect = _run_effect(manifest, folders, f"Restore {len(folders)} staged document(s) of {domain} to before "
                                            f"backfill run {run_id}, commit them and rebuild the projection once.")
    report = {"domain": domain, "run_id": run_id, "dry_run": dry_run, "warnings": warnings,
              "restored": [], "committed": 0, "commit_failed": [], "rebuild": None}
    if dry_run:
        return {**report, "restored": folders, "effect": effect}
    refuse_while_worker_runs()
    from artmind.atomic_dir import write_dir_atomic

    committed = 0
    try:
        for name in folders:
            folder = ingest.KG_DIR / domain / name
            entry = manifest["folders"][name]
            stashed = base / name / "observations.json"
            try:
                # Chunk-cache files first, observations.json last: until the last write lands the folder
                # still reads as the run's output, so a second undo picks it up again.
                for rel in entry.get("chunk_files") or []:
                    if (base / name / rel).exists():
                        _restore_file(base / name / rel, folder / rel)
                if _sha(folder / "observations.json") != _sha(stashed):
                    write_dir_atomic(folder, {"observations.json": stashed.read_bytes()})
            except Exception as e:
                logger.error("backfill undo: {} not restored: {}", name, e)
                report["warnings"].append(f"{name}: not restored ({e}); re-run the undo")
                continue
            report["restored"].append(name)
            entry["undone"] = True
            entry["undo_committed"] = False
            _save_manifest(manifest)
            committed += _commit_undone(folder, domain, manifest, entry, report)
    finally:
        _finish(report, domain, committed, True)
    if report["commit_failed"]:
        report["warnings"].append(f"{len(report['commit_failed'])} restored document(s) failed to commit; "
                                  f"run `artmind ingest backfill-properties --retryCommit {run_id}`")
    return {**report, "effect": effect}


def _commit_undone(folder: Path, domain: str, manifest: dict, entry: dict, report: dict) -> bool:
    """Commit a restored folder: the run's own values are then no longer committed."""
    ok = _commit(folder, domain, manifest, {"document": entry.get("document") or folder.name}, report)
    entry["committed"] = False
    entry["undo_committed"] = ok
    _save_manifest(manifest)
    return ok


def retry_commits(run_id: str, *, dry_run: bool = False) -> dict:
    """Commit what run `run_id` (or an undo of it) left staged but uncommitted; rebuild once. A folder
    whose staging no longer matches what the run (or undo) wrote is named in `warnings`; its current
    staging is committed all the same -- staging is the record."""
    manifest = _load_manifest(run_id)
    domain = manifest["domain"]
    base = run_dir(run_id) / domain
    folders, warnings = [], []
    for n, e in manifest["folders"].items():
        if not _written(e) or not (ingest.KG_DIR / domain / n).is_dir():
            continue
        undone = bool(e.get("undone"))
        if (undone and e.get("undo_committed")) or (not undone and e.get("committed")):
            continue
        expected = _sha(base / n / "observations.json") if undone else e["after_sha"]
        if _sha(ingest.KG_DIR / domain / n / "observations.json") != expected:
            warnings.append(f"{n}: staging changed since the {'undo' if undone else 'run'}; "
                            "committing the current staging")
        folders.append(n)
    effect = _run_effect(manifest, folders, f"Commit {len(folders)} staged document(s) of {domain} that backfill "
                                            f"run {run_id} wrote but did not commit, then rebuild the projection once.")
    report = {"domain": domain, "run_id": run_id, "dry_run": dry_run, "warnings": warnings,
              "retried": folders, "committed": 0, "commit_failed": [], "rebuild": None}
    if dry_run:
        return {**report, "effect": effect}
    refuse_while_worker_runs()
    committed = 0
    try:
        for name in folders:
            entry = manifest["folders"][name]
            folder = ingest.KG_DIR / domain / name
            if entry.get("undone"):
                committed += _commit_undone(folder, domain, manifest, entry, report)
            else:
                committed += _commit(folder, domain, manifest, {"document": entry.get("document") or name}, report)
    finally:
        _finish(report, domain, committed, True)
    return {**report, "effect": effect}
