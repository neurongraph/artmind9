"""Per-domain plan config: <ARTMIND_HOME>/domains/plan/<domain>.yaml (spec 2026-10-22 §4.5).

User data like views and insights tuning: never seeded, outside schemas/ so it
never moves schema_set_hash(), read on every call. A parent domain's file
applies to every child domain that has no file of its own (most specific wins,
never merged).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FIRST_PERSON = ("i", "me", "my", "mine", "myself")
DEFAULT_THRESHOLD = 0.7
CLASSIFIERS = ("auto", "lexical", "decision")
_KEYS = frozenset({"narrator", "first_person", "confidence_threshold", "classifier", "decision_model",
                   "categories", "kinship", "scope_rules", "calendar"})
_RULE_ID_RE = re.compile(r"[a-z][a-z0-9_]*")
_MONTH_DAY_RE = re.compile(r"(\d{2})-(\d{2})")
_WHEN_KEYS = ("mention", "not_cue", "route")
_ACTION_KEYS = ("set", "exclude_classes", "require_participant")
_RULE_KEYS = frozenset({"id", "when", *_ACTION_KEYS})
_CALENDAR_KEYS = frozenset({"name", "month_day", "span_days", "recurring", "year"})
_RECURRING = ("yearly", "once")
_DOMAIN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*")
_CLASS_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


class PlanConfigError(ValueError):
    """A plan config file breaks a rule of spec §4.5."""


@dataclass(frozen=True)
class Narrator:
    entity_class: str
    name: str
    aliases: tuple[str, ...] = ()          # stripped, original case kept, deduped casefold


@dataclass(frozen=True)
class ScopeRule:
    id: str                                 # snake_case, unique per file
    when_mention: str | None = None         # casefolded word, e.g. "we"
    when_not_cue: tuple[str, ...] = ()      # casefolded words; rule applies when NONE appear
    when_route: str | None = None           # a route id
    set_participants: str | None = None     # from `set: {participants: <value>}`
    exclude_classes: tuple[str, ...] = ()   # normalised like narrator entity_class
    require_participant: str | None = None  # "narrator" or an entity name


@dataclass(frozen=True)
class CalendarEvent:
    name: str
    month_day: str                          # "MM-DD", validated as a real date (02-29 allowed)
    span_days: int = 1                      # 1..60
    recurring: str = "yearly"               # "yearly" | "once"
    year: int | None = None                 # required when recurring == "once", forbidden otherwise


def _forms(word: str) -> list[str]:
    w = word.strip().casefold()
    forms = [w]
    if w.endswith("ies") and len(w) > 3:
        forms.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 2:
        forms.append(w[:-2])
    if w.endswith("s") and len(w) > 1:
        forms.append(w[:-1])
    forms += [w + "s", w + "es"]
    if w.endswith("y") and len(w) > 1:
        forms.append(w[:-1] + "ies")
    return forms


def _lookup(table: tuple[tuple[str, tuple[str, ...]], ...], word: str) -> tuple[str, ...] | None:
    by_key = dict(table)
    for form in _forms(word):
        if form in by_key:
            return by_key[form]
    return None


@dataclass(frozen=True)
class PlanConfig:
    narrator: Narrator | None = None
    first_person: tuple[str, ...] = DEFAULT_FIRST_PERSON
    confidence_threshold: float = DEFAULT_THRESHOLD
    classifier: str = "auto"
    decision_model: str | None = None
    source: str | None = None
    categories: tuple[tuple[str, tuple[str, ...]], ...] = ()   # (casefolded class word, members)
    kinship: tuple[tuple[str, tuple[str, ...]], ...] = ()      # (casefolded kin word, variants casefolded)
    scope_rules: tuple[ScopeRule, ...] = ()
    calendar: tuple[CalendarEvent, ...] = ()

    def category(self, word: str) -> tuple[str, ...] | None:
        """Members of the class word (casefold; singular/plural by a trailing s/es), else None."""
        return _lookup(self.categories, word)

    def kin(self, word: str) -> tuple[str, ...] | None:
        return _lookup(self.kinship, word)


def is_domain_name(name) -> bool:
    """True for a dotted domain name whose whole string matches (no trailing newline, no path parts)."""
    return isinstance(name, str) and _DOMAIN_RE.fullmatch(name) is not None


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_PLAN_DIR)


def config_file(domain: str) -> Path | None:
    """The most specific file for `domain`: its own, else its nearest parent's."""
    if not is_domain_name(domain):
        return None
    parts = domain.split(".")
    for i in range(len(parts), 0, -1):
        path = config_dir() / f"{'.'.join(parts[:i])}.yaml"
        if path.is_file():
            return path
    return None


def _entity_class(raw: str, source: str) -> str:
    # Same normalisation ingestion applies to entity classes (kept local: no graph_query import).
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip()).upper()
    if not _CLASS_RE.fullmatch(name) or not re.search(r"[A-Z]", name):
        raise PlanConfigError(f"{source}: narrator entity_class {raw!r} is not a usable class name")
    return name


def _string_list(raw, source: str, what: str, *, allow_empty: bool = False) -> list[str]:
    if (not isinstance(raw, list) or (not raw and not allow_empty)
            or not all(isinstance(w, str) and w.strip() for w in raw)):
        raise PlanConfigError(f"{source}: {what} must be a{'' if allow_empty else ' non-empty'} list of non-empty strings")
    return [w.strip() for w in raw]


def _unique(items) -> tuple[str, ...]:
    seen, out = set(), []
    for item in items:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return tuple(out)


def _parse_word_map(raw, source: str, key: str, *, fold_members: bool):
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise PlanConfigError(f"{source}: {key} must be a mapping of word to a list of strings")
    out: dict[str, tuple[str, ...]] = {}
    for word, members in raw.items():
        if not isinstance(word, str) or not word.strip():
            raise PlanConfigError(f"{source}: {key} words must be non-empty strings")
        folded = word.strip().casefold()
        if folded in out:
            raise PlanConfigError(f"{source}: {key} lists a word twice (ignoring case)")
        items = _string_list(members, source, f"{key} entry {folded!r}")
        out[folded] = _unique([m.casefold() for m in items] if fold_members else items)
    return tuple(out.items())


def _parse_scope_rules(raw, source: str) -> tuple[ScopeRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PlanConfigError(f"{source}: scope_rules must be a list of rules")
    rules, ids = [], set()
    for i, rule in enumerate(raw):
        where = f"scope_rules[{i}]"
        if not isinstance(rule, dict):
            raise PlanConfigError(f"{source}: {where} must be a mapping")
        bad = set(rule) - _RULE_KEYS
        if bad:
            raise PlanConfigError(f"{source}: {where} has unknown key(s) {sorted(map(str, bad))}; "
                                  f"allowed: id, when, {', '.join(_ACTION_KEYS)}")
        rid = rule.get("id")
        if not isinstance(rid, str) or not _RULE_ID_RE.fullmatch(rid):
            raise PlanConfigError(f"{source}: {where} id must be snake_case (lowercase letters, digits, underscores)")
        if rid in ids:
            raise PlanConfigError(f"{source}: scope_rules id {rid!r} is used twice")
        ids.add(rid)
        where = f"scope rule {rid!r}"
        when = rule.get("when")
        if not isinstance(when, dict) or not when or set(when) - set(_WHEN_KEYS):
            raise PlanConfigError(f"{source}: {where} needs when: with at least one of {list(_WHEN_KEYS)} and no other key")
        mention = route = None
        not_cue: tuple[str, ...] = ()
        if "mention" in when:
            if not isinstance(when["mention"], str) or not when["mention"].strip():
                raise PlanConfigError(f"{source}: {where} when.mention must be a non-empty string")
            mention = when["mention"].strip().casefold()
        if "not_cue" in when:
            not_cue = _unique([w.casefold() for w in _string_list(when["not_cue"], source, f"{where} when.not_cue")])
        if "route" in when:
            if not isinstance(when["route"], str) or not when["route"].strip():
                raise PlanConfigError(f"{source}: {where} when.route must be a non-empty string")
            route = when["route"].strip()
        if not any(k in rule for k in _ACTION_KEYS):
            raise PlanConfigError(f"{source}: {where} needs an action: one of {list(_ACTION_KEYS)}")
        participants = None
        if "set" in rule:
            sets = rule["set"]
            if (not isinstance(sets, dict) or set(sets) != {"participants"}
                    or not isinstance(sets["participants"], str) or not sets["participants"].strip()):
                raise PlanConfigError(f"{source}: {where} set must be {{participants: <string>}}")
            participants = sets["participants"].strip()
        classes: tuple[str, ...] = ()
        if "exclude_classes" in rule:
            classes = _unique([_entity_class(c, source) for c in _string_list(rule["exclude_classes"], source,
                                                                              f"{where} exclude_classes")])
        required = None
        if "require_participant" in rule:
            val = rule["require_participant"]
            if not isinstance(val, str) or not val.strip():
                raise PlanConfigError(f"{source}: {where} require_participant must be a non-empty string")
            required = val.strip()
        rules.append(ScopeRule(rid, mention, not_cue, route, participants, classes, required))
    return tuple(rules)


def _parse_calendar(raw, source: str) -> tuple[CalendarEvent, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PlanConfigError(f"{source}: calendar must be a list of events")
    events = []
    for i, ev in enumerate(raw):
        where = f"calendar[{i}]"
        if not isinstance(ev, dict):
            raise PlanConfigError(f"{source}: {where} must be a mapping")
        if set(ev) - _CALENDAR_KEYS or "name" not in ev or "month_day" not in ev:
            raise PlanConfigError(f"{source}: {where} must have name and month_day, and only these keys: "
                                  f"{sorted(_CALENDAR_KEYS)}")
        if not isinstance(ev["name"], str) or not ev["name"].strip():
            raise PlanConfigError(f"{source}: {where} name must be a non-empty string")
        md = ev["month_day"]
        m = _MONTH_DAY_RE.fullmatch(md) if isinstance(md, str) else None
        try:
            if not m:
                raise ValueError
            datetime.date(2000, int(m.group(1)), int(m.group(2)))  # 2000 is a leap year: 02-29 is allowed
        except ValueError:
            raise PlanConfigError(f"{source}: {where} month_day must be a real date written MM-DD") from None
        span = ev.get("span_days", 1)
        if isinstance(span, bool) or not isinstance(span, int) or not 1 <= span <= 60:
            raise PlanConfigError(f"{source}: {where} span_days must be an integer from 1 to 60")
        recurring = ev.get("recurring", "yearly")
        if recurring not in _RECURRING:
            raise PlanConfigError(f"{source}: {where} recurring must be one of {list(_RECURRING)}")
        year = ev.get("year")
        if recurring == "once":
            if isinstance(year, bool) or not isinstance(year, int) or not 1000 <= year <= 9999:
                raise PlanConfigError(f"{source}: {where} year is required (four digits) when recurring is once")
        elif year is not None:
            raise PlanConfigError(f"{source}: {where} year is only allowed when recurring is once")
        events.append(CalendarEvent(ev["name"].strip(), md, span, recurring, year))
    return tuple(events)


def parse_config(data, source: str) -> PlanConfig:
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise PlanConfigError(f"{source}: must be a mapping")
    unknown = set(data) - _KEYS
    if unknown:
        raise PlanConfigError(f"{source}: unknown key(s) {sorted(unknown)}")
    narrator = None
    if data.get("narrator") is not None:
        raw = data["narrator"]
        if (not isinstance(raw, dict) or not {"entity_class", "name"} <= set(raw) <= {"entity_class", "name", "aliases"}
                or not all(isinstance(raw[k], str) and raw[k].strip() for k in raw if k != "aliases")):
            raise PlanConfigError(f"{source}: narrator must be {{entity_class, name, aliases?}} (strings; aliases a list)")
        aliases = _unique(_string_list(raw.get("aliases", []), source, "narrator aliases", allow_empty=True))
        narrator = Narrator(_entity_class(raw["entity_class"], source), raw["name"].strip(), aliases)
    first_person = DEFAULT_FIRST_PERSON
    if "first_person" in data:
        raw = data["first_person"]
        if not isinstance(raw, list) or not raw or not all(isinstance(w, str) and w.strip() for w in raw):
            raise PlanConfigError(f"{source}: first_person must be a non-empty list of words")
        first_person = tuple(w.strip().casefold() for w in raw)
    threshold = data.get("confidence_threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise PlanConfigError(f"{source}: confidence_threshold must be a number in (0, 1]")
    classifier = data.get("classifier", "auto")
    if classifier not in CLASSIFIERS:
        raise PlanConfigError(f"{source}: classifier must be one of {list(CLASSIFIERS)}")
    model = data.get("decision_model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise PlanConfigError(f"{source}: decision_model must be a non-empty string")
    return PlanConfig(narrator, first_person, float(threshold), classifier, model.strip() if model else None, source,
                      _parse_word_map(data.get("categories"), source, "categories", fold_members=False),
                      _parse_word_map(data.get("kinship"), source, "kinship", fold_members=True),
                      _parse_scope_rules(data.get("scope_rules"), source),
                      _parse_calendar(data.get("calendar"), source))


def load_config(domain: str) -> PlanConfig:
    path = config_file(domain)
    if path is None:
        return PlanConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PlanConfigError(f"{path} cannot be read: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PlanConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(data, str(path))


def merge(configs: list[PlanConfig]) -> PlanConfig:
    """Several selected domains: the most cautious threshold, the first narrator,
    first-person list and model found, and the classifier only if they agree."""
    if not configs:
        return PlanConfig()
    if len(configs) == 1:
        return configs[0]
    classifiers = {c.classifier for c in configs}
    rules: dict[str, ScopeRule] = {}
    for c in configs:
        for r in c.scope_rules:
            rules.setdefault(r.id, r)
    return PlanConfig(
        categories=_merge_maps(c.categories for c in configs),
        kinship=_merge_maps(c.kinship for c in configs),
        scope_rules=tuple(rules.values()),
        calendar=tuple(e for c in configs for e in c.calendar),
        narrator=next((c.narrator for c in configs if c.narrator), None),
        first_person=next((c.first_person for c in configs if c.first_person != DEFAULT_FIRST_PERSON),
                          DEFAULT_FIRST_PERSON),
        confidence_threshold=max(c.confidence_threshold for c in configs),
        classifier=classifiers.pop() if len(classifiers) == 1 else "auto",
        decision_model=next((c.decision_model for c in configs if c.decision_model), None),
    )


def _merge_maps(maps) -> tuple[tuple[str, tuple[str, ...]], ...]:
    out: dict[str, list[str]] = {}
    for table in maps:
        for word, members in table:
            out.setdefault(word, []).extend(members)
    return tuple((w, _unique(m)) for w, m in out.items())
