"""Dry-run re-extraction (spec 2026-10-11 §7.4): re-run the entities,
properties and relationships prompts on a few evidence chunks under a
proposed schema and/or meta.yaml, and diff the result against the chunks'
current staging. Writes nothing (no staging, graph, registry); every LLM call
is appended to the LLM audit log as usual.

Like-for-like: the proposed side skips the graph-derived name/property
vocabularies and the per-document canonicalization pass, so it is compared
with staging's pre-canonical `name`s. Run once with neither --schema nor
--meta to see how much the diff moves from LLM variance alone.
"""

import json
from pathlib import Path

import yaml

from artmind.reviews import answerability, shape

STEP_PREFIX = "reextract"
_OBS_META = answerability._OBS_META


class ReextractError(ValueError):
    pass


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def extract_chunk(text: str, schema: dict, *, model: str, meta: dict | None = None,
                  anchor_date: str | None = None) -> dict:
    from artmind import extraction

    failed: list[str] = []
    raw, ok = extraction.extract_with_retry(f"{STEP_PREFIX}_entities", model,
                                            extraction.build_entities_prompt(text, schema, meta=meta))
    if not ok:
        failed.append("entities")
    entities = [dict(e) for e in raw or [] if isinstance(e, dict) and e.get("id") and e.get("name")]
    for entity in entities:
        entity.setdefault("entity_class", entity.get("type") or "")
    relationships: list[dict] = []
    props: dict[str, dict] = {}
    if entities:
        raw_props, ok = extraction.extract_with_retry(
            f"{STEP_PREFIX}_properties", model,
            extraction.build_properties_prompt(text, entities, schema, anchor_date=anchor_date, meta=meta))
        if not ok:
            failed.append("properties")
        for item in raw_props or []:
            if isinstance(item, dict) and item.get("id"):
                props[item["id"]] = item.get("properties") or {}
        raw_rels, ok = extraction.extract_with_retry(
            f"{STEP_PREFIX}_relationships", model,
            extraction.build_relationships_prompt(text, entities, schema, meta=meta))
        if not ok:
            failed.append("relationships")
        relationships = [r for r in raw_rels or []
                         if isinstance(r, dict) and r.get("source_id") and r.get("target_id") and r.get("rel_type")]
    for entity in entities:
        entity["properties"] = props.get(entity["id"], {})
    return {"entities": entities, "relationships": relationships, "failed_steps": failed}


def _node_label(name, cls) -> str:
    return f"{str(name).strip()} ({cls})"


def current_side(observations: list[dict], relationships: list[dict]) -> dict:
    nodes = {}
    for obs in observations:
        key = (_cf(obs.get("name")), _cf(obs.get("entity_class")))
        entry = nodes.setdefault(key, {"label": _node_label(obs.get("name"), obs.get("entity_class")), "props": {}})
        entry["props"].update({k: v for k, v in obs.items() if k not in _OBS_META})
    edges = {(_cf(r.get("source_name")), _cf(r.get("rel_type")), _cf(r.get("target_name")))
             for r in relationships if _cf(r.get("rel_type")) != "extracted_from"}
    return {"nodes": nodes, "edges": edges}


def proposed_side(entities: list[dict], relationships: list[dict]) -> dict:
    nodes, names = {}, {}
    for entity in entities:
        key = (_cf(entity.get("name")), _cf(entity.get("entity_class")))
        nodes.setdefault(key, {"label": _node_label(entity.get("name"), entity.get("entity_class")), "props": {}})
        nodes[key]["props"].update(entity.get("properties") or {})
        names[entity.get("id")] = _cf(entity.get("name"))
    edges = {(names[r["source_id"]], _cf(r.get("rel_type")), names[r["target_id"]])
             for r in relationships if r.get("source_id") in names and r.get("target_id") in names}
    return {"nodes": nodes, "edges": edges}


def _edge_label(edge: tuple) -> str:
    return f"{edge[0]} -{edge[1]}-> {edge[2]}"


def diff(current: dict, proposed: dict) -> dict:
    cur_nodes, new_nodes = current["nodes"], proposed["nodes"]
    kept = sorted(set(cur_nodes) & set(new_nodes))
    properties = []
    for key in kept:
        old, new = cur_nodes[key]["props"], new_nodes[key]["props"]
        added = {k: v for k, v in new.items() if k not in old}
        removed = {k: v for k, v in old.items() if k not in new}
        changed = {k: [old[k], new[k]] for k in old.keys() & new.keys()
                   if json.dumps(old[k], sort_keys=True, default=str) != json.dumps(new[k], sort_keys=True, default=str)}
        if added or removed or changed:
            # Use casefolded name (key[0]) and original class from the current label
            original_class = cur_nodes[key]["label"].rsplit("(", 1)[1][:-1]
            properties.append({"node": f"{key[0]} ({original_class})",
                               "added": added, "removed": removed, "changed": changed})
    return {
        "nodes": {"added": sorted(new_nodes[k]["label"] for k in set(new_nodes) - set(cur_nodes)),
                  "removed": sorted(cur_nodes[k]["label"] for k in set(cur_nodes) - set(new_nodes)),
                  "kept": len(kept)},
        "edges": {"added": sorted(_edge_label(e) for e in proposed["edges"] - current["edges"]),
                  "removed": sorted(_edge_label(e) for e in current["edges"] - proposed["edges"]),
                  "kept": len(current["edges"] & proposed["edges"])},
        "properties": properties,
    }


def _load_yaml(path: Path | str, what: str) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReextractError(f"cannot read the {what} file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReextractError(f"the {what} file {path} must hold a YAML mapping")
    return data


def reextract(*, trace_id: str | None = None, domain: str | None = None, chunk_ids=(),
              schema_path=None, meta_path=None, max_chunks: int = 5, pattern=None, model: str | None = None) -> dict:
    import paths
    from artmind.temporal import load_schema, relative_anchor_date
    from utils.functions import load_env, resolve_llm_model

    try:
        _, domain, chunk_ids = shape.resolve_target(trace_id, domain, chunk_ids)
    except shape.ShapeError as exc:
        raise ReextractError(str(exc)) from exc
    chunk_ids = chunk_ids[:max_chunks]
    schema = (_load_yaml(schema_path, "schema") if schema_path
              else load_schema(domain, schemas_dir=paths.DOMAIN_SCHEMAS_DIR))
    meta = _load_yaml(meta_path, "meta") if meta_path else None
    if meta is not None and not isinstance(meta.get("prompt_templates"), dict):
        raise ReextractError("the meta file must define prompt_templates (copy domains/meta.yaml and edit it)")
    model = resolve_llm_model(load_env(), override=model)

    chunks = []
    for cid in chunk_ids:
        staged = shape.staging_for_chunk(domain, cid)
        if staged is None or not staged["chunk"].get("text"):
            chunks.append({"chunk_id": cid, "error": "no staging folder holds this chunk"})
            continue
        doc = staged["document"]
        anchor = relative_anchor_date(schema, {k: doc.get(k) for k in ("_valid_from", "_valid_to", "_valid_time_source")})
        proposed = extract_chunk(staged["chunk"]["text"], schema, model=model, meta=meta, anchor_date=anchor)
        if proposed["failed_steps"]:
            # A failed step leaves the proposed side empty; diffing it would read as
            # "the proposal removes everything". Report the failure instead.
            chunks.append({"chunk_id": cid, "document": doc.get("path") or doc.get("name"),
                           "failed_steps": proposed["failed_steps"], "diff": None,
                           "error": f"extraction failed at {', '.join(proposed['failed_steps'])} "
                                    "(see the LLM error above); no diff"})
            continue
        item = {
            "chunk_id": cid,
            "document": doc.get("path") or doc.get("name"),
            "failed_steps": proposed["failed_steps"],
            "diff": diff(current_side(staged["observations"], staged["relationships"]),
                         proposed_side(proposed["entities"], proposed["relationships"])),
        }
        if pattern is not None:
            item["answerability"] = {
                "current": answerability.match(pattern, answerability.from_staging(staged["observations"],
                                                                                   staged["relationships"])),
                "proposed": answerability.match(pattern, answerability.from_extraction(proposed["entities"],
                                                                                       proposed["relationships"])),
            }
        chunks.append(item)
    return {
        "trace_id": trace_id,
        "domain": domain,
        "model": model,
        "schema": str(schema_path) if schema_path else "(current)",
        "meta": str(meta_path) if meta_path else "(current)",
        "llm_calls_planned": 3 * len(chunk_ids),
        "writes": "none",
        "chunks": chunks,
    }
