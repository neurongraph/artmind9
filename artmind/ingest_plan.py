"""`ingest sync --dryRun` (spec 2026-11-02-cli-uniformity-design.md §3.4): what a sync of
these files would do, decided by reading only -- no conversion, no registry write, no
frontmatter stamp, no LLM call.

Every per-file decision is the ingest's own, reused as `ingest pending` reuses it:
`ingest.plan_vault_native` (a note's tier), `ingest.plan_binary` (a converted source) and
`structured.pipeline.is_unchanged` (a table). A file lands in one of three tiers:
`content` (extracted, converted or loaded), `metadata` (nothing to extract, but the ingest
stamps, moves or re-registers it) and `unchanged`.

The LLM estimate is an upper bound: per chunk the three extraction steps plus one chunk
summary, plus the document summary the extraction asks for when a document has several
chunks. Retries, cut-off splits and summaries already cached are not subtracted. A binary
that still needs converting has no chunk count until docling runs, so its chunks are
estimated from the text of its pages (pdf, pptx) and it says so (`estimated: true`).
"""
from __future__ import annotations

import math
from pathlib import Path

TIERS = ("content", "metadata", "unchanged")


def llm_calls(chunks: int, *, schema: dict) -> int:
    """The calls extracting a document of `chunks` chunks makes under `schema`."""
    from artmind import document_context as dc
    from artmind.schema_validate import graph_extraction_enabled

    summaries = chunks + (1 if chunks > 1 else 0)  # one per chunk, plus the reduce
    if not graph_extraction_enabled(schema):
        return summaries
    settings = dc.context_settings(schema)
    document_summary = 1 if chunks > 1 and settings.enabled and settings.summary else 0
    return 3 * chunks + summaries + document_summary


def _estimated_chunks(source: Path, chunk_size: int) -> int:
    """Chunks a not-yet-converted binary will probably make: its pages' text over the chunk size."""
    from artmind import page_check

    pages = page_check.source_pages(source)
    chars = sum(len(p) for p in pages) if pages else 0
    return max(1, math.ceil(chars / chunk_size))


def _entry(path: Path, root: Path | None, kind: str, tier: str, domain: str | None, **extra) -> dict:
    try:
        shown = path.resolve().relative_to(root).as_posix() if root else str(path)
    except ValueError:
        shown = str(path)
    return {"path": shown, "kind": kind, "tier": tier, "domain": domain, "reason": None,
            "chunks": None, "llm_calls": 0, "estimated": False, **extra}


def plan_file(
    f: Path, *, domain: str | None, set_domain: str | None, fork: bool, adopt: bool, reextract: bool,
    chunk_size: int, root: Path | None,
) -> dict:
    """One file's plan. Raises what the ingest itself would raise for a file it cannot take."""
    from artmind import ingest
    from artmind.structured import is_structured_source
    from artmind.temporal import load_schema

    if is_structured_source(f):
        from artmind.structured.pipeline import is_unchanged, registered_tables

        _, rows = registered_tables(f, domain)
        if is_unchanged(rows, ingest._compute_sha256(f)):
            return _entry(f, root, "table", "unchanged", domain)
        return _entry(f, root, "table", "content", domain, reason="changed" if any(rows) else "new")

    if ingest._is_vault_native_markdown(f):
        plan = ingest.plan_vault_native(
            f, domain=domain, set_domain=set_domain, fork=fork, adopt=adopt, reextract=reextract,
        )
        eff = plan.effective_domain
        extra = {"artmind_id": plan.resolution.artmind_id}
        if plan.tier == "content":
            chunks = len(ingest._split_markdown(plan.body, chunk_size))
            body_changed = plan.version.tier == "content"
            reason = "new" if plan.is_new else "changed" if body_changed else "domain_change" if plan.domain_changed else "reextract"
            return _entry(f, root, "note", "content", eff, reason=reason, chunks=chunks,
                          llm_calls=llm_calls(chunks, schema=load_schema(eff)), **extra)
        moved = plan.resolution.verdict != "reingest"
        tier = "metadata" if plan.stamp or moved else "unchanged"
        return _entry(f, root, "note", tier, eff, **extra)

    if ingest._is_binary_source(f):
        eff = set_domain or domain or "general"
        kind = "image" if f.suffix.lower() in ingest.IMAGE_EXTENSIONS else "binary"
        plan = ingest.plan_binary(f, eff)
        schema = load_schema(eff)
        if plan.unchanged and not reextract:
            return _entry(f, root, kind, "unchanged", eff)
        if plan.source_unchanged and plan.registered_path.exists():
            # Re-extracted from the markdown already stored (`--reextract`, or a
            # domain change): its chunks are known.
            _, body = ingest._parse_md_frontmatter(plan.registered_path.read_text(encoding="utf-8"))
            chunks = len(ingest._split_markdown(body, chunk_size))
            return _entry(f, root, kind, "content", eff, reason="domain_change" if plan.domain_changed else "reextract",
                          chunks=chunks, llm_calls=llm_calls(chunks, schema=schema))
        chunks = 1 if kind == "image" else _estimated_chunks(f, chunk_size)
        calls = llm_calls(chunks, schema=schema) + (1 if kind == "image" else 0)  # + the image description
        return _entry(f, root, kind, "content", eff, reason="new" if plan.is_new else "changed",
                      chunks=chunks, llm_calls=calls, estimated=True)

    # An ad-hoc .md outside the vault: path-keyed, no baseline to compare against.
    eff = domain or "general"
    _, body = ingest._parse_md_frontmatter(f.read_text(encoding="utf-8"))
    chunks = len(ingest._split_markdown(body, chunk_size))
    return _entry(f, root, "file", "content", eff, chunks=chunks,
                  llm_calls=llm_calls(chunks, schema=load_schema(eff)), estimated=True)


def plan_sync(
    files: list[Path], *, domain_for, set_domain: str | None, fork: bool, adopt: bool, reextract: bool,
    chunk_size: int, root: Path | None,
) -> dict:
    """`{"files": [...], "errors": [{"path", "error"}]}` over `files`, in order.

    `domain_for(path)` is the domain the sync gives that file before its own `_domain`
    frontmatter is read (mapping, then `--domain`)."""
    plans: list[dict] = []
    errors: list[dict] = []
    for f in files:
        try:
            plans.append(plan_file(
                f, domain=domain_for(f), set_domain=set_domain, fork=fork, adopt=adopt,
                reextract=reextract, chunk_size=chunk_size, root=root,
            ))
        except Exception as e:  # the ingest would fail on this file, not work on it
            try:
                shown = f.resolve().relative_to(root).as_posix() if root else str(f)
            except ValueError:
                shown = str(f)
            errors.append({"path": shown, "error": str(e)})
    return {"files": plans, "errors": errors}


def effect_for(plan: dict, *, model: str, stage_only: bool, reextract: bool):
    """The `effect` of a sync over `plan` (`plan_sync`'s result)."""
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching

    by_tier = {t: [p for p in plan["files"] if p["tier"] == t] for t in TIERS}
    content = by_tier["content"]
    tables = [p for p in content if p["kind"] == "table"]
    documents = [p for p in content if p["kind"] != "table"]
    redone = [p for p in documents if p["reason"] in ("reextract", "domain_change")]
    fresh = [p for p in documents if p not in redone]
    calls = sum(p["llm_calls"] for p in content)
    changes = []
    for kind, group in (("document_ingest", fresh), ("document_reextract", redone), ("table_change", tables)):
        if group:
            changes.append(Change(kind, len(group), [p["path"] for p in group]))
    estimated = any(p["estimated"] for p in content)
    domains = sorted({p["domain"] for p in content if p["domain"]})
    summary = (
        f"Ingest {len(content)} file(s) ({len(documents)} document(s), {len(tables)} table(s)); "
        f"{len(by_tier['metadata'])} metadata-only, {len(by_tier['unchanged'])} unchanged; "
        f"about {calls} LLM call(s){' (estimated)' if estimated else ''}"
        f"{'; staged only, no graph write' if stage_only else ''}."
    )
    return Effect(
        summary=summary,
        changes=changes,
        stale_topics=topics_touching(
            doc_ids=[p["artmind_id"] for p in documents if p.get("artmind_id")],
            doc_names=[Path(p["path"]).name for p in documents],
        ),
        llm_calls=calls,
        llm_model=model if calls else None,
        reversible=False,
        travels=True,
        records=[f".artmind/data/kg/{d}" for d in domains],
    )
