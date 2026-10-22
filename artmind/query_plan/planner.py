"""`artmind query plan`: one deterministic plan for a question (spec
docs/superpowers/specs/2026-10-22-query-plan-design.md §3.2, §4.2).

Stages: candidates (code) → classifier call 1 (domain?, view?, route) →
classifier call 2 (the chosen view's entity/enum params) → fill → assemble.
Read-only. `execute=True` only ever runs `views run`, and only for a
high-confidence view match with one parameter combination.
"""

from __future__ import annotations

import shlex
import time
from datetime import date

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import ViewError, jsonable
from artmind.kg_views.store import domain_covered
from artmind.query_plan import candidates, fill
from artmind.query_plan import config as plan_config
from artmind.query_plan import routes as routes_mod
from artmind.query_plan.classify import ClassifierUnavailable, Decision, LexicalClassifier, select_classifier
from artmind.query_plan.classify import lexical

PLAN_VERSION = 1
MAX_COMBINATIONS = 4
ALL_DOMAINS = "__all__"
NO_VIEW = "none"
ROUTING_TABLE = "SKILL.md"
VIEW_FALLBACK = "artmind query vector-text --domain {domains} {question} --compact"


class PlanError(ValueError):
    """The question cannot be planned: empty, or a forced classifier that is not configured."""


class _Calls:
    """Classifier calls with lexical fallback, counted and timed for the payload."""

    def __init__(self, classifier, warnings: list[str]):
        self.classifier = classifier
        self.warnings = warnings
        self.calls = 0
        self.latency_ms = 0.0
        self.fell_back = False
        self.reason: str | None = None

    def decide(self, question: str, decisions: list[Decision]) -> dict:
        if not decisions:
            return {}
        started = time.perf_counter()
        try:
            return self.classifier.decide(question, decisions)
        except ClassifierUnavailable as exc:
            self.fell_back = True
            self.reason = exc.reason
            self.warnings.append(f"decision classifier unavailable ({exc.reason}); used the lexical classifier")
            return LexicalClassifier().decide(question, decisions)
        finally:
            self.calls += 1
            self.latency_ms += (time.perf_counter() - started) * 1000


def _top(probabilities: dict, n: int, *, exclude: tuple = ()) -> list[tuple[str, float]]:
    items = [(k, p) for k, p in probabilities.items() if k not in exclude]
    return sorted(items, key=lambda kp: (-kp[1], kp[0]))[:n]


def _view_text(spec) -> str:
    return f"{spec.summary} Examples: {' / '.join(spec.examples)}"[:600]


def _candidate_text(ref: dict, narrator) -> str:
    label = f"{ref.get('name')} ({ref.get('entity_class')})"
    if narrator and "narrator" in (ref.get("matched_by") or []):
        return f"{label}: the narrator, the 'I' of the notes"
    description = (ref.get("description") or "").strip()
    return f"{label}: {description}" if description else label


def _context(selected: list[str], question: str, entities: list[dict], document: str | None,
             mentions: list) -> dict:
    named = [e for e in entities if e["via"] != "first_person"]
    resolved = [e for e in named if e["candidates"]] + [
        e for e in entities if e["via"] == "first_person" and e["candidates"]
    ]
    entity_name = None
    if resolved and resolved[0]["via"] != "first_person":
        entity_name = resolved[0]["candidates"][0]["name"]
    elif named:
        entity_name = named[0]["mention"]
    return {
        "domains": ",".join(selected),
        "question": question,
        "keywords": " ".join(candidates.keywords(question, mentions)),
        "document": document,
        "entity_id": resolved[0]["candidates"][0]["_id"] if resolved else None,
        "other_entity_id": resolved[1]["candidates"][0]["_id"] if len(resolved) > 1 else None,
        "entity_name": entity_name,
    }


def _match_view(spec, question: str, entities: list[dict], cfg, threshold: float, today: date, calls: _Calls):
    """view_match payload, entity picks per param, status, and the param confidence stages."""
    used: set = set()
    params: dict = {}
    groups_by_param: dict[str, dict[str, list[dict]]] = {}
    mention_of: dict[str, tuple[str, str]] = {}
    for entity in entities:
        for ref in entity["candidates"]:
            mention_of.setdefault(ref["_id"], (entity["mention"], entity["via"]))
    decisions = []
    for name, pspec in spec.params.items():
        if pspec.type == "entity":
            wanted = str(pspec.entity_class or "").casefold()
            pool, seen = [], set()
            for entity in entities:
                for ref in entity["candidates"]:
                    if ref["_id"] not in seen and (not wanted or str(ref.get("entity_class") or "").casefold() == wanted):
                        seen.add(ref["_id"])
                        pool.append(ref)
            groups = {f"c{i + 1}": g for i, g in enumerate(candidates.twin_groups(pool))}
            groups_by_param[name] = groups
            if groups:
                decisions.append(Decision(
                    name=f"param_{name}",
                    instructions=f"Which candidate is {pspec.description or name} in the question?",
                    options={**{k: _candidate_text(g[0], cfg.narrator) for k, g in groups.items()},
                             "none": "None of these"},
                    lexical_scores={k: 1.0 for k in groups},
                    default="none", default_confidence=0.0, none_mass=0.0,
                ))
        elif pspec.type == "enum":
            decisions.append(Decision(
                name=f"param_{name}",
                instructions=pspec.description or f"Which {name} does the question ask about?",
                options={**{v: v for v in pspec.values}, "none": "None of these"},
                lexical_scores=lexical.value_scores(question, pspec.values),
                default="none", default_confidence=0.0, none_mass=0.0,
            ))
    # Dates first: an int "year" param must not consume the year inside "March 2026" that a date needs.
    scalars = [(n, p) for n, p in spec.params.items() if p.type not in ("entity", "enum")]
    for name, pspec in sorted(scalars, key=lambda np: np[1].type != "date"):
        params[name] = fill.fill_scalar(pspec, question, today, used) or {"type": pspec.type, "via": None,
                                                                           "value": None}
    answers = calls.decide(question, decisions)
    statuses: list[str] = []
    stages: list[tuple[str, float]] = []
    picks: dict[str, list[dict]] = {}
    for name, pspec in spec.params.items():
        answer = answers.get(f"param_{name}")
        if pspec.type == "entity":
            groups = groups_by_param[name]
            entry = {"type": "entity", "via": None, "input": None, "chosen": None,
                     "candidates": [g[0] for g in groups.values()], "twins": False, "confidence": 0.0}
            if not groups or answer is None or answer.choice == "none":
                statuses.append("no_match")
            else:
                group = groups[answer.choice]
                text, via = mention_of.get(group[0]["_id"], (None, "mention"))
                entry.update(input=text, via="narrator" if via == "first_person" else "mention",
                             confidence=answer.confidence)
                stages.append((f"param:{name}", answer.confidence))
                if answer.confidence < threshold:
                    statuses.append("needs_disambiguation")
                elif len(group) > 1:
                    entry.update(twins=True, candidates=group)
                    picks[name] = group
                else:
                    entry["chosen"] = group[0]
                    picks[name] = [group[0]]
            params[name] = entry
        elif pspec.type == "enum":
            if answer is not None and answer.choice != "none":
                params[name] = {"type": "enum", "via": "classifier", "value": answer.choice,
                                "confidence": answer.confidence}
            elif not pspec.required:
                params[name] = {"type": "enum", "via": "default", "value": jsonable(pspec.default)}
            else:
                params[name] = {"type": "enum", "via": None, "value": None}
                statuses.append("missing_param")
        elif params[name]["via"] is None:
            statuses.append("missing_param")
    status = next((s for s in ("no_match", "needs_disambiguation", "missing_param") if s in statuses), "ok")
    params = {name: params[name] for name in spec.params}  # the view's own order, so --param order is stable
    view_match = {"name": spec.name, "version": spec.version, "confidence": None, "status": status,
                  "params": params, "twins": [n for n, e in params.items() if e.get("twins")]}
    return view_match, picks, status, stages


def _view_args(params: dict, combo: dict) -> dict[str, str]:
    raw: dict[str, str] = {}
    for name, entry in params.items():
        if name in combo:
            raw[name] = "@" + combo[name]["_id"]
        elif entry.get("via") not in (None, "default"):
            value = entry["value"]
            raw[name] = ",".join(map(str, value)) if isinstance(value, list) else str(value)
    return raw


def _view_cmd(name: str, selected: list[str], raw: dict[str, str]) -> str:
    parts = ["artmind query views run", shlex.quote(name), "--domain", shlex.quote(",".join(selected))]
    parts += [f"--param {shlex.quote(f'{k}={v}')}" for k, v in raw.items()]
    return " ".join(parts) + " --compact"


def plan(question: str, domains: list[str] | None = None, *, execute: bool = False,
         classifier: str = "auto", today: date | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise PlanError("the question is empty")
    today = today or date.today()
    warnings: list[str] = []
    all_routes = routes_mod.load_routes()

    # ── candidates that need no domain decision ──────────────────────────────
    overview = graph_query.domains_overview().get("rows") or []
    options = candidates.domain_options(overview)
    given_cfg = plan_config.merge([plan_config.load_config(d) for d in domains]) if domains else None
    try:
        clf, auto_reason = select_classifier(
            classifier, given_cfg.classifier if given_cfg else "auto", given_cfg.decision_model if given_cfg else None,
        )
    except ValueError as exc:
        raise PlanError(str(exc)) from exc
    calls = _Calls(clf, warnings)
    first_person = given_cfg.first_person if given_cfg else plan_config.DEFAULT_FIRST_PERSON
    mentions = candidates.find_mentions(question, first_person)
    scope = list(domains) if domains else [r["domain"] for r in overview if r.get("domain")]
    document = candidates.match_document(question, candidates.document_names(scope)) if scope else None
    has_store = any(
        r.get("table_count") for r in overview
        if any(r.get("domain") == d or str(r.get("domain")).startswith(d + ".") for d in scope)
    )
    facts = lexical.Facts(question=question.casefold(), mentions=len(mentions), document=document,
                          has_store=has_store, has_date=lexical.has_date(question))
    views, invalid = views_store.list_views(list(domains) if domains else None)
    for bad in invalid:
        warnings.append(f"view {bad['name']} is invalid and was skipped: {bad['error']}")
    views_by_name = {v.spec.name: v for v in views}

    # ── classifier call 1: domain?, view?, route ────────────────────────────
    decisions = []
    if not domains and len(options) > 1:
        decisions.append(Decision(
            name="domain", instructions="Which knowledge domain holds the answer to the question?",
            options={**{o.key: o.text() for o in options}, ALL_DOMAINS: "No single domain: search every domain."},
            lexical_scores=lexical.domain_scores(question, options),
            default=ALL_DOMAINS, default_confidence=0.0, none_mass=0.5,
        ))
    if views:
        decisions.append(Decision(
            name="view",
            instructions="Which saved query answers this question exactly? Choose none if no saved query fits.",
            options={**{n: _view_text(v.spec) for n, v in views_by_name.items()},
                     NO_VIEW: "No saved query answers this question."},
            lexical_scores=lexical.view_scores(question, [v.spec for v in views]),
            default=NO_VIEW, default_confidence=1.0, none_mass=1.0,
        ))
    decisions.append(Decision(
        name="route", instructions="What kind of question is this?",
        options={r.id: r.description for r in all_routes},
        lexical_scores=lexical.route_scores(all_routes, facts),
        default="search", default_confidence=0.5, none_mass=0.5,
    ))
    answers = calls.decide(question, decisions)

    # ── domains ─────────────────────────────────────────────────────────────
    domain_candidates: list[dict] = []
    if domains:
        selected, source, domain_conf = list(domains), "given", None
    elif len(options) == 1:
        selected, source, domain_conf = [options[0].key], ("parent" if options[0].is_parent else "matched"), 1.0
    elif not options:
        selected, source, domain_conf = [], "all", 0.0
    else:
        answer = answers["domain"]
        domain_candidates = [{"domain": k, "p": p} for k, p in _top(answer.probabilities, 3, exclude=(ALL_DOMAINS,))]
        if answer.choice == ALL_DOMAINS:
            selected, source, domain_conf = [r["domain"] for r in overview if r.get("domain")], "all", 0.0
        else:
            chosen = next(o for o in options if o.key == answer.choice)
            selected, source = [chosen.key], ("parent" if chosen.is_parent else "matched")
            domain_conf = answer.confidence
    cfg = given_cfg or plan_config.merge([plan_config.load_config(d) for d in selected])
    threshold = cfg.confidence_threshold

    # ── entities ────────────────────────────────────────────────────────────
    entities = []
    for mention in mentions:
        try:
            found = candidates.resolve_mention(mention, selected, cfg.narrator) if selected else []
        except Exception as exc:  # one mention failing must not sink the plan (spec §7)
            warnings.append(f"could not resolve {mention.text!r}: {exc}")
            found = []
        if mention.via == "first_person" and cfg.narrator and selected and not found:
            warnings.append(f"narrator {cfg.narrator.name!r} ({cfg.narrator.entity_class}) is not in the graph; "
                            "first-person words were not mapped")
        entities.append({"mention": mention.text, "span": [mention.start, mention.end], "via": mention.via,
                         "candidates": found})
    for entity in entities:
        for group in candidates.twin_groups(entity["candidates"]):
            if len(group) > 1:
                warnings.append(f"{len(group)} nodes share the name {group[0].get('name')!r} "
                                f"({group[0].get('entity_class')})")

    # ── view match (classifier call 2) or route ─────────────────────────────
    view_answer = answers.get("view")
    route_answer = answers["route"]
    chosen_view = None
    if view_answer is not None and view_answer.choice != NO_VIEW and view_answer.confidence >= threshold:
        loaded = views_by_name[view_answer.choice]
        if selected and all(domain_covered(d, loaded.spec.domains) for d in selected):
            chosen_view = loaded
        else:
            warnings.append(f"view {loaded.spec.name} matched but does not cover {selected}; not used")
    context = _context(selected, question, entities, document, mentions)
    stages: list[tuple[str, float]] = []
    if source != "given":
        stages.append(("domain", domain_conf if source != "all" else 0.0))
    if view_answer is not None:
        stages.append(("view", view_answer.confidence))
    view_match, status, unfilled, too_many = None, None, False, False
    rendered: list[dict] = []
    raws: list[dict] = []
    route_obj = None
    if chosen_view is not None:
        spec = chosen_view.spec
        view_match, picks, status, param_stages = _match_view(spec, question, entities, cfg, threshold, today, calls)
        view_match["confidence"] = view_answer.confidence
        stages += param_stages
        route_id, route_conf = "view", view_answer.confidence
        if status == "ok":
            combos = fill.combinations(picks, MAX_COMBINATIONS)
            too_many = combos is None
            for combo in (combos or []):
                raw = _view_args(view_match["params"], combo)
                raws.append(raw)
                why = f"saved view {spec.name} answers this shape (p={view_answer.confidence:.2f})"
                rendered.append({"cmd": _view_cmd(spec.name, selected, raw),
                                 "why": why + (" - one run per twin node" if combos and len(combos) > 1 else "")})
        fallback_cmd = routes_mod.render(VIEW_FALLBACK, context)
        fallbacks = ([{"when": "zero_rows", "cmd": fallback_cmd,
                       "why": "the view found nothing; search the notes for the same question"}]
                     if fallback_cmd else [])
        read: list[str] = []
    else:
        route_obj = next(r for r in all_routes if r.id == route_answer.choice)
        route_id, route_conf = route_obj.id, route_answer.confidence
        stages.append(("route", route_conf))
        for template in route_obj.steps:
            cmd = routes_mod.render(template, context)
            if cmd is None:
                unfilled = True
            else:
                rendered.append({"cmd": cmd, "why": f"route {route_obj.id} (p={route_conf:.2f})"})
        fallbacks = []
        for fallback in route_obj.fallbacks:
            cmd = routes_mod.render(fallback.cmd, context)
            if cmd:
                fallbacks.append({"when": fallback.when, "cmd": cmd, "why": f"if the first step returns {fallback.when}"})
        read = list(route_obj.read)
    rendered = [{"n": i, **step} for i, step in enumerate(rendered, 1)]

    # ── confidence ─────────────────────────────────────────────────────────
    weakest, score = min(stages, key=lambda s: s[1])
    high = (score >= threshold and status in (None, "ok") and not unfilled and not too_many
            and bool(rendered) and bool(selected))
    level = "high" if high else "low"
    steps, suggested = (rendered, []) if high else ([], rendered)
    if not high:
        read = [*read, ROUTING_TABLE]

    # ── --execute ───────────────────────────────────────────────────────────
    executed = None
    rows: list = []
    rows_total, truncated = 0, False
    if execute:
        if not high:
            warnings.append("--execute ignored: the plan is not high-confidence")
        elif chosen_view is None:
            warnings.append("--execute ignored: the route is not a saved view")
        elif len(raws) != 1:
            warnings.append(f"--execute ignored: the view has {len(raws)} parameter combinations (twin nodes)")
        else:
            try:
                envelope = views_runner.run_view(chosen_view.spec, selected, raws[0])
                rows = envelope.pop("rows", []) or []
                rows_total = envelope.pop("rows_total", len(rows))
                truncated = bool(envelope.pop("truncated", False))
                executed = {"cmd": steps[0]["cmd"], "status": envelope.get("status", "ok"), "error": None,
                            "result": envelope}
            except ViewError as exc:
                executed = {"cmd": steps[0]["cmd"], "status": "error", "error": str(exc), "result": None}

    resolved_classes = {ref.get("entity_class") for e in entities for ref in e["candidates"]}
    lexical_used = clf.backend == "lexical" or calls.fell_back
    return {
        "plan_version": PLAN_VERSION,
        "question": question,
        "domains": {"selected": selected, "source": source, "confidence": domain_conf,
                    "candidates": domain_candidates},
        "route": {
            "id": route_id, "confidence": route_conf,
            "alternatives": [{"id": k, "p": p} for k, p in _top(route_answer.probabilities, 2, exclude=(route_id,))],
            "triggered_by": lexical.triggered(route_obj, facts) if (route_obj and lexical_used) else [],
        },
        "view_match": view_match,
        "entities": entities,
        "store": candidates.store_facts(selected, question, resolved_classes) if selected else None,
        "confidence": {"level": level, "score": round(score, 4), "threshold": threshold, "weakest": weakest},
        "steps": steps,
        "suggested_steps": suggested,
        "fallbacks": fallbacks,
        "read": read,
        "classifier": {"backend": clf.backend, "model": clf.model, "calls": calls.calls,
                       "latency_ms": round(calls.latency_ms, 1), "fell_back": calls.fell_back,
                       "reason": calls.reason or auto_reason},
        "warnings": warnings,
        "executed": executed,
        "rows": rows,
        "rows_total": rows_total,
        "truncated": truncated,
    }
