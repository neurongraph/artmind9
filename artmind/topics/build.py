"""`artmind topic build` (spec 2026-11-10 §6–§7): plan the grid from orient's facts, gather
per cell, map per (document, subject) concurrently through the map cache, reduce per facet,
check, render, and write the topic plan beside the page.

The LLM seams (`map_llm(prompt) -> dict | None`, `reduce_llm(prompt) -> str`), the orient
cards, the graph session and the survey are injectable for tests; by default they come from
`extraction`, `orient` and the graph. `--dryRun` runs the counts and the gather (no LLM) and
writes nothing.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from artmind.topics import check, grid, mapcache, prompts, render, shape, store
from artmind.topics import plan as plan_model
from artmind.topics import plan_store

MAP_CONCURRENCY_ENV = "ARTMIND_TOPIC_MAP_CONCURRENCY"
DEFAULT_MAP_CONCURRENCY = 8


def map_concurrency() -> int:
    raw = os.environ.get(MAP_CONCURRENCY_ENV)
    if raw is None:
        return DEFAULT_MAP_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{MAP_CONCURRENCY_ENV} must be an integer between 1 and 32, got {raw!r}") from None
    if not 1 <= value <= 32:
        raise ValueError(f"{MAP_CONCURRENCY_ENV} must be an integer between 1 and 32, got {raw!r}")
    return value


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _effect(slug: str, page_path: Path, plan_path: Path, calls: int, model: str, documents: int, cost: float):
    from artmind.cli_conventions import Change, Effect

    return Effect(
        summary=(f"Write the topic page {slug!r} and its plan from {documents} document(s) "
                 f"({calls} LLM call(s)); a hand-written `## My notes` section is kept."),
        changes=[Change("topic_write", 2, [str(page_path.name), f"topic_plans/{slug}"])],
        llm_calls=calls, llm_model=model, est_cost_usd=cost,
        reversible=False, travels=True, records=[str(page_path), str(plan_path)],
    )


class _Plan:
    """Everything the dry run reports and the real run executes."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def plan_build(topic: str, domains: list[str], *, facets=None, by: str = "auto", split: str = "auto",
               per_cell: int = plan_model.DEFAULT_PER_CELL, max_docs: int = plan_model.DEFAULT_MAX_DOCS,
               model: str | None = None, full: bool = False, cards=None, session_factory=None, search=None,
               suggest=None, members=None, anchors=None, entity_class=None, dropped=(), resolve=None) -> _Plan:
    from artmind.graph_query import read_session

    subjects = [" ".join(s.split()) for s in (facets or []) if s and s.strip()]
    member_names = [" ".join(m.split()) for m in (members or []) if m and m.strip()]
    if anchors and not entity_class:
        raise ValueError("--anchor needs --entityClass (the class of the members to discover)")
    if member_names or anchors:
        by, split = "none", "none"  # members are gathered without a period slice (spec §6.4)
        if len(member_names) > plan_model.MAX_MEMBERS:
            raise ValueError(f"at most {plan_model.MAX_MEMBERS} --member entries")
    elif len(subjects) > plan_model.MAX_SUBJECTS:
        raise ValueError(f"at most {plan_model.MAX_SUBJECTS} --facet subjects")
    if not subjects and not member_names:
        subjects = [" ".join(topic.split())]
    if len(set(subjects)) != len(subjects):
        raise ValueError("a --facet is given twice")
    warnings: list[str] = []
    facts = None
    if by == "auto" or split == "auto":
        try:
            facts = shape.facts(domains, cards)
        except Exception as exc:  # orient unreadable: the rule falls back to no slices
            warnings.append(f"shape facts unavailable ({exc}); --by auto resolved to none")
            facts = {}
    by = shape.resolve_by(by, facts or {})
    member_records, discovered = [], None
    with (session_factory or read_session)() as session:
        if anchors:
            from artmind.topics import members as members_mod

            discovered = members_mod.discover(list(anchors), entity_class, domains, session, kept=member_names,
                                              dropped=list(dropped), resolve=resolve)
        if member_names:
            from artmind.topics import members as members_mod

            member_records = members_mod.member_records(member_names, domains, session)
            subjects = [m["name"] for m in member_records] + [s for s in subjects if s not in member_names]
            if anchors:
                subjects.append(members_mod.anchors_subject(anchors))
        if len(set(subjects)) != len(subjects):
            raise ValueError("a --facet or --member is given twice")
        by_name = {m["name"]: m for m in member_records}
        queries = [" ".join([s, *(by_name[s].get("aliases") or [])]) if s in by_name
                   else (s.replace(";", " ") if anchors and s == subjects[-1] and member_names else s) for s in subjects]
        counted, floors = grid.count_all(queries, domains, session=session)
        split = shape.split_from_counts(split, domains, counted)
        cells, cut = grid.cells_from(counted, floors, domains, by, split, per_cell, max_docs)
        materials, _ = grid.gather(queries, cells, domains, search=search)
        links = _links(materials, session)
        suggested = suggest(topic, domains, session) if suggest else []
    label_cell = {c.label(): c for c in cells}
    pairs = []
    for m in materials:
        seen = set()
        for label in m.cells:
            c = label_cell.get(label)
            if c is not None and c.subject not in seen:
                seen.add(c.subject)
                pairs.append((m, c))
    version = {m.doc_id: _version(m, links) for m in materials}
    keys = {(m.doc_id, c.subject): mapcache.key(version[m.doc_id], subjects[c.subject - 1],
                                                 prompts.MAP_PROMPT_VERSION, model)
            for m, c in pairs}
    to_map = [(m, c) for m, c in pairs if full or mapcache.get(keys[(m.doc_id, c.subject)]) is None]
    groups = sorted({(c.subject, c.domain if split == "domain" else None) for _, c in pairs},
                    key=lambda g: (g[0], g[1] or ""))
    # Incremental reduce (spec §8.2): a group whose notes all come from cached maps and whose
    # section is in the reduce cache is not reduced again; the overview always is.
    mapped = {(m.doc_id, c.subject) for m, c in to_map}
    cached_answers = {} if full else {k: mapcache.get(v) for k, v in keys.items() if k not in mapped}
    stub = _Plan(subjects=subjects, topic=" ".join(topic.split()), split=split, model=model, pairs=pairs)
    reduce_needed = []
    for g in groups:
        if full or any((m.doc_id, c.subject) in mapped for m, c in pairs
                       if (c.subject, c.domain if split == "domain" else None) == g):
            reduce_needed.append(g)
            continue
        rows = _notes(stub, cached_answers).get(g)
        if rows and mapcache.get(_reduce_key(stub, g, rows)) is None:
            reduce_needed.append(g)
    reduce_calls = len(reduce_needed) + 1 if pairs else 0
    if cut["cut"]:
        warnings.append(f"the cells want {cut['wanted']} documents; cut fairly to {cut['max_docs']} (--maxDocs)")
    for i, docs in enumerate(counted, 1):
        if not docs:
            warnings.append(f"subject {i} matches no document's words: a wrong facet?")
    cost = shape.estimate_cost([m.size() for m, _ in to_map], reduce_calls)
    return _Plan(member_records=member_records, discovered=discovered, dropped=tuple(dropped),
                 anchors=list(anchors or []), entity_class=entity_class,
                 topic=" ".join(topic.split()), domains=list(domains), subjects=subjects, by=by, split=split,
                 per_cell=per_cell, max_docs=max_docs, model=model, facts=facts, cells=cells, cut=cut,
                 materials=materials, links=links, pairs=pairs, keys=keys, to_map=to_map, groups=groups,
                 reduce_calls=reduce_calls, cost=cost, warnings=warnings, suggested=suggested, full=full)


def _links(materials, session):
    from artmind.topics.gather import links_for

    return links_for(materials, session)


def _version(material, links) -> str | None:
    for s in material.sources:
        v = (links.get(s.id) or {}).get("version")
        if v:
            return str(v)
    return None


def grid_payload(p: _Plan) -> dict:
    return {"slices": {"by": p.by, "split": p.split if p.split != "none" else None},
            "cells": [c.to_json(gathered=True) for c in p.cells], "cut": p.cut["cut"],
            **({"facts": p.facts} if p.facts else {})}


def build_topic(topic: str, domains: list[str], *, facets=None, by: str = "auto", split: str = "auto",
                per_cell: int = plan_model.DEFAULT_PER_CELL, max_docs: int = plan_model.DEFAULT_MAX_DOCS,
                slug: str | None = None, out: Path | None = None, model: str | None = None,
                dry_run: bool = False, refresh: str = "manual", full: bool = False, cards=None,
                session_factory=None, search=None, map_llm=None, reduce_llm=None, now: str | None = None,
                generations=None, suggest=None, members=None, anchors=None, entity_class=None, dropped=(),
                resolve=None) -> dict:
    if model is None:
        from utils.functions import load_env, resolve_llm_model

        model = resolve_llm_model(load_env())
    directory = store.topics_dir(out)
    slug = slug or store.slugify(topic)
    plan_store.check_slug(slug)
    if refresh not in plan_model.REFRESH:
        raise ValueError(f"--refresh must be one of {', '.join(plan_model.REFRESH)}")
    p = plan_build(topic, domains, facets=facets, by=by, split=split, per_cell=per_cell, max_docs=max_docs,
                   model=model, full=full, cards=cards, session_factory=session_factory, search=search,
                   suggest=suggest, members=members, anchors=anchors, entity_class=entity_class, dropped=dropped,
                   resolve=resolve)
    page_path, plan_path = directory / f"{slug}.md", plan_store.plans_dir() / slug
    calls = len(p.to_map) + p.reduce_calls
    result = {
        "slug": slug, "path": None, "plan_path": None, "topic": p.topic,
        "subjects": [{"n": i, "text": s} for i, s in enumerate(p.subjects, 1)],
        "grid": grid_payload(p), "documents": len(p.materials),
        "calls": {"map": len(p.to_map), "map_cached": len(p.pairs) - len(p.to_map), "reduce": p.reduce_calls},
        "warnings": list(p.warnings),
    }
    if p.suggested:
        result["suggested_subjects"] = p.suggested
    if p.discovered is not None:
        result["discover"] = p.discovered
    if p.member_records:
        result["members"] = p.member_records
    result["effect"] = _effect(slug, page_path, plan_path, calls, model, len(p.materials), p.cost).to_json()
    if dry_run or (p.discovered is not None and not p.member_records):
        # discovery without kept members is a proposal only: nothing to build yet (spec §6.4 step 2)
        if not dry_run:
            result["error"] = "discovery lists candidates; keep members with --member, then build"
            result["effect"] = {**result["effect"], "summary": "Nothing written: no members kept yet.",
                                "changes": [], "travels": False, "records": [],
                                "llm": {**result["effect"]["llm"], "calls": 0}}
        else:
            result["dry_run"] = True
        return result
    if not p.pairs:
        result["error"] = "no document matched any subject; nothing written"
        result["effect"] = {**result["effect"], "summary": "Nothing written: no document matched.",
                            "changes": [], "travels": False, "records": [], "llm": {**result["effect"]["llm"],
                                                                                      "calls": 0}}
        return result
    if map_llm is None or reduce_llm is None:
        default_map, default_reduce = _default_llms(model)
        map_llm, reduce_llm = map_llm or default_map, reduce_llm or default_reduce
    body, used, made = _write_body(p, map_llm, reduce_llm)
    if body is None:
        result["error"] = "no document said anything about the subjects; no page written"
        result["effect"] = {**result["effect"], "summary": "No page written: no document said anything.",
                            "changes": [], "travels": False, "records": []}
        return result
    texts = {s.id: s.text for m in used for s in m.sources}
    titles = {s.id: ((p.links.get(s.id) or {}).get("title") or (p.links.get(s.id) or {}).get("name") or "")
              for m in used for s in m.sources}
    body, checks = check.check(body, texts, titles=titles, topic=p.topic)
    rendered, cited = render.render_body(body, p.links)
    cited_set = set(cited)
    sources = []
    for m in used:
        ids = [s.id for s in m.sources if s.id in cited_set]
        if ids:
            sources.append({"doc_id": m.doc_id, "name": m.name, "content_sha": _version(m, p.links),
                            "passages": ids, "cells": list(m.cells)})
    if generations is None:
        generations = _generations(p.domains, session_factory)
    spec = plan_model.PlanSpec(slug=slug, topic=p.topic, version=1, domains=tuple(p.domains),
                               subjects=tuple(p.subjects), by=p.by, split=p.split, per_cell=p.per_cell,
                               max_docs=p.max_docs, refresh=refresh, members=tuple(p.member_records),
                               discover=({"anchors": p.anchors, "entity_class": p.entity_class}
                                         if p.anchors else None), dropped=tuple(p.dropped))
    old = plan_store.existing(slug)
    skill_md = old.skill_md if old is not None and not _unedited(old) else plan_model.default_skill_md(spec)
    spec = replace(spec, version=plan_store.next_version(spec, old, skill_md))
    built = {"at": now or _now(), "generations": generations,
             "grid": [{"subject": c.subject, "period": c.period, "domain": c.domain, "matching": c.matching,
                       "taken": len(c.doc_ids)} for c in p.cells],
             "documents": sorted(m.doc_id for m in p.materials),
             "model": model, "prompt_version": prompts.FACET_PROMPT_VERSION,
             "version": spec.version, "fingerprint": plan_model.fingerprint(spec, skill_md)}
    spec = replace(spec, built=built)
    meta = {"artmind_topic": store.FORMAT, "topic": p.topic, "slug": slug, "domains": list(p.domains),
            "subjects": list(p.subjects), "anchor": {"kind": "topic"}, "built": built, "model": model,
            "prompt_version": prompts.FACET_PROMPT_VERSION, "max_docs": p.max_docs, "sources": sources,
            "checks": checks}
    path = store.write_page(directory, slug, meta, rendered)
    folder = plan_store.write_plan(spec, skill_md)
    result.update(path=str(path), plan_path=str(folder), version=spec.version, checks=checks,
                  calls={**result["calls"], "made": made}, documents=len(sources))
    return result


def _unedited(old) -> bool:
    """A SKILL.md the operator never touched is regenerated for a changed definition."""
    return old.skill_md == plan_model.default_skill_md(old.spec)


def _generations(domains, session_factory) -> dict:
    from artmind.catalogue import state
    from artmind.graph_query import read_session

    try:
        with (session_factory or read_session)() as session:
            return state.generations(session, domains)
    except Exception:
        return {}


def _map_all(p: _Plan, map_llm) -> tuple[dict, int]:
    """Map answers per (doc_id, subject): from the cache, else one LLM call each (concurrent)."""
    from concurrent.futures import ThreadPoolExecutor

    answers: dict = {}
    todo = set((m.doc_id, c.subject) for m, c in p.to_map)
    for m, c in p.pairs:
        if (m.doc_id, c.subject) not in todo:
            answers[(m.doc_id, c.subject)] = mapcache.get(p.keys[(m.doc_id, c.subject)])

    def one(pair):
        m, c = pair
        subject = p.subjects[c.subject - 1]
        focus = subject if subject == p.topic else f"{subject} (part of: {p.topic})"
        answer = map_llm(prompts.map_prompt(focus, None, m))
        if isinstance(answer, dict):
            mapcache.put(p.keys[(m.doc_id, c.subject)], answer)
        return (m.doc_id, c.subject), answer

    workers = map_concurrency()
    if workers > 1 and len(p.to_map) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(p.to_map))) as pool:
            for k, a in pool.map(one, p.to_map):
                answers[k] = a
    else:
        for pair in p.to_map:
            k, a = one(pair)
            answers[k] = a
    return answers, len(p.to_map)


def _notes(p, answers: dict, used_ids: list | None = None) -> dict:
    """group -> [(title, note, date, period)] from the map answers, in pair order."""
    notes: dict = {}
    for m, c in p.pairs:
        if (m.doc_id, c.subject) not in answers:
            continue
        parsed = prompts.parse_map(answers.get((m.doc_id, c.subject)), {s.id for s in m.sources})
        if not parsed or not parsed["relevant"]:
            continue
        group = (c.subject, c.domain if p.split == "domain" else None)
        for n in parsed["notes"]:
            notes.setdefault(group, []).append((m.title or m.name or m.doc_id, n, m.date, c.period))
        if used_ids is not None and m.doc_id not in used_ids:
            used_ids.append(m.doc_id)
    return notes


def _reduce_key(p, group, rows) -> str:
    """The reduce cache key of one section: its notes, subject, page prompt version and model."""
    import hashlib

    lines = "\n".join(prompts.notes_text([(t, n)]) for t, n, _, _ in rows)
    text = json.dumps(["section", p.topic, p.subjects[group[0] - 1], group[1], lines,
                       prompts.FACET_PROMPT_VERSION, p.model or ""], ensure_ascii=False)
    return "r" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:31]


def _write_body(p: _Plan, map_llm, reduce_llm):
    answers, made = _map_all(p, map_llm)
    by_doc = {m.doc_id: m for m in p.materials}
    used_ids: list[str] = []
    notes = _notes(p, answers, used_ids)
    if not notes:
        return None, [], made
    # One section per subject; with a domain split, one `###` part per child domain inside it.
    sections: dict[int, list[tuple[str | None, str]]] = {}
    for group in p.groups:
        rows = notes.get(group)
        if not rows:
            continue
        subject = p.subjects[group[0] - 1]
        key = _reduce_key(p, group, rows)
        cached = None if p.full else mapcache.get(key)
        text = cached.get("text") if isinstance(cached, dict) else None
        if text is None:
            text = _section(p, subject, rows, reduce_llm)
            mapcache.put(key, {"text": text})
            made += 1
        sections.setdefault(group[0], []).append((group[1], text))
    texts = [(p.subjects[i - 1] + (f" ({dom})" if dom else ""), text)
             for i, parts_ in sorted(sections.items()) for dom, text in parts_]
    overview, differ = prompts.split_overview(reduce_llm(prompts.overview_prompt(p.topic, texts)))
    made += 1
    parts = ["## Overview", "", overview, ""]
    for i, parts_ in sorted(sections.items()):
        parts += [f"## {p.subjects[i - 1]}", ""]
        for dom, text in parts_:
            parts += ([f"### {dom}", ""] if dom else []) + [text, ""]
    timeline = _timeline(notes) if p.by != "none" or p.member_records else []
    if timeline:
        parts += ["## Timeline", "", *timeline, ""]
    parts += ["## Where sources differ", "", differ]
    return "\n".join(parts).strip() + "\n", [by_doc[d] for d in used_ids], made


def _section(p: _Plan, subject: str, rows, reduce_llm) -> str:
    lines = [prompts.notes_text([(t, n)]) for t, n, _, _ in rows]
    if sum(len(line) for line in lines) <= prompts.REDUCE_INPUT_CHARS:
        return prompts.strip_fences(reduce_llm(prompts.section_prompt(p.topic, subject, "\n".join(lines))))
    # Too long for one call: reduce per period first, then merge (spec §7.3).
    by_period: dict = {}
    for line, (_, _, _, period) in zip(lines, rows):
        by_period.setdefault(period or "", []).append(line)
    partials = []
    for period in sorted(by_period):
        for group in _groups(by_period[period], prompts.REDUCE_INPUT_CHARS):
            partials.append(prompts.strip_fences(reduce_llm(prompts.section_prompt(p.topic, subject, "\n".join(group)))))
    return prompts.strip_fences(reduce_llm(prompts.merge_section_prompt(p.topic, subject, partials)))


def _timeline(notes: dict) -> list[str]:
    """One dated line per claim whose document carries a date, oldest first, each claim once."""
    seen, rows = set(), []
    for group_rows in notes.values():
        for _, n, date, _ in group_rows:
            if not date or n["claim"] in seen:
                continue
            seen.add(n["claim"])
            rows.append((str(date)[:10], n["claim"], n["passages"]))
    return [f"- {d}: {claim} " + "".join(f"[#{i}]" for i in ids) for d, claim, ids in sorted(rows)]


def plan_from_page(page) -> dict:
    """Build arguments for a page built before plans existed: its topic as the one subject."""
    meta = page.meta
    return {"topic": str(meta.get("topic") or page.slug), "domains": list(meta.get("domains") or []),
            "facets": list(meta.get("subjects") or []) or None, "by": "auto", "split": "auto",
            "max_docs": int(meta.get("max_docs") or plan_model.DEFAULT_MAX_DOCS)}


def args_from_plan(spec) -> dict:
    """`build_topic` keyword arguments that rebuild a saved plan (refresh, the daily job)."""
    from artmind.topics import members as members_mod

    names = [m["name"] for m in spec.members]
    anchors = list((spec.discover or {}).get("anchors") or [])
    anchor_subject = members_mod.anchors_subject(anchors) if anchors and names else None
    facets = [s for s in spec.subjects if s not in names and s != anchor_subject]
    out = {"topic": spec.topic, "domains": list(spec.domains), "facets": facets or None, "by": spec.by,
           "split": spec.split, "per_cell": spec.per_cell, "max_docs": spec.max_docs, "refresh": spec.refresh,
           # the model of the last build keys the map cache: refresh with it
           "model": (spec.built or {}).get("model")}
    if names:
        out.update(members=names, anchors=anchors or None,
                   entity_class=(spec.discover or {}).get("entity_class"), dropped=tuple(spec.dropped))
    return out
