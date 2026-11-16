"""`artmind ingest backfill-properties`: fill declared properties of chosen classes from the staged
chunk text, without re-running entities, relationships, canonicalisation or summaries (spec
2026-11-15 §4).

Each selected chunk's text is sent to the configured provider once (answers cached under
logs/backfill-cache/). Answers merge into the staged observations.json -- only the named keys; names
and ids never change, and a key the model does not answer keeps its value -- valid time is
re-derived (`retime.retime_observations`), each changed folder's observations.json is stashed under
logs/backfill-stash/<run>/ first, then committed with the rebuild deferred (`commit_to_graph`, which
bumps catalogue state), and the projection is rebuilt once.

`retime --redoProperties` runs on this module: one pass over every class with a `temporal:`
property (`targets` holds them all, so a chunk is asked once)."""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
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


def plan(domain: str, targets: dict[str, list[str]], match: str | None = None,
         documents: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """[{folder, document, chunks: [{id, text, observations}]}]: the staged chunks (read from
    `chunks.json`, no chunk cache needed) holding an observation of a target class. Table and
    update-session folders are never planned."""
    folders = staging_folders(domain)
    if documents:
        # Same matching as retime: "2026-01-07", "2026-01-07.md" or the whole key.
        wanted = {Path(d).stem for d in documents}
        folders = [f for f in folders if f.name in wanted or f.name.rsplit("~", 1)[0] in wanted]
    if limit is not None:
        folders = folders[:limit]
    out = []
    for folder in folders:
        if folder.name.startswith("table__") or (match and not fnmatch.fnmatch(folder.name, match)):
            continue
        document = _load_json(folder / "document.json", {}) or {}
        if document.get("source_kind") in ("table", ingest.USER_CHAT_SOURCE_KIND):
            continue
        by_chunk: dict[str, list[dict]] = {}
        for o in _load_json(folder / "observations.json", []) or []:
            if o.get("entity_class") in targets and o.get("chunk_id") and o.get("id"):
                by_chunk.setdefault(o["chunk_id"], []).append(o)
        texts = {c.get("id"): c.get("text") or "" for c in _load_json(folder / "chunks.json", []) or []}
        chunks = [{"id": cid, "text": texts[cid], "observations": obs}
                  for cid, obs in by_chunk.items() if (texts.get(cid) or "").strip()]
        if chunks:
            out.append({"folder": folder, "document": document, "chunks": chunks})
    return out


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


def _patch_chunk_cache(folder: Path, changed: list[dict], values: dict[str, dict]) -> None:
    """Best effort (plan question 14): put the filled values into the chunk cache too, so a rebuild
    of observations.json from `chunks/<sha>/chunk_NNN.json` keeps them. Matched by raw name and class."""
    if not (folder / "chunks").is_dir():
        return
    for sub in (folder / "chunks").glob("*"):
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
            _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))


def _effect(domain: str, work: list[dict], targets: dict, model: str) -> dict:
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching
    from artmind.topics.shape import estimate_cost

    names = [w["document"].get("name") or w["folder"].name for w in work]
    chunk_chars = [len(c["text"]) for w in work for c in w["chunks"]]
    pairs = ", ".join(f"{c}.{p}" for c, ps in targets.items() for p in ps)
    return Effect(
        summary=(f"Fill {pairs} in {len(work)} staged document(s) of {domain}: {len(chunk_chars)} LLM call(s); "
                 "each chunk's text is sent to the configured provider once. Then commit the changed "
                 "documents and rebuild the projection once."),
        changes=[Change("property_backfill", len(work), names)] if work else [],
        stale_topics=topics_touching(doc_ids=[w["document"]["id"] for w in work if w["document"].get("id")],
                                     doc_names=names),
        llm_calls=len(chunk_chars), llm_model=model if chunk_chars else None,
        est_cost_usd=estimate_cost(chunk_chars, 0) if chunk_chars else None,
        reversible=True,
        undo=(f"copy logs/backfill-stash/<run_id>/{domain}/<folder>/observations.json back into each staging "
              f"folder, then run `artmind ingest retime --domain {domain}`"),
        travels=True, records=[f".artmind/data/kg/{domain}"] if work else [],
    ).to_json()


def backfill_domain(domain: str, *, targets: dict[str, list[str]], model: str, match: str | None = None,
                    dry_run: bool = False, documents: list[str] | None = None, limit: int | None = None,
                    rebuild: bool = True) -> dict:
    """Fill `targets` ({class: [declared keys]}) in `domain`'s staged documents. `rebuild=False`
    leaves the projection rebuild to the caller (retime rebuilds once for both)."""
    from artmind.temporal import load_schema

    schema = load_schema(domain, schemas_dir=ingest.DOMAIN_SCHEMAS_DIR)
    if not schema:
        raise FileNotFoundError(f"Domain schema not found: {ingest.DOMAIN_SCHEMAS_DIR / f'{domain}_schema.yaml'}")
    scoped = scoped_schema(schema, targets)
    work = plan(domain, targets, match, documents, limit)
    effect = _effect(domain, work, targets, model)
    report = {"domain": domain, "dry_run": dry_run, "targets": targets,
              "planned": [w["folder"].name for w in work],
              "chunks": sum(len(w["chunks"]) for w in work), "filled": 0, "committed": 0,
              "commit_failed": [], "failed_chunks": [], "results": [], "run_id": None, "rebuild": None}
    if dry_run:
        report["results"] = [{"folder": w["folder"].name, "document": w["document"].get("name") or w["folder"].name,
                              "before": w["document"].get("_valid_from"), "after": w["document"].get("_valid_from"),
                              "source": w["document"].get("_valid_time_source"), "changed": True} for w in work]
        return {**report, "effect": effect}
    run_id = uuid.uuid4().hex[:12]
    aliases, declared_all = property_key_rules(schema)
    debug_dir = ingest.LOGS_DIR / "extraction-debug" / "backfill" / domain
    committed = 0
    for w in work:
        folder, document = w["folder"], w["document"]
        name = document.get("name") or folder.name
        original = _load_json(folder / "observations.json", []) or []
        observations = copy.deepcopy(original)
        by_id = {o["id"]: o for o in observations if o.get("id")}
        values: dict[str, dict] = {}
        for chunk in w["chunks"]:
            prompt, ids = _prompt(chunk, document, schema, scoped, folder)
            answer = _answer_for(prompt, model, "backfill_properties", debug_dir)
            if answer is None:
                report["failed_chunks"].append(chunk["id"])
                logger.warning("backfill: {} chunk {} failed; a re-run retries it", name, chunk["id"])
                continue
            for item in answer if isinstance(answer, list) else []:
                if not isinstance(item, dict):
                    continue
                oid = ids.get(str(item.get("id")))
                if oid not in by_id:
                    continue
                allowed = set(targets.get(by_id[oid]["entity_class"]) or [])
                raw = item.get("properties")
                props = normalize_property_keys(dict(raw) if isinstance(raw, dict) else {}, aliases, declared_all)
                props = flatten_domain_props({k: v for k, v in props.items() if k in allowed})
                if props:
                    by_id[oid].update(props)
                    values.setdefault(oid, {}).update(props)
                    report["filled"] += len(props)
        new, dropped = retime_observations(observations, document, schema)
        changed = new != original
        row = {"folder": folder.name, "document": name, "before": document.get("_valid_from"), "changed": changed}
        if changed:
            stash = ingest.LOGS_DIR / "backfill-stash" / run_id / domain / folder.name
            stash.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / "observations.json", stash / "observations.json")
            if dropped:
                _keep_dropped(folder, name, dropped)
            ingest.write_staging(folder, {"observations.json": new})
            try:
                _patch_chunk_cache(folder, [by_id[i] for i in values], values)
            except Exception as e:  # best effort: staging is the record
                logger.warning("backfill: chunk cache of {} not patched: {}", name, e)
            if ingest.commit_to_graph(folder, domain, defer_rebuild=True):
                committed += 1
            else:
                report["commit_failed"].append(name)
        row["after"] = next((o.get("_valid_from") for o in new if o.get("entity_class") in targets), row["before"])
        row["source"] = next((o.get("_valid_time_source") for o in new if o.get("entity_class") in targets),
                             document.get("_valid_time_source"))
        report["results"].append(row)
    report.update(run_id=run_id, committed=committed,
                  rebuild=ingest.rebuild_projection(domain) if committed and rebuild else None)
    return {**report, "effect": effect}
