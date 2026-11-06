"""`artmind query plan`: one deterministic plan for a question (spec
docs/superpowers/specs/2026-10-22-query-plan-design.md §3.2, §4.2).

Stages: candidates (code) → classifier call 1 (domain?, view?, route) →
classifier call 2 (the chosen view's entity/enum params) → fill → assemble.
Read-only. `execute=True` only ever runs `views run`, and only for a
high-confidence view match with one parameter combination.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from loguru import logger

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import ViewError, jsonable
from artmind.kg_views.store import domain_covered
from artmind.query_plan import candidates, catalogue, fill, intents, slots as slots_mod
from artmind.query_plan import config as plan_config
from artmind.query_plan import program as program_mod
from artmind.query_plan import routes as routes_mod
from artmind.query_plan.classify import ClassifierUnavailable, Decision, LexicalClassifier, select_classifier
from artmind.query_plan.classify import lexical

PLAN_VERSION = 2
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


#: The parts a plan reads: the four it always has, plus `stores` and `assets` for the option line.
#: Never `health`: a plan does not wait for it, nor ask for it (spec 2026-11-02 §4).
PLAN_PARTS = (*catalogue.DEFAULT_PARTS, "stores", "assets")


def _get_catalogue(cover: list[str]):
    try:
        return catalogue.get(cover, PLAN_PARTS)
    except Exception as exc:  # the option line is optional: plan from the four parts it always had
        logger.warning("plan: catalogue stores/assets unavailable: {}", exc)
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


def _used_entities(entities: list[dict]):
    """(primary, second): the entities a template's `{entity_id}` / `{other_entity_id}` come from.
    The primary entity is the first named mention, resolved or not. The narrator is the second entity
    beside exactly one named mention, and the id fallback when nobody is named."""
    # A name the question spells out (capitalised, quoted) outranks one the gazetteer found.
    named = sorted((e for e in entities if e["via"] != "first_person"), key=lambda e: e["via"] == "catalogue")
    narrator = next((e for e in entities if e["via"] == "first_person" and e["candidates"]), None)
    primary = named[0] if named else narrator
    second = named[1] if len(named) > 1 else (narrator if len(named) == 1 else None)
    return named, primary, second


def _context(selected: list[str], question: str, entities: list[dict], document: str | None,
             mentions: list, overview: list[dict] | None = None, cats: list | None = None) -> dict:
    """Placeholder values. The narrator never supplies a name, since its name is no search term."""
    named, primary, second = _used_entities(entities)
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


# A candidate whose whole name is the mention's text outweighs other name matches ("Placeb" over
# "Placeb Airport"); two such groups (not twins of each other) split the weight as before.
EXACT_NAME_WEIGHT = 10.0
# Lexical path only: a view scoring at least this, whose parameters all fill from name evidence and the
# question, is trusted as if it had reached the threshold (D5b). Below it the view is not tried at all.
LEXICAL_VIEW_FLOOR = 0.5


def _name_key(text) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _exact_name(ref: dict, mention: str | None) -> bool:
    return bool(mention) and _name_key(ref.get("name")) == _name_key(mention)


def _group_weight(group: list[dict], mention: str | None = None) -> float:
    if any(_exact_name(r, mention) for r in group):
        return EXACT_NAME_WEIGHT
    return 1.0 if any(NAME_EVIDENCE & set(r.get("matched_by") or ()) for r in group) else FULLTEXT_WEIGHT


def _names_all_evidenced(view_match: dict, entities: list[dict]) -> bool:
    """Every entity parameter was filled from a candidate with name evidence (exact name, catalogue,
    narrator), never from a fulltext neighbour alone."""
    mention_of = {ref["_id"]: e["mention"] for e in entities for ref in e["candidates"]}
    for entry in view_match["params"].values():
        if entry.get("type") != "entity":
            continue
        refs = [entry["chosen"]] if entry.get("chosen") else (entry["candidates"] if entry.get("twins") else [])
        if not refs:
            return False
        if not any(NAME_EVIDENCE & set(r.get("matched_by") or ()) or _exact_name(r, mention_of.get(r["_id"]))
                   for r in refs):
            return False
    return True


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
                    lexical_scores={k: _group_weight(g, mention_of.get(g[0]["_id"], (None,))[0])
                                    for k, g in groups.items()},
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


def _chunks_only(selected: list[str], overview: list[dict]) -> bool:
    """Every overview row the selection covers is a chunks-only domain
    (`graph_extraction: false`): it has documents to search and no graph."""
    rows = [r for r in overview
            if any(r.get("domain") == d or str(r.get("domain")).startswith(d + ".") for d in selected)]
    return bool(selected) and bool(rows) and all(r.get("graph_extraction") is False for r in rows)


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


# ── v2: slots, programs and the non-retrieval intents (spec 2026-11-05 §3, §4, §5) ─────────────

RETRIEVING = frozenset({"retrieve", "follow_up_why"})
ACTIONS = {
    "curate": {"skill": "artmind-curate", "draft": None},
    "update": {"skill": "artmind-update", "draft": None},
    "ops": {"skill": "artmind-ingestion-helper", "draft": None},
}  # P3 fills the drafts, with the command's --dryRun effect
ENTITY_PLACEHOLDERS = frozenset({"entity_id", "entity_name", "other_entity_id", "entity_class", "other_entity_class"})


def _plain(value):
    """A slot value as JSON: dataclasses (Scope) and tuples become dicts and lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def _slot_json(slot) -> dict:
    return {"value": _plain(slot.value), "source": slot.source, "quality": slot.quality, "note": slot.note}


def _page_json(page) -> dict:
    return {"until": page.until, "k": page.k, "from": page.from_}


def _node_json(node, context: dict) -> dict:
    out: dict = {"id": node.id, "kind": node.kind}
    if node.run is not None:
        rendered = routes_mod.render(node.run, context)
        out.update(run=rendered or node.run, filled=rendered is not None)
    if node.children:
        out["children"] = [_node_json(c, context) for c in node.children]
    if node.map is not None:
        out["map"] = {"over": node.map.over, "as": node.map.as_, "max": node.map.max,
                      "do": _node_json(node.map.do, context)}
    if node.pick is not None:
        out["pick"] = {"from": node.pick.from_, "how": node.pick.how, "score": [p.text for p in node.pick.score]}
    if node.page is not None:
        out["page"] = _page_json(node.page)
    if node.when is not None:
        out["when"] = node.when.text
    return out


def _guard_json(guard) -> dict:
    then = guard.then
    action: dict = {"kind": then.kind}
    if then.route:
        action["route"] = then.route
    if then.slots:
        action["slots"] = dict(then.slots)
    if then.page is not None:
        action["page"] = _page_json(then.page)
    if then.run:
        action["run"] = then.run
    return {"on": guard.on, "if": guard.when.text, "then": action}


def _status_action(previous) -> dict:
    job = previous.open_job if previous is not None else None
    draft = f"artmind ingest job-status {shlex.quote(job)} --compact" if job else "artmind ingest jobs --compact"
    return {"skill": None, "draft": draft}


def _flatten_exclude(values) -> list[str]:
    out: list[str] = []
    for value in values or ():
        out += [p.strip() for p in str(value).split(",") if p.strip()]
    return list(dict.fromkeys(out))


def _class_table(cats: list | None, overview: list[dict], selected: list[str]) -> dict[str, str]:
    """class name -> description of the selected domains (the overview's top classes carry no description)."""
    table: dict[str, str] = {}
    if cats:
        for cat in cats:
            for name, _count, *rest in cat.classes:
                table.setdefault(name, (rest[0] if rest else "") or "")
        return table
    for row in overview:
        if any(row.get("domain") == d or str(row.get("domain")).startswith(d + ".") for d in selected):
            for name in row.get("top_classes") or []:
                if isinstance(name, str) and name.strip():
                    table.setdefault(name, "")
    return table


def _twins(entities: list[dict], index: dict | None, cfg, warnings: list[str]) -> list[dict]:
    """Fill `entities[i].twins` and return the identity gaps. Reads only what the plan already holds
    (resolved candidates and the catalogue name index): spouse links and open same-as proposals are a P2b
    input (`spouse_of` and `open_same_as` stay None here)."""
    try:
        kin = slots_mod.kin_groups(cfg)
    except Exception as exc:  # the kinship file is optional context
        warnings.append(f"kinship groups unavailable: {exc}")
        kin = []
    pool = [ref for e in entities for ref in e["candidates"]]
    by_class: dict[str, list[dict]] = {}
    gaps: list[dict] = []
    seen_groups: set[frozenset] = set()
    for entity in entities:
        entity["twins"] = []
        if not entity["candidates"] or entity["via"] == "previous":
            continue
        top = entity["candidates"][0]
        cls = str(top.get("entity_class") or "").casefold()
        if cls not in by_class:
            by_class[cls] = [candidates.catalogue_ref(n) for group in (index or {}).values() for n in group
                             if str(n.entity_class or "").casefold() == cls]
        found = slots_mod.twins_for(top, [*pool, *by_class[cls]], kin, spouse_of=None, open_same_as=None)
        entity["twins"] = found
        if not found:
            continue
        ids = frozenset([top["_id"], *(t["_id"] for t in found)])
        if ids in seen_groups:
            continue
        seen_groups.add(ids)
        warnings.append(f"twins: {top.get('name')!r} ({top.get('entity_class')}) may be the same as "
                        f"{len(found)} other node(s) ({', '.join(sorted({t['why'] for t in found}))}); "
                        "this plan does not union them")
        gaps.append({"kind": "identity", "why": "twins", "entities": [top, *found]})
    return gaps


def _name_evidence(group: list[dict]) -> bool:
    return any(NAME_EVIDENCE & set(r.get("matched_by") or ()) for r in group)


def _fuzzy_entity(entities: list[dict], built, route_obj) -> bool:
    """A template's entity slot took the top candidate of a group with no exact, catalogue or narrator
    evidence (a fulltext neighbour). Entities taken from the previous turn are never fuzzy."""
    if built.view_match is not None:
        for entry in built.view_match["params"].values():
            if entry.get("type") == "entity" and entry.get("via") == "mention":
                group = [entry["chosen"]] if entry.get("chosen") else entry.get("candidates") or []
                if group and not _name_evidence(group):
                    return True
        return False
    if route_obj is None:
        return False
    used: set[str] = set()
    for node in program_mod.run_nodes(route_obj.program):
        used |= routes_mod.placeholders(node.run or "")
    used &= ENTITY_PLACEHOLDERS
    if not used:
        return False
    _named, primary, second = _used_entities(entities)
    picked = [primary] + ([second] if used & {"other_entity_id", "other_entity_class"} else [])
    return any(e is not None and e["via"] not in ("first_person", "previous") and e["candidates"]
               and not _name_evidence(e["candidates"]) for e in picked)


def plan(question: str, domains: list[str] | None = None, *, execute: bool = False,
         classifier: str = "auto", today: date | None = None, model: str | None = None,
         decision_timeout: float | None = None, previous: intents.Previous | None = None,
         exclude: list[str] | tuple[str, ...] = ()) -> dict:
    """`model` overrides the decision model (else the domain config's, else ARTMIND_DECISION_MODEL);
    `decision_timeout` overrides ARTMIND_DECISION_TIMEOUT. `previous` is the previous turn (spec §3.2);
    `exclude` names documents (ids or names) left out of the scope."""
    user_question = (question or "").strip()
    if not user_question:
        raise PlanError("the question is empty")
    today = today or date.today()
    warnings: list[str] = []
    all_routes = routes_mod.load_routes()

    # A context-only reply ("yes") that inherits the previous turn plans the question it answers.
    early = intents.decide_intent(user_question, previous, None)
    rule_based = early.source != "lexical"
    question = user_question
    if rule_based and early.previous_used and early.source != "status_open_job" and previous is not None \
            and previous.question and previous.question.strip():
        question = previous.question.strip()
    effective_question = question if question != user_question else None
    exclude = _flatten_exclude(exclude)

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
            model or (given_cfg.decision_model if given_cfg else None), timeout=decision_timeout,
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

    # ── classifier call 1: domain?, view?, intent?, route ───────────────────
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
    if not rule_based:  # the previous turn's rules settled the intent: nothing to ask
        decisions.append(intents.intent_decision(user_question, previous))
    decisions.append(Decision(
        name="route", instructions="What kind of question is this?",
        options={r.id: r.description for r in all_routes},
        lexical_scores=lexical.route_scores(all_routes, facts),
        default="search", default_confidence=0.5, none_mass=0.5,
    ))
    answers = calls.decide(question, decisions)
    intent_result = early if rule_based else intents.decide_intent(user_question, previous, answers.get("intent"))
    intent = intent_result.intent
    retrieving = intent in RETRIEVING

    selected, source, domain_conf, domain_candidates = _choose_domains(domains, options, overview, answers)
    cfg = given_cfg or _configs(selected)
    threshold = cfg.confidence_threshold
    chunks_only = _chunks_only(selected, overview)
    if chunks_only and retrieving:
        warnings.append(
            f"{', '.join(selected)} builds no graph (graph_extraction: false): planned as a search of the notes or a survey of its documents"
        )

    route_answer = answers["route"]
    view_answer = answers.get("view")
    lexical_used = clf.backend == "lexical" or calls.fell_back
    slot_map: dict[str, slots_mod.Slot] = {}
    gaps: list[dict] = []
    entities: list[dict] = []
    stages: list[tuple[str, float]] = []
    built = _Steps()
    store = None
    route_obj = None
    chosen_view = None
    rendered: list[dict] = []
    fallbacks: list[dict] = []
    read: list[str] = []
    high = False
    score = 0.0
    weakest = "intent"
    slot_quality_block: dict | None = None
    context: dict = {}
    steps: list[dict] = []
    suggested: list[dict] = []
    action: dict | None = None
    answer_from: str | None = None
    program_json: list[dict] = []
    guards_json: list[dict] = []
    route_id, route_conf = route_answer.choice, route_answer.confidence

    def selected_scope(route_for_scope: str | None):
        return slots_mod.scope_slot(list(domains or ()), selected, domain_conf or 0.0, threshold, cfg, question,
                                    route_for_scope, exclude)

    if not retrieving:
        # No retrieval: no entity resolution, no view, no steps. The route is still recorded (spec §3.3).
        route_obj = next((r for r in all_routes if r.id == route_id), None)
        stages = [("intent", intent_result.confidence)]
        weakest, score = stages[0]
        high = score >= threshold
        if intent in ACTIONS:
            action = dict(ACTIONS[intent])
        elif intent == "status":
            action = _status_action(previous)
        elif intent == "follow_up_format":
            answer_from = "previous"
        slot_map["scope"] = selected_scope(route_id)
        read = [] if high else [ROUTING_TABLE]
        if execute:
            warnings.append(f"--execute ignored: intent {intent} plans no retrieval")
    else:
        selected_cats = _selected_cats(cats, selected)
        if selected_cats is not None and {c.domain for c in selected_cats} == {c.domain for c in cover_cats}:
            selected_index = all_index
        else:
            names = _selected_names(cats, selected)
            selected_index = candidates.name_index(names) if names is not None else None
        entities = _resolve_entities(mentions, selected, cfg, warnings, selected_index)
        kept = {e["span"][0] for e in entities}
        kept_mentions = [m for m in mentions if m.start in kept]

        # A follow-up with a pronoun and no named mention is about the previous turn's subjects (§4.6).
        if previous is not None and intent == "retrieve" and intents.uses_previous_subjects(question, named_mentions):
            subjects = slots_mod.follow_up_subjects(
                [{"id": e.id, "name": e.name} for e in previous.entities], previous.documents)
            for subject in subjects:
                if subject.kind == "entity":
                    entities.append({"mention": subject.text, "span": [-1, -1], "via": "previous", "candidates": [
                        {"_id": subject.ref["id"], "name": subject.text, "entity_class": None,
                         "matched_by": ["previous"]}]})
                elif document is None:
                    document = subject.text
            if subjects:
                slot_map["previous_subjects"] = slots_mod.Slot(
                    "previous_subjects", [{"text": s.text, "kind": s.kind} for s in subjects], "previous", 1.0)

        # ── slots (spec §4) ─────────────────────────────────────────────────
        narrator = slots_mod.narrator_slot(mentions, cfg)
        if narrator is not None:
            narrator_id = next((r["_id"] for e in entities for r in e["candidates"]
                                if "narrator" in (r.get("matched_by") or ())), None)
            slot_map["narrator"] = dataclasses.replace(narrator, value={**narrator.value, "id": narrator_id})
            gaps += slots_mod.narrator_stub_gaps(entities, cfg, narrator_id)
        expansion = slots_mod.category_expansion(question, _class_table(selected_cats, overview, selected), cfg)
        if expansion is not None:
            slot_map["category"] = slots_mod.Slot(
                "category", {"word": expansion.word, "classes": list(expansion.classes),
                             "terms": list(expansion.terms), "narrowed": expansion.narrowed},
                expansion.source, 1.0)
        search_term = slots_mod.search_term_slot(
            question, kept_mentions, [*(expansion.terms if expansion else ()), *(expansion.classes if expansion else ())])
        slot_map["search_term"] = search_term
        window = slots_mod.date_window_slot(question, today, cfg)
        if window is not None:
            slot_map["date_window"] = window
        if intent == "follow_up_why":
            slot_map["must_find"] = slots_mod.Slot(
                "must_find", [m.text for m in mentions if m.via != "first_person"], "mention", 1.0)
        gaps += _twins(entities, selected_index, cfg, warnings)

        # ── view match (classifier call 2) or route ─────────────────────────
        forced = previous.route if (intent == "follow_up_why" and previous is not None) else None
        forced_route = next((r for r in all_routes if r.id == forced), None)
        if (not chunks_only and forced_route is None and view_answer is not None and view_answer.choice != NO_VIEW
                and view_answer.confidence >= threshold):
            loaded = views_by_name[view_answer.choice]
            if selected and all(domain_covered(d, loaded.spec.domains) for d in selected):
                chosen_view = loaded
            else:
                warnings.append(f"view {loaded.spec.name} matched but does not cover {selected}; not used")
        lift_view = None
        if (lexical_used and not chunks_only and chosen_view is None and forced_route is None
                and view_answer is not None and view_answer.choice != NO_VIEW
                and LEXICAL_VIEW_FLOOR <= view_answer.confidence < threshold):
            loaded = views_by_name[view_answer.choice]
            if selected and all(domain_covered(d, loaded.spec.domains) for d in selected):
                lift_view = loaded
        resolved_classes = {ref.get("entity_class") for e in entities for ref in e["candidates"]}
        if selected:
            try:
                store = candidates.store_facts(selected, question, resolved_classes)
            except Exception as exc:  # the structured store is optional context (spec §7)
                warnings.append(f"store facts unavailable: {exc}")
        context = _context(selected, question, entities, document, kept_mentions, overview, selected_cats)
        context.update({
            "search_term": search_term.value or question,
            "date_window": f"{window.value['from']}..{window.value['to']}" if window else None,
            "entity_class_list": ",".join(expansion.classes) if expansion and expansion.classes else None,
        })
        if source != "given":
            stages.append(("domain", domain_conf if source != "all" else 0.0))
        lifted_built = None
        if lift_view is not None:
            trial = _view_steps(lift_view, view_answer, entities, question, cfg, threshold, today, calls, selected,
                                context)
            if trial.status == "ok" and trial.view_match and _names_all_evidenced(trial.view_match, entities):
                trial.view_match["confidence_lifted"] = {
                    "from": view_answer.confidence, "to": threshold,
                    "reason": "lexical: every parameter filled from name evidence or the question"}
                trial.view_match["confidence"] = threshold
                lifted_built, chosen_view = trial, lift_view
        view_conf = threshold if lifted_built is not None else (view_answer.confidence if view_answer else 0.0)
        if view_answer is not None and not chunks_only:
            stages.append(("view", view_conf))
        if chosen_view is not None:
            built = lifted_built or _view_steps(chosen_view, view_answer, entities, question, cfg, threshold, today,
                                                calls, selected, context)
            route_id, route_conf = "view", view_conf
        else:
            # A chunks-only scope has no graph routes; it still tells a survey from a search.
            picked = forced_route.id if forced_route else route_answer.choice
            if chunks_only:
                picked = "survey" if picked == "survey" else "search"
            route_obj = next(r for r in all_routes if r.id == picked)
            route_id = route_obj.id
            route_conf = 1.0 if (chunks_only or forced_route) else route_answer.confidence
            built = _route_steps(route_obj, route_conf, context, (store or {}).get("kind"))
        stages += built.stages
        rendered, fallbacks, read = built.steps, built.fallbacks, built.read

        slot_map["scope"] = selected_scope(route_id)
        view_twins = bool(built.view_match and built.view_match.get("twins"))
        twins_found = any(e.get("twins") for e in entities)
        sq_score, sq_info = slots_mod.slot_quality(
            slot_map, twins_found=twins_found, twins_unioned=view_twins,
            entity_from_fuzzy=_fuzzy_entity(entities, built, route_obj),
            search_term_empty=search_term.value == "", all_domains=slot_map["scope"].source == "all")
        stages.append(("slot_quality", sq_score))
        slot_quality_block = {"score": sq_score, **sq_info}

        # ── confidence ─────────────────────────────────────────────────────
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
        steps, suggested = (rendered, []) if high else ([], rendered)
        if not high:
            read = [*read, ROUTING_TABLE]
        if chosen_view is not None:
            program_json = [{"id": f"v{i}", "kind": "run", "run": step["cmd"], "filled": True}
                            for i, step in enumerate(built.steps, 1)]
        elif route_obj is not None:
            program_json = [_node_json(n, context) for n in route_obj.program]
            guards_json = [_guard_json(g) for g in route_obj.guards]

    level = "high" if high else "low"
    executed, rows, rows_total, truncated = None, [], 0, False
    if execute and retrieving:
        executed, rows, rows_total, truncated = _execute(chosen_view, selected, high, steps, built.raws, warnings)

    scope_slot = slot_map["scope"]
    return {
        "plan_version": PLAN_VERSION,
        "question": user_question,
        "effective_question": effective_question,
        "previous_used": intent_result.previous_used,
        "intent": {"id": intent, "confidence": intent_result.confidence, "source": intent_result.source,
                   "inherited_offer": intent_result.inherited_offer,
                   "alternatives": [{"id": k, "p": p} for k, p in intent_result.alternatives]},
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
        "slots": {name: _slot_json(slot) for name, slot in slot_map.items()},
        "slot_quality": slot_quality_block,
        "scope": _plain(scope_slot.value),
        "confidence": {"level": level, "score": round(score, 4), "threshold": threshold, "weakest": weakest},
        "program": program_json,
        "guards": guards_json,
        "asset_match": None,  # P2b/P3
        "gaps": [_plain(g) for g in gaps],
        "action": action,
        "answer_from": answer_from,
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
