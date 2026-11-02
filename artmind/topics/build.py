"""`artmind topic build` (spec 2026-11-01 §3.2): anchor → gather → map → reduce
→ check → render → write. The LLM seams (`map_llm(prompt) -> dict | None`,
`reduce_llm(prompt) -> str`) and the anchor/gather results are injectable for
tests; by default they come from `extraction` and the graph."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from artmind.topics import anchor as anchor_mod
from artmind.topics import check, gather, prompts, render, store


def _default_llms(model: str):
    from artmind import extraction
    import paths

    debug = Path(paths.LOGS_DIR) / "topic-debug"

    def map_llm(prompt):
        answer, ok = extraction.extract_with_retry("topic_map", model, prompt, debug_dir=debug)
        return answer if ok else None

    def reduce_llm(prompt):
        return extraction.call_llm(model, prompt, call_type="topic_reduce")

    return map_llm, reduce_llm


def _graph_domains(domains: list[str]) -> list[str]:
    from artmind.schema_validate import domain_graph_extraction

    return [d for d in domains if domain_graph_extraction(d)]


def _groups(lines: list[str], limit: int) -> list[list[str]]:
    groups: list[list[str]] = []
    size = 0
    for line in lines:
        if groups and size + len(line) <= limit:
            groups[-1].append(line)
            size += len(line)
        else:
            groups.append([line])
            size = len(line)
    return groups


def page_effect(slug: str, path: Path, calls: int, model: str, documents: int) -> "Effect":
    """The `effect` of building one page: one file written, `calls` LLM calls.

    Computed from the gathered documents before any call, so a dry run and the
    real run report the same plan (`calls` is the one-reduce minimum; a long
    reduce adds partial-merge calls, which the payload's own `calls` counts).
    """
    from artmind.cli_conventions import Change, Effect

    return Effect(
        summary=(f"Write the topic page {slug!r} from {documents} document(s) "
                 f"({calls} LLM call(s)); a hand-written `## My notes` section is kept."),
        changes=[Change("topic_write", 1, [slug])],
        llm_calls=calls,
        llm_model=model,
        reversible=False,
        travels=True,
        records=[str(path)],
    )


def _no_page_effect(effect: dict) -> dict:
    """The effect of a build that found nothing to say: the calls were made, no page was written."""
    return {**effect, "summary": "No page written: no document said anything about the topic.",
            "changes": [], "travels": False, "records": []}


def build_page(topic: str, domains: list[str], *, max_docs: int = 12, slug: str | None = None,
               out: Path | None = None, model: str | None = None, dry_run: bool = False,
               anchor: dict | None = None, gathered=None, map_llm=None, reduce_llm=None,
               now: str | None = None) -> dict:
    if model is None:
        from utils.functions import load_env, resolve_llm_model

        model = resolve_llm_model(load_env())
    directory = store.topics_dir(out)
    slug = slug or store.slugify(topic)
    anchor = anchor if anchor is not None else anchor_mod.choose_anchor(topic, graph_domains=_graph_domains(domains))
    materials, links = gathered if gathered is not None else gather.gather(topic, domains, anchor, max_docs)
    result = {"slug": slug, "path": None, "dry_run": dry_run,
              "anchor": {k: anchor.get(k) for k in ("kind", "name") if anchor.get(k)},
              "documents": [m.name or m.doc_id for m in materials], "calls": len(materials) + 1}
    result["effect"] = page_effect(slug, directory / f"{slug}.md", len(materials) + 1, model,
                                   len(materials)).to_json()
    if dry_run:
        return result
    if map_llm is None or reduce_llm is None:
        default_map, default_reduce = _default_llms(model)
        map_llm, reduce_llm = map_llm or default_map, reduce_llm or default_reduce

    noted: list[tuple[str, dict]] = []
    used: list = []
    for m in materials:
        parsed = prompts.parse_map(map_llm(prompts.map_prompt(topic, anchor, m)), {s.id for s in m.sources})
        if parsed and parsed["relevant"]:
            used.append(m)
            noted += [(m.title or m.name or m.doc_id, n) for n in parsed["notes"]]
    if not noted:
        result["error"] = "no document said anything about the topic; no page written"
        result["documents"] = []
        result["effect"] = _no_page_effect(result["effect"])
        return result

    lines = prompts.notes_text(noted).splitlines()
    calls = len(materials)
    if sum(len(l) for l in lines) > prompts.REDUCE_INPUT_CHARS:
        partials = []
        for group in _groups(lines, prompts.REDUCE_INPUT_CHARS):
            partials.append(prompts.strip_fences(reduce_llm(prompts.reduce_prompt(topic, anchor, "\n".join(group)))))
            calls += 1
        body = prompts.strip_fences(reduce_llm(prompts.merge_prompt(topic, anchor, partials)))
    else:
        body = prompts.strip_fences(reduce_llm(prompts.reduce_prompt(topic, anchor, "\n".join(lines))))
    calls += 1

    texts = {s.id: s.text for m in used for s in m.sources}
    # Map each passage id to its source document's title for the identifier check.
    titles = {s.id: (links.get(s.id, {}).get("title") or links.get(s.id, {}).get("name") or "")
              for m in used for s in m.sources}
    body, checks = check.check(body, texts, titles=titles, topic=topic)
    rendered, cited = render.render_body(body, links)
    cited_set = set(cited)
    sources = []
    for m in used:
        ids = [s.id for s in m.sources if s.id in cited_set]
        if ids:
            info = links.get(ids[0], {})
            sources.append({"doc_id": m.doc_id, "name": m.name, "content_sha": info.get("version"), "passages": ids})
    anchor_meta = ({"kind": "entity", "key": anchor.get("key"), "name": anchor.get("name"),
                    "observation_set_hash": anchor.get("observation_set_hash")}
                   if anchor.get("kind") == "entity" else {"kind": "topic"})
    meta = {"artmind_topic": store.FORMAT, "topic": topic, "slug": slug, "domains": list(domains),
            "anchor": anchor_meta, "built": now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": model, "prompt_version": prompts.PROMPT_VERSION, "max_docs": max_docs,
            "sources": sources, "checks": checks}
    path = store.write_page(directory, slug, meta, rendered)
    result.update(path=str(path), calls=calls, checks=checks, documents=[s["name"] for s in sources])
    return result
