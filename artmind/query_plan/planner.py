"""`artmind query plan`: one deterministic plan for a question (spec
docs/superpowers/specs/2026-10-22-query-plan-design.md §3.2, §4.2).

Stages: candidates (code) → classifier call 1 (domain?, view?, route) →
classifier call 2 (the chosen view's entity/enum params) → fill → assemble.
Read-only. `execute=True` only ever runs `views run`, and only for a
high-confidence view match with one parameter combination.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import date

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import ViewError, jsonable
from artmind.kg_views.store import domain_covered
from artmind.query_plan import candidates, catalogue, fill
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
    """The question cannot be planned: empty, a bad plan config, or a forced classifier that is not configured."""


class _Calls:
    """Classifier calls with a sticky lexical fallback, counted and timed for the payload.

    One `decide` is one call, even when the backend fails and the lexical classifier redoes it.
    Once the backend has been unavailable, later calls go straight to the lexical classifier."""

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
        self.calls += 1
        try:
            if not self.fell_back:
                try:
                    answers = dict(self.classifier.decide(question, decisions))
                except ClassifierUnavailable as exc:
                    self.fell_back = True
                    self.reason = exc.reason
                    self.warnings.append(f"decision classifier unavailable ({exc.reason}); used the lexical classifier")
                else:
                    missing = [d for d in decisions if d.name not in answers]
                    if missing:
                        names = ", ".join(d.name for d in missing)
                        self.warnings.append(f"decision classifier left out {names}; used the lexical classifier for it")
                        answers.update(LexicalClassifier().decide(question, missing))
                    return answers
            return LexicalClassifier().decide(question, decisions)
        finally:
            self.latency_ms += (time.perf_counter() - started) * 1000


@dataclass
class _Steps:
    """What a view or a route produced: ready commands, or the reason there are none."""

    steps: list[dict] = field(default_factory=list)
    fallbacks: list[dict] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    raws: list[dict] = field(default_factory=list)
    stages: list[tuple[str, float]] = field(default_factory=list)
    view_match: dict | None = None
    status: str | None = None
    unfilled: bool = False
    too_many: bool = False
    reason: str | None = None

    @property
    def blocker(self) -> str | None:
        if self.status not in (None, "ok"):
            return "view_status"
        if self.too_many:
            return "too_many"
        if self.unfilled:
            return "unfilled"
        return None


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


_IRREGULAR = {"people": "person", "children": "child", "men": "man", "women": "woman"}


def _stems(word: str) -> set[str]:
    """The word and its plausible singulars (places -> place, cities -> city, cases -> case)."""
    if word in _IRREGULAR:
        return {word, _IRREGULAR[word]}
    out = {word}
    for suffix in ("ies", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            stem = word[: -len(suffix)]
            out |= {stem + "y", stem + "e", stem} if suffix == "ies" else {stem, stem + "e"}
    return out


def _get_catalogue(cover: list[str]):
    return catalogue.get(cover)


def _minimal_cover(scope: list[str]) -> list[str]:
    """The scope without a domain whose parent is also in it: a parent catalogue unions its leaves."""
    unique = sorted(set(scope))
    return [d for d in unique if not any(d != e and candidates.covered_by(d, e) for e in unique)]


def _selected_cats(cats: dict, selected: list[str]) -> list | None:
    """The catalogues that answer for `selected`, or None when some selected domain is covered only by
    a broader catalogue (its names would leak across domains) or by none."""
    if not selected or not cats:
        return None
    found: dict = {}
    for d in selected:
        mine = candidates.catalogue_cats_for(d, cats)
        if not mine:
            return None
        found.update({c.domain: c for c in mine})
    return [found[k] for k in sorted(found)]


def _selected_names(cats: dict, selected: list[str]) -> list | None:
    """Name entries of the selected domains: the domains' own catalogues, or, where only a broader
    catalogue exists, its entries filtered by their own domain. None when some domain has neither."""
    if not selected or not cats:
        return None
    found: dict = {}
    for d in selected:
        own = candidates.catalogue_cats_for(d, cats)
        if own:
            entries = [n for c in own for n in c.names]
        else:
            broader = [c for k, c in sorted(cats.items()) if candidates.covered_by(d, k)]
            if not broader:
                return None
            entries = [n for c in broader for n in c.names if n.domain and candidates.covered_by(n.domain, d)]
        found.update({n._id: n for n in entries})
    return list(found.values())


def _merged_names(cats) -> list:
    return list({n._id: n for c in cats for n in c.names if n._id is not None}.values())


def _class_word(question: str, selected: list[str], overview: list[dict], cats: list | None = None) -> str | None:
    """A class of the selected domains that the question names ("which PERSON entities", "list the
    persons", "bank accounts" for BANK_ACCOUNT). All catalogue classes when there are any, else the
    top classes of `domains-overview`."""
    top: list[str] = []
    if cats:
        counts: dict[str, int] = {}
        for cat in cats:
            for name, count, *_ in cat.classes:
                counts[name] = counts.get(name, 0) + count
        top = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    else:
        for row in overview:
            name = str(row.get("domain") or "")
            if any(name == d or name.startswith(d + ".") for d in selected):
                top += [c for c in row.get("top_classes") or [] if isinstance(c, str) and c.strip()]
    words = re.findall(r"[a-z0-9]+", question.casefold())
    for cls in top:
        parts = [p for p in re.split(r"[_\s]+", cls.casefold()) if p]
        for i in range(len(words) - len(parts) + 1):
            if all(_stems(parts[j]) & _stems(words[i + j]) for j in range(len(parts))):
                return cls
    return None


def _context(selected: list[str], question: str, entities: list[dict], document: str | None,
             mentions: list, overview: list[dict] | None = None, cats: list | None = None) -> dict:
    """Placeholder values. The primary entity is the first named mention, resolved or not. The
    narrator is the second entity beside exactly one named mention, and the id fallback when
    nobody is named; it never supplies a name, since its name is no search term."""
    # A name the question spells out (capitalised, quoted) outranks one the gazetteer found.
    named = sorted((e for e in entities if e["via"] != "first_person"), key=lambda e: e["via"] == "catalogue")
    narrator = next((e for e in entities if e["via"] == "first_person" and e["candidates"]), None)
    primary = named[0] if named else narrator
    second = named[1] if len(named) > 1 else (narrator if len(named) == 1 else None)
    entity_name = None
    if named:
        entity_name = named[0]["candidates"][0]["name"] if named[0]["candidates"] else named[0]["mention"]
    return {
        "domains": ",".join(selected),
        "question": question,
        "keywords": " ".join(candidates.keywords(question, mentions)),
        "document": document,
        "entity_id": primary["candidates"][0]["_id"] if primary and primary["candidates"] else None,
        "other_entity_id": second["candidates"][0]["_id"] if second and second["candidates"] else None,
        "entity_name": entity_name,
        "entity_class": primary["candidates"][0].get("entity_class") if primary and primary["candidates"] else None,
        "other_entity_class": (second["candidates"][0].get("entity_class")
                               if second and second["candidates"] else None),
        "class": _class_word(question, selected, overview or [], cats),
    }


# Lexical weight of a candidate group: a name match (exact, catalogue, narrator) outweighs fulltext
# neighbours, so one exact match among up to four fulltext ones still clears the 0.7 threshold.
NAME_EVIDENCE = frozenset({"exact", "catalogue", "narrator"})
FULLTEXT_WEIGHT = 0.1


def _group_weight(group: list[dict]) -> float:
    return 1.0 if any(NAME_EVIDENCE & set(r.get("matched_by") or ()) for r in group) else FULLTEXT_WEIGHT


def _pick_group(answer, groups: dict[str, list[dict]]) -> tuple[str | None, float]:
    """The group an answer chose; if an earlier param already took it, the best group left (or none)."""
    if answer is None or not groups or answer.choice == "none":
        return None, 0.0
    if answer.choice in groups:
        return answer.choice, answer.confidence
    best = max(groups, key=lambda k: (answer.probabilities.get(k, 0.0), -list(groups).index(k)))
    if answer.probabilities.get("none", 0.0) > answer.probabilities.get(best, 0.0):
        return None, 0.0
    return best, answer.probabilities.get(best, 0.0)


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
                    lexical_scores={k: _group_weight(g) for k, g in groups.items()},
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
    taken: set[str] = set()  # one entity fills at most one param of a view
    for name, pspec in spec.params.items():
        answer = answers.get(f"param_{name}")
        if pspec.type == "entity":
            groups = {k: g for k, g in groups_by_param[name].items() if not {r["_id"] for r in g} & taken}
            entry = {"type": "entity", "via": None, "input": None, "chosen": None,
                     "candidates": [g[0] for g in groups.values()], "twins": False, "confidence": 0.0}
            choice, confidence = _pick_group(answer, groups)
            if choice is None:
                statuses.append("no_match")
            else:
                group = groups[choice]
                taken.update(r["_id"] for r in group)
                text, via = mention_of.get(group[0]["_id"], (None, "mention"))
                entry.update(input=text, via="narrator" if via == "first_person" else "mention",
                             confidence=confidence)
                stages.append((f"param:{name}", confidence))
                if confidence < threshold:
                    statuses.append("needs_disambiguation")
                elif len(group) > 1:
                    entry.update(twins=True, candidates=group)
                    picks[name] = group
                else:
                    entry["chosen"] = group[0]
                    picks[name] = [group[0]]
            params[name] = entry
        elif pspec.type == "enum":
            if answer is not None and answer.choice != "none" and answer.confidence >= threshold:
                stages.append((f"param:{name}", answer.confidence))
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


def _choose_domains(domains, options, overview, answers):
    """(selected, source, confidence, candidates) from the given domains or the domain decision."""
    if domains:
        return list(domains), "given", None, []
    if len(options) == 1:
        return [options[0].key], ("parent" if options[0].is_parent else "matched"), 1.0, []
    if not options:
        return [], "all", 0.0, []
    answer = answers["domain"]
    shown = [{"domain": k, "p": p} for k, p in _top(answer.probabilities, 3, exclude=(ALL_DOMAINS,))]
    if answer.choice == ALL_DOMAINS:
        return [r["domain"] for r in overview if r.get("domain")], "all", 0.0, shown
    chosen = next(o for o in options if o.key == answer.choice)
    return [chosen.key], ("parent" if chosen.is_parent else "matched"), answer.confidence, shown


def _resolve_entities(mentions, selected: list[str], cfg, warnings: list[str],
                      index: dict | None = None) -> list[dict]:
    """`index` is the selected domains' catalogue name index (None without one). A catalogue mention takes
    its candidates from it and runs no name query; without an index it is resolved like any other. A
    catalogue mention with no candidate in the selected domains is dropped."""
    entities = []
    for mention in mentions:
        try:
            if index is not None and selected and mention.via == "catalogue":
                hit = index.get(candidates._norm(mention.text)) or ()
                found = [candidates.catalogue_ref(e) for e in hit[:candidates.MAX_CANDIDATES]]
            else:
                found = candidates.resolve_mention(mention, selected, cfg.narrator) if selected else []
        except Exception as exc:  # one mention failing must not sink the plan (spec §7)
            warnings.append(f"could not resolve {mention.text!r}: {exc}")
            found = []
        if mention.via == "first_person" and cfg.narrator and selected and not found:
            warnings.append(f"narrator {cfg.narrator.name!r} ({cfg.narrator.entity_class}) is not in the graph; "
                            "first-person words were not mapped")
        if mention.via == "catalogue" and not found:
            continue  # the name belongs to a domain that is not selected: it is no mention here
        entities.append({"mention": mention.text, "span": [mention.start, mention.end], "via": mention.via,
                         "candidates": found})
    for entity in entities:
        for group in candidates.twin_groups(entity["candidates"]):
            if len(group) > 1:
                warnings.append(f"{len(group)} nodes share the name {group[0].get('name')!r} "
                                f"({group[0].get('entity_class')})")
    return entities


def _view_steps(loaded, view_answer, entities, question, cfg, threshold, today, calls, selected, context) -> _Steps:
    """A chosen view: its parameter fill (classifier call 2), one run per twin combination."""
    spec = loaded.spec
    view_match, picks, status, stages = _match_view(spec, question, entities, cfg, threshold, today, calls)
    view_match["confidence"] = view_answer.confidence
    out = _Steps(view_match=view_match, status=status, stages=stages)
    if status != "ok":
        out.reason = f"view {spec.name}: status {status}"
    else:
        combos = fill.combinations(picks, MAX_COMBINATIONS)
        if combos is None:
            out.too_many = True
            count = 1
            for group in picks.values():
                count *= len(group)
            out.reason = f"{count} twin combinations exceed {MAX_COMBINATIONS}"
        for combo in (combos or []):
            raw = _view_args(view_match["params"], combo)
            out.raws.append(raw)
            why = f"saved view {spec.name} answers this shape (p={view_answer.confidence:.2f})"
            out.steps.append({"cmd": _view_cmd(spec.name, selected, raw),
                              "why": why + (" - one run per twin node" if len(combos) > 1 else "")})
    if out.steps:  # the fallback only goes with view steps that were emitted
        fallback_cmd = routes_mod.render(VIEW_FALLBACK, context)
        if fallback_cmd:
            out.fallbacks.append({"when": "zero_rows", "cmd": fallback_cmd,
                                  "why": "the view found nothing; search the notes for the same question"})
    return out


def _route_steps(route, confidence: float, context: dict, store_kind: str | None = None) -> _Steps:
    out = _Steps(stages=[("route", confidence)])
    missing: set[str] = set()
    for template in route.steps_for(store_kind):
        cmd = routes_mod.render(template, context)
        if cmd is None:
            out.unfilled = True
            missing |= {p for p in routes_mod.placeholders(template) if not str(context.get(p) or "").strip()
                        or str(context.get(p)).startswith("-")}
        else:
            out.steps.append({"cmd": cmd, "why": f"route {route.id} (p={confidence:.2f})"})
    for template in route.optional_steps:  # skipped silently: never unfilled, never a confidence factor
        cmd = routes_mod.render(template, context)
        if cmd:
            out.steps.append({"cmd": cmd, "why": f"route {route.id} (optional)"})
    if out.unfilled:
        out.reason = f"route {route.id} needs {', '.join(sorted(missing)) or 'a value it could not fill'}"
    for fallback in route.fallbacks:
        cmd = routes_mod.render(fallback.cmd, context)
        if cmd:
            out.fallbacks.append({"when": fallback.when, "cmd": cmd, "why": f"if the first step returns {fallback.when}"})
    out.read = list(route.read)
    return out


def _search_steps(all_routes, context: dict) -> list[dict]:
    """The search route's commands, the guidance for a plan that has none of its own."""
    search = next(r for r in all_routes if r.id == "search")
    steps = []
    for template in search.steps:
        cmd = routes_mod.render(template, context)
        if cmd:
            steps.append({"cmd": cmd, "why": "route search (no step of the chosen route could be built)"})
    return steps


def _execute(chosen_view, selected, high: bool, steps: list[dict], raws: list[dict], warnings: list[str]):
    """(executed, rows, rows_total, truncated); only a high-confidence single-combination view runs."""
    if not high:
        warnings.append("--execute ignored: the plan is not high-confidence")
    elif chosen_view is None:
        warnings.append("--execute ignored: the route is not a saved view")
    elif len(raws) != 1:
        warnings.append(f"--execute ignored: the view has {len(raws)} parameter combinations (twin nodes)")
    else:
        try:
            envelope = views_runner.run_view(chosen_view.spec, selected, raws[0])
        except ViewError as exc:
            return {"cmd": steps[0]["cmd"], "status": "error", "error": str(exc), "result": None}, [], 0, False
        rows = envelope.pop("rows", []) or []
        rows_total = envelope.pop("rows_total", len(rows))
        truncated = bool(envelope.pop("truncated", False))
        return ({"cmd": steps[0]["cmd"], "status": envelope.get("status", "ok"), "error": None,
                 "result": envelope}, rows, rows_total, truncated)
    return None, [], 0, False


def _configs(domains: list[str]):
    try:
        return plan_config.merge([plan_config.load_config(d) for d in domains])
    except plan_config.PlanConfigError as exc:
        raise PlanError(str(exc)) from exc


def plan(question: str, domains: list[str] | None = None, *, execute: bool = False,
         classifier: str = "auto", today: date | None = None, model: str | None = None) -> dict:
    """`model` overrides the decision model (else the domain config's, else ARTMIND_DECISION_MODEL)."""
    question = (question or "").strip()
    if not question:
        raise PlanError("the question is empty")
    today = today or date.today()
    warnings: list[str] = []
    all_routes = routes_mod.load_routes()

    # ── candidates that need no domain decision ──────────────────────────────
    overview = graph_query.domains_overview().get("rows") or []
    scope = list(domains) if domains else [r["domain"] for r in overview if r.get("domain")]
    cover = _minimal_cover(scope)
    cats: dict = {}
    sources: dict = {}
    if cover:
        try:
            cats, sources = _get_catalogue(cover)
        except Exception as exc:  # the catalogue is an accelerator: plan as before without it (spec §11)
            cats, sources = {}, {}
            warnings.append(f"domain catalogue unavailable: {exc}")
    options = candidates.domain_options(overview, cats)
    given_cfg = _configs(domains) if domains else None
    try:
        clf, auto_reason = select_classifier(
            classifier, given_cfg.classifier if given_cfg else "auto",
            model or (given_cfg.decision_model if given_cfg else None),
        )
    except ValueError as exc:
        raise PlanError(str(exc)) from exc
    calls = _Calls(clf, warnings)
    first_person = given_cfg.first_person if given_cfg else plan_config.DEFAULT_FIRST_PERSON
    mentions = candidates.find_mentions(question, first_person)
    cover_cats = [cats[d] for d in cover if d in cats]
    all_index = candidates.name_index(_merged_names(cover_cats)) if cover_cats else {}
    mentions = sorted([*mentions, *candidates.gazetteer_mentions(question, mentions, all_index)],
                      key=lambda m: m.start)
    if scope and len(cover_cats) == len(cover) and cover:
        document_pool = sorted({d for c in cover_cats for d in c.documents})
    else:
        document_pool = candidates.document_names(scope) if scope else []
    document = candidates.match_document(question, document_pool) if scope else None
    has_store = any(
        r.get("table_count") for r in overview
        if any(r.get("domain") == d or str(r.get("domain")).startswith(d + ".") for d in scope)
    )
    # The first-person mention counts as an entity only where a narrator is configured to resolve it.
    named_mentions = sum(1 for m in mentions if m.via != "first_person")
    narrated = bool(any(m.via == "first_person" for m in mentions) and given_cfg and given_cfg.narrator)
    facts = lexical.Facts(question=question.casefold(), mentions=named_mentions + int(narrated), document=document,
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

    selected, source, domain_conf, domain_candidates = _choose_domains(domains, options, overview, answers)
    cfg = given_cfg or _configs(selected)
    threshold = cfg.confidence_threshold

    selected_cats = _selected_cats(cats, selected)
    if selected_cats is not None and {c.domain for c in selected_cats} == {c.domain for c in cover_cats}:
        selected_index = all_index
    else:
        names = _selected_names(cats, selected)
        selected_index = candidates.name_index(names) if names is not None else None
    entities = _resolve_entities(mentions, selected, cfg, warnings, selected_index)

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
    resolved_classes = {ref.get("entity_class") for e in entities for ref in e["candidates"]}
    store = None
    if selected:
        try:
            store = candidates.store_facts(selected, question, resolved_classes)
        except Exception as exc:  # the structured store is optional context (spec §7)
            warnings.append(f"store facts unavailable: {exc}")
    kept = {e["span"][0] for e in entities}
    context = _context(selected, question, entities, document, [m for m in mentions if m.start in kept], overview,
                       selected_cats)
    stages: list[tuple[str, float]] = []
    if source != "given":
        stages.append(("domain", domain_conf if source != "all" else 0.0))
    if view_answer is not None:
        stages.append(("view", view_answer.confidence))
    route_obj = None
    if chosen_view is not None:
        built = _view_steps(chosen_view, view_answer, entities, question, cfg, threshold, today, calls, selected,
                            context)
        route_id, route_conf = "view", view_answer.confidence
    else:
        route_obj = next(r for r in all_routes if r.id == route_answer.choice)
        route_id, route_conf = route_obj.id, route_answer.confidence
        built = _route_steps(route_obj, route_conf, context, (store or {}).get("kind"))
    stages += built.stages
    rendered, fallbacks, read = built.steps, built.fallbacks, built.read

    # ── confidence ─────────────────────────────────────────────────────────
    weakest, score = min(stages, key=lambda s: s[1])
    blocker = built.blocker
    if blocker and score >= threshold:  # a stage below the threshold stays the weakest link
        weakest = blocker
    high = score >= threshold and not blocker and bool(rendered) and bool(selected)
    if built.reason:
        warnings.append(built.reason + ("" if rendered else "; suggested search instead"))
    if not high and not rendered:  # a low plan never goes out without guidance
        rendered = _search_steps(all_routes, context)
        fallbacks = []
        if not rendered:
            warnings.append("no command could be built from this question")
    rendered = [{"n": i, **step} for i, step in enumerate(rendered, 1)]
    level = "high" if high else "low"
    steps, suggested = (rendered, []) if high else ([], rendered)
    if not high:
        read = [*read, ROUTING_TABLE]

    executed, rows, rows_total, truncated = None, [], 0, False
    if execute:
        executed, rows, rows_total, truncated = _execute(chosen_view, selected, high, steps, built.raws, warnings)

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
        "view_match": built.view_match,
        "entities": entities,
        "catalogue": {d: {"source": sources.get(d), "built_at": cats[d].built_at, "names": len(cats[d].names),
                          "truncated": cats[d].truncated} for d in sorted(cats)},
        "store": store,
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
