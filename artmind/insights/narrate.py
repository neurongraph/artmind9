"""LLM narration of findings and naming of circles (spec 2026-10-17 §11).

The LLM is a callable `prompt -> text`, injected so tests never reach a model;
`default_llm()` uses the configured provider. Every caption is checked by
prompts.check_narration; a rejected or failed one leaves `narration` None,
so the UI falls back to the template title. Nothing here can fail a run.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable

from loguru import logger

from artmind.insights.graphs import jaccard
from artmind.insights.prompts import (
    PROMPT_VERSION, check_circle_name, check_narration, circle_prompt, finding_prompt,
)

BATCH = 20
KEEP_NAME_JACCARD = 0.7
MAX_CIRCLE_MEMBERS = 10

LLM = Callable[[str], str]


def default_llm() -> tuple[LLM, str]:
    from artmind.extraction import call_llm
    from utils.functions import load_env, resolve_llm_model

    model = resolve_llm_model(load_env())
    return (lambda prompt: call_llm(model, prompt, call_type="insights")), model


def narration_key(f) -> str:
    payload = json.dumps({"v": PROMPT_VERSION, "d": f.detector, "t": f.title, "s": f.subjects, "x": f.stats},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def select_for_narration(findings, top: int) -> list:
    live = sorted((f for f in findings if f.diff != "gone"), key=lambda f: (-f.score, f.key))
    chosen = live[:top]
    seen = {f.key for f in chosen}
    chosen += [f for f in live if f.diff == "new" and f.key not in seen][:top]
    return chosen


def _parse(text: str) -> dict:
    from artmind.extraction import parse_json_response

    data = parse_json_response(text)
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def _apply_cached_findings(findings, previous) -> int:
    """Copy narration and narration_key from previous findings whose key still matches."""
    cache = {p.key: p for p in (previous or []) if p.narration and p.narration_key}
    n = 0
    for f in findings:
        if f.diff == "gone":
            continue
        k = narration_key(f)
        hit = cache.get(f.key)
        if hit is not None and hit.narration_key == k:
            f.narration, f.narration_key = hit.narration, k
            n += 1
    return n


def narrate_findings(findings, *, previous, ctx, llm: LLM, top: int) -> dict:
    """Mutates findings in place (narration, narration_key). Returns counts."""
    stats = Counter({"narrated": 0, "cached": 0, "rejected": 0, "calls": 0, "errors": 0, "skipped_batches": 0})
    stats["cached"] = _apply_cached_findings(findings, previous)
    todo = [f for f in select_for_narration(findings, top) if f.narration is None]
    known = {e.name.casefold() for e in ctx.entities if e.name}
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    for n, batch in enumerate(batches):
        stats["calls"] += 1
        try:
            replies = _parse(llm(finding_prompt(batch)))
        except Exception as exc:  # any provider or parse failure: leave the template titles
            logger.warning("insights narration batch failed: {}", exc)
            stats["errors"] += 1
            stats["skipped_batches"] = len(batches) - n - 1  # a failing provider is not asked again
            break
        for f in batch:
            text = str(replies.get(f.key, "") or "").strip()
            ok, reason = check_narration(text, f, known)
            if ok:
                f.narration, f.narration_key = text, narration_key(f)
                stats["narrated"] += 1
            else:
                logger.info("insights narration for {} rejected: {}", f.key, reason)
                stats["rejected"] += 1
    return dict(stats)


def _circle_input(c: dict, ctx) -> dict:
    by_id = {e._id: e for e in ctx.entities}
    rel_props = [p for (cls, p) in ctx.entity_props if cls == c["class"] and p.startswith("relationship_to")]
    members = []
    for m in c["members"][:MAX_CIRCLE_MEMBERS]:
        e = by_id.get(m)
        if e is None:
            continue
        info = {"name": e.name}
        if e.type:
            info["type"] = e.type
        for p in rel_props:
            v = ctx.entity_props[(c["class"], p)].get(m)
            if v:
                info["relationship"] = v
        members.append(info)
    days = {d for m in c["members"] if m in by_id for d in by_id[m].days}
    shared = Counter()
    for e in ctx.entities:
        if e.cls in ("ORGANIZATION", "LOCATION") and e._id not in ctx.anchors:
            n = len(days.intersection(e.days))
            if n:
                shared[e.name] = n
    return {"id": c["id"], "members": members, "shared": [n for n, _ in shared.most_common(5)]}


def _keep_circle_names(circles: list[dict], previous) -> list[dict]:
    """Reuse a previous llm name when the member sets overlap by KEEP_NAME_JACCARD or more.
    Returns the circles still to name."""
    old = [p for p in (previous or []) if p.get("name_source") == "llm"]
    todo = []
    for c in circles:
        match = max(old, key=lambda p: jaccard(set(p["members"]), set(c["members"])), default=None)
        if match is not None and match.get("class") == c.get("class") and \
                jaccard(set(match["members"]), set(c["members"])) >= KEEP_NAME_JACCARD:
            c["name"], c["name_source"] = match["name"], "llm"
        else:
            todo.append(c)
    return todo


def _dedupe_names(circles: list[dict], templates: dict) -> None:
    """Two circles may not share a name: the later one falls back to its template name."""
    seen = set()
    for c in circles:
        key = str(c["name"]).casefold()
        if key in seen and c.get("name_source") == "llm":
            c["name"], c["name_source"] = templates[c["id"]], "template"
        seen.add(str(c["name"]).casefold())


def _templates(circles: list[dict], ctx) -> dict:
    by_id = {e._id: e for e in ctx.entities} if ctx is not None else {}
    out = {}
    for c in circles:
        if c.get("name_source") == "llm":
            lead = by_id.get(c.get("lead"))
            out[c["id"]] = f"Circle around {lead.name if lead is not None else c.get('lead')}"
        else:
            out[c["id"]] = c["name"]
    return out


def carry_cached(findings, previous_findings, circles: list[dict], previous_circles, ctx=None) -> None:
    """The cache-hit pass alone, no LLM: captions whose key still matches and circle names
    kept by member overlap. Used when narration is off so earlier work is not discarded."""
    templates = _templates(circles, ctx)
    _keep_circle_names(circles, previous_circles)
    _dedupe_names(circles, templates)
    if any(c.get("name_source") == "llm" for c in circles):
        label_circle_findings(findings, circles)  # before the key check: circle_name is part of the stats
    _apply_cached_findings(findings, previous_findings)


def name_circles(circles: list[dict], *, previous, ctx, llm: LLM) -> dict:
    """Mutates circles in place (name, name_source). Returns counts."""
    stats = Counter({"named": 0, "kept": 0, "rejected": 0, "calls": 0, "errors": 0})
    templates = _templates(circles, ctx)
    todo = _keep_circle_names(circles, previous)
    stats["kept"] = len(circles) - len(todo)
    if not todo:
        _dedupe_names(circles, templates)
        return dict(stats)
    stats["calls"] += 1
    try:
        replies = _parse(llm(circle_prompt([_circle_input(c, ctx) for c in todo])))
    except Exception as exc:
        logger.warning("insights circle naming failed: {}", exc)
        stats["errors"] += 1
        _dedupe_names(circles, templates)
        return dict(stats)
    by_id = {e._id: e for e in ctx.entities}
    for c in todo:
        name = str(replies.get(c["id"], "") or "").strip().strip('"')
        members = [by_id[m].name for m in c["members"] if m in by_id and by_id[m].name]
        ok, _reason = check_circle_name(name, members)
        if ok:
            c["name"], c["name_source"] = name, "llm"
            stats["named"] += 1
        else:
            stats["rejected"] += 1
    _dedupe_names(circles, templates)
    return dict(stats)


def label_circle_findings(findings, circles: list[dict]) -> None:
    """Put each circle's name on its circle/share findings (stats.circle_name), matched by members."""
    for f in findings:
        if f.detector != "communities" or "members" not in f.stats:
            continue
        best = max(circles, key=lambda c: jaccard(set(c["members"]), set(f.stats["members"])), default=None)
        if best is not None and jaccard(set(best["members"]), set(f.stats["members"])) >= 0.5:
            f.stats["circle_name"] = best["name"]
    # Brokers recorded circle names at detection time, before naming: map template -> final name.
    renamed = {c["template_name"]: c["name"] for c in circles if c.get("template_name")}
    for f in findings:
        if f.detector == "brokers" and isinstance(f.stats.get("circles"), list):
            f.stats["circles"] = [renamed.get(n, n) for n in f.stats["circles"]]
