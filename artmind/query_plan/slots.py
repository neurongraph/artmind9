"""Slot fillers for planner v2 (spec 2026-11-05-planner-v2-design.md §4).

Pure functions: no Neo4j, no LLM, no clock. The planner passes in what it already has
(question, mentions, resolved entity refs, catalogue classes, plan config, today) and gets
back `Slot` values with a quality in [0, 1]. `slot_quality` multiplies the per-slot factors
(§4.8) into the number that joins the plan's `min(route, domain, view?, slot_quality)`.

Plan config attributes (`categories`, `kinship`, `scope_rules`, `calendar`) are read with
`getattr` so this module works against a config that predates them.
"""

from __future__ import annotations

import calendar as _calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from artmind.graph_query import sanitize_lucene_query
from artmind.query_plan.candidates import QUESTION_WORDS, TEMPORAL_WORDS, Mention
from artmind.query_plan.classify.lexical import STOPWORDS

# Spec §4.8 factors.
FUZZY_MENTION = 0.7
TWINS_SINGLE = 0.5
EMPTY_SEARCH = 0.6
ALL_DOMAINS = 0.8
# Quality of a slot taken from the user's own words / config.
FULL = 1.0

# Multi-word question filler; removed before the word-level pass.
FILLER_PHRASES = (
    "tell me about", "tell me", "show me", "give me", "how many", "how much", "was there", "were there",
    "is there", "are there", "all the", "what did", "what does", "what do", "can you", "could you",
    "please",
)
# Single words that carry no search value, beyond STOPWORDS and QUESTION_WORDS.
FILLER_WORDS = frozenset("""
list show tell any some many much more get make find give did does do what which
last this next ago year years month months week weeks today yesterday tomorrow
ever lot lots
""".split())

_WORD_RE = re.compile(r"[^\W_][\w'’&-]*")


@dataclass(frozen=True)
class Slot:
    name: str
    value: Any
    source: str
    quality: float
    note: str | None = None


def _norm(text: Any) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").casefold()).split())


def _stems(word: str) -> set[str]:
    out = {word}
    for suffix in ("ies", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            stem = word[: -len(suffix)]
            out |= {stem + "y", stem + "e", stem} if suffix == "ies" else {stem, stem + "e"}
    return out


def _has_word(question: str, word: str) -> bool:
    w = _norm(word)
    return bool(w) and re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", _norm(question)) is not None


# --------------------------------------------------------------------------- narrator (§4.1)

def _narrator_names(cfg) -> set[str]:
    narrator = getattr(cfg, "narrator", None)
    if narrator is None:
        return set()
    names = {_norm(narrator.name), *(_norm(a) for a in (getattr(narrator, "aliases", ()) or ()))}
    return {n for n in names if n}


def narrator_slot(mentions: Sequence[Mention], cfg) -> Slot | None:
    """First-person mentions, or a mention spelling the narrator's own name or an alias,
    resolve to the configured narrator. None when no narrator is configured or none is named."""
    narrator = getattr(cfg, "narrator", None)
    if narrator is None:
        return None
    value = {"entity_class": narrator.entity_class, "name": narrator.name}
    if any(m.via == "first_person" for m in mentions):
        return Slot("narrator", value, "narrator", FULL)
    names = _narrator_names(cfg)
    if any(_norm(m.text) in names for m in mentions):
        return Slot("narrator", value, "alias", FULL)
    return None


def narrator_stub_gaps(entities: Sequence[dict], cfg, narrator_id: str | None = None) -> list[dict]:
    """A resolved candidate named like the narrator (or an alias) but with another id is a stub the
    curation should merge. `entities` are the planner's resolved mentions (`{"candidates": [...]}`)
    or bare refs. With `narrator_id` every other id is a stub; without it, the first id seen is
    taken as the narrator when several share the name."""
    narrator = getattr(cfg, "narrator", None)
    if narrator is None:
        return []
    names = _narrator_names(cfg)
    found: dict[Any, dict] = {}
    for ent in entities:
        for ref in ent.get("candidates", [ent]):
            if _norm(ref.get("name")) not in names:
                continue
            cls = ref.get("entity_class")
            if cls and str(cls).casefold() != narrator.entity_class.casefold():
                continue
            if ref.get("_id") is not None:
                found.setdefault(ref["_id"], ref)
    ids = list(found)
    stubs = ids[1:] if narrator_id is None else [i for i in ids if i != narrator_id]
    if not stubs:
        return []
    return [{"kind": "identity", "why": "narrator_stub", "entities": list(found.values())}]


# --------------------------------------------------------------------------- search term (§4.2)

def _strip_filler(question: str) -> list[str]:
    text = question.casefold().replace("’", "'")
    for phrase in FILLER_PHRASES:
        text = re.sub(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", " ", text)
    out: list[str] = []
    for word in _WORD_RE.findall(text):
        word = re.sub(r"'s$", "", word.strip("'’-"))
        if len(word) < 2 or word.isdigit():   # a bare year is a date window, not a search word
            continue
        if word in STOPWORDS or word in QUESTION_WORDS or word in FILLER_WORDS or word in TEMPORAL_WORDS:
            continue
        out.append(word)
    return out


def _term(text: str) -> str:
    cleaned = sanitize_lucene_query(text)
    return f'"{cleaned}"' if " " in cleaned and not cleaned.startswith('"') else cleaned


def search_term_slot(question: str, mentions: Sequence[Mention], expansion_terms: Iterable[str] = ()) -> Slot:
    """The `{search_term}`: the user's mention spans, else the question minus filler and
    stopwords; category expansion terms are OR-ed in (space-separated: Lucene's default
    operator). Empty when nothing survives: Slot(value="", quality=EMPTY_SEARCH) and the
    step falls back to `{question}`."""
    spans = [m.text for m in mentions if m.via != "first_person" and m.text.strip()]
    if spans:
        base, source = [_term(s) for s in spans], "mention"
    else:
        base, source = [_term(w) for w in _strip_filler(question)], "filler_stripped"
    base = [t for t in base if t]
    terms: list[str] = []
    seen: set[str] = set()
    for raw in [*base, *(_term(str(t)) for t in expansion_terms if str(t).strip())]:
        key = raw.strip('"').casefold()
        if raw and key not in seen:
            seen.add(key)
            terms.append(raw)
    if not terms:
        return Slot("search_term", "", "question", EMPTY_SEARCH)
    if not base:
        source = "category"
    return Slot("search_term", " ".join(terms), source, FULL)


# --------------------------------------------------------------------------- categories (§4.3)

@dataclass(frozen=True)
class Expansion:
    word: str
    classes: tuple[str, ...]
    terms: tuple[str, ...]
    source: str            # category | class_match
    narrowed: bool = False  # true for a class match (one level), false for a category


def _class_table(catalogue_classes) -> dict[str, str]:
    """class name -> description, from a mapping, a list of names or of (name, description)."""
    if isinstance(catalogue_classes, Mapping):
        return {str(k): str(v or "") for k, v in catalogue_classes.items()}
    table: dict[str, str] = {}
    for item in catalogue_classes or ():
        if isinstance(item, str):
            table[item] = ""
        else:
            table[str(item[0])] = str(item[1]) if len(item) > 1 and item[1] else ""
    return table


def _class_key(name: str) -> str:
    return _norm(name.replace("_", " "))


def category_expansion(question: str, catalogue_classes, cfg) -> Expansion | None:
    """A class word of the question ("doctors", "illnesses") expanded to entity classes and
    plain terms. `cfg.category(word)` wins; otherwise the class whose name (case- and
    plural-insensitive) or description matches, alone. A category key always expands to all its
    members, even when one member class shares its name ("doctor: [Doctor, Dentist]"); a question
    naming a member level ("which dentists") is not a key, so it narrows through the class match."""
    table = _class_table(catalogue_classes)
    keys = {_class_key(n): n for n in table}
    words = re.findall(r"[a-z0-9]+", question.casefold())
    lookup = getattr(cfg, "category", None)
    for word in words:
        members = lookup(word) if callable(lookup) else None
        if not members:
            continue
        classes: list[str] = []
        terms: list[str] = []
        for member in members:
            hit = keys.get(_class_key(str(member)))
            if hit:
                classes.append(hit)
            else:
                terms.append(str(member))
        return Expansion(word, tuple(classes), tuple(terms), "category")
    for name in table:
        parts = _class_key(name).split()
        for i in range(len(words) - len(parts) + 1):
            if parts and all(_stems(parts[j]) & _stems(words[i + j]) for j in range(len(parts))):
                return Expansion(" ".join(words[i:i + len(parts)]), (name,), (), "class_match", narrowed=True)
    # Description match: only when exactly one class's description holds a content word.
    content = [w for w in words if len(w) > 3 and w not in STOPWORDS and w not in QUESTION_WORDS
               and w not in FILLER_WORDS and w not in TEMPORAL_WORDS]
    for word in content:
        hits = [n for n, d in table.items()
                if d and any(_stems(word) & _stems(t) for t in re.findall(r"[a-z0-9]+", d.casefold()))]
        if len(hits) == 1:
            return Expansion(word, (hits[0],), (), "class_match")
    return None


# --------------------------------------------------------------------------- twins (§4.4)

def kin_groups(cfg=None, base_groups: Iterable[Iterable[str]] | None = None) -> list[frozenset[str]]:
    """Alias groups for `twins_for`: insights/aliases.yaml (via the insights loader unless
    `base_groups` is given) plus the plan config's `kinship:` extension (key + members)."""
    if base_groups is None:
        from artmind.insights.detectors.same_as import load_alias_groups
        base_groups = load_alias_groups()
    groups = [frozenset(_norm(n) for n in g) for g in base_groups]
    for key, members in getattr(cfg, "kinship", ()) or ():
        groups.append(frozenset(_norm(n) for n in (key, *members)))
    return groups


def _spouses(spouse_of, ident) -> set:
    value = (spouse_of or {}).get(ident)
    if value is None:
        return set()
    return {value} if isinstance(value, (str, int)) else set(value)


def twins_for(entity_ref: dict, same_class_refs: Sequence[dict], kin_variants: Iterable[Iterable[str]] = (),
              spouse_of: Mapping | None = None, open_same_as: set[frozenset] | None = None) -> list[dict]:
    """Other entities of the same class that denote the same thing, each ref copied with `why`:
    `name` (casefold equality), `same_as` (an open proposal), `kinship` (one alias group),
    `shared_spouse` (first name alone vs full name, only when both link to the same spouse)."""
    me_id, me_name = entity_ref.get("_id"), _norm(entity_ref.get("name"))
    me_cls = str(entity_ref.get("entity_class") or "").casefold()
    groups = [frozenset(_norm(n) for n in g) for g in kin_variants]
    out: list[dict] = []
    seen: set = set()
    for ref in same_class_refs:
        rid = ref.get("_id")
        if rid == me_id or rid in seen:
            continue
        if str(ref.get("entity_class") or "").casefold() != me_cls:
            continue
        name = _norm(ref.get("name"))
        why = None
        if name and name == me_name:
            why = "name"
        elif open_same_as and frozenset((me_id, rid)) in open_same_as:
            why = "same_as"
        elif name and me_name and any(me_name in g and name in g for g in groups):
            why = "kinship"
        elif name and me_name:
            a, b = me_name.split(), name.split()
            short, long_ = (a, b) if len(a) < len(b) else (b, a)
            if (len(short) == 1 and len(long_) > 1 and long_[0] == short[0]
                    and _spouses(spouse_of, me_id) & _spouses(spouse_of, rid)):
                why = "shared_spouse"
        if why:
            seen.add(rid)
            out.append({**ref, "why": why})
    return out


# --------------------------------------------------------------------------- scope (§4.5)

@dataclass(frozen=True)
class Scope:
    domains: tuple[str, ...]
    exclude_documents: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    filters: dict = field(default_factory=dict)


def scope_slot(domains_given: Sequence[str], selected: Sequence[str], domain_conf: float, threshold: float,
               cfg, question: str, route_id: str | None, excluded_documents: Iterable[str] = ()) -> Slot:
    """Scope = domains + excluded documents + the config rules that apply to this question.
    Quality 1.0 when domains were given or chosen at >= threshold; 0.8 (ALL_DOMAINS, source
    `all`) when the planner fell back to searching all it handed in `selected`. A rule applies
    when every condition it states holds (`when_mention` a whole word of the question,
    `when_not_cue` none of the cues present, `when_route` equal to `route_id`)."""
    if domains_given:
        domains, source, quality = tuple(domains_given), "given", FULL
    elif selected and domain_conf >= threshold:
        domains, source, quality = tuple(selected), "chosen", FULL
    else:
        domains, source, quality = tuple(selected), "all", ALL_DOMAINS
    applied: list[str] = []
    exclude: list[str] = []
    participants: list[str] = []
    require: str | None = None
    for rule in getattr(cfg, "scope_rules", ()) or ():
        if rule.when_mention is not None and not _has_word(question, rule.when_mention):
            continue
        if rule.when_not_cue and any(_has_word(question, c) for c in rule.when_not_cue):
            continue
        if rule.when_route is not None and rule.when_route != route_id:
            continue
        applied.append(rule.id)
        for cls in rule.exclude_classes or ():
            if cls not in exclude:
                exclude.append(cls)
        if rule.set_participants and rule.set_participants not in participants:
            participants.append(rule.set_participants)
        if rule.require_participant and require is None:
            require = rule.require_participant
    filters = {"exclude_classes": exclude, "participants": participants, "require_participant": require}
    scope = Scope(domains, tuple(dict.fromkeys(excluded_documents)), tuple(applied), filters)
    return Slot("scope", scope, source, quality)


# --------------------------------------------------------------------------- date window (§4.7)

_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_MONTH_ALTS = sorted({*_MONTHS, *(m[:3] for m in _MONTHS), "sept"}, key=len, reverse=True)
_MONTH_RE = r"(?P<mon>" + "|".join(_MONTH_ALTS) + r")\.?"
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_RE = re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r"\s+(?P<y>(?:19|20)\d{2})\b")
_MY_RE = re.compile(r"\b" + _MONTH_RE + r"\s+(?P<y>(?:19|20)\d{2})\b")
_YEAR_RE = re.compile(r"(?<![\d-])((?:19|20)\d{2})(?![\d-])")
_REL_RE = re.compile(r"\b(today|yesterday|(?:last|this)\s+(?:year|month|week))\b")


def _month_no(token: str) -> int:
    token = token.casefold().rstrip(".")
    return next(n for m, n in _MONTHS.items() if m.startswith(token[:3]))


def _mk(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, _calendar.monthrange(y, m)[1]))


def _win(start: date, end: date, source: str, event: str | None = None, note: str | None = None) -> Slot:
    return Slot("date_window", {"from": start.isoformat(), "to": end.isoformat(), "event": event},
                source, FULL, note)


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1)


def _calendar_window(question: str, today: date, cfg) -> Slot | None:
    year_in_q = _YEAR_RE.search(question)
    for ev in getattr(cfg, "calendar", ()) or ():
        if not _has_word(question, ev.name):
            continue
        month, day = (int(p) for p in ev.month_day.split("-"))
        if ev.recurring == "once" and ev.year is not None:
            year = ev.year
        elif year_in_q:
            year = int(year_in_q.group(1))
        else:
            year = today.year
            if _mk(year, month, day) > today:
                year -= 1
        start = _mk(year, month, day)
        return _win(start, start + timedelta(days=ev.span_days), "calendar", ev.name)
    return None


def date_window_slot(question: str, today: date, cfg, document_date: date | None = None) -> Slot | None:
    """A `{date_window}` as `{"from", "to", "event"}` (ISO dates, `to` exclusive), or None.
    Order: a calendar event named in the question, an explicit date or year, a relative phrase
    (against `document_date` when given, else `today`). A window containing the current year or
    month is flagged `note="partial_year"` / `"partial_month"` (the bundle says so)."""
    slot = _calendar_window(question, today, cfg)
    if slot:
        return slot
    q = question.casefold()
    m = _ISO_RE.search(q)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return _win(d, d + timedelta(days=1), "explicit_date")
        except ValueError:
            pass
    m = _DMY_RE.search(q)
    if m:
        d = _mk(int(m["y"]), _month_no(m["mon"]), int(m["d"]))
        return _win(d, d + timedelta(days=1), "explicit_date")
    m = _MY_RE.search(q)
    if m:
        y, mo = int(m["y"]), _month_no(m["mon"])
        return _win(date(y, mo, 1), _month_end(y, mo), "explicit_date",
                    note="partial_month" if (y, mo) == (today.year, today.month) else None)
    m = _YEAR_RE.search(q)
    if m:
        y = int(m[1])
        return _win(date(y, 1, 1), date(y + 1, 1, 1), "explicit_date",
                    note="partial_year" if y == today.year else None)
    m = _REL_RE.search(q)
    if m:
        anchor = document_date or today
        phrase = m[1]
        if phrase == "today":
            return _win(anchor, anchor + timedelta(days=1), "relative")
        if phrase == "yesterday":
            return _win(anchor - timedelta(days=1), anchor, "relative")
        kind = phrase.split()[1]
        last = phrase.startswith("last")
        if kind == "year":
            y = anchor.year - (1 if last else 0)
            return _win(date(y, 1, 1), date(y + 1, 1, 1), "relative",
                        note="partial_year" if y == today.year else None)
        if kind == "month":
            y, mo = anchor.year, anchor.month
            if last:
                y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
            return _win(date(y, mo, 1), _month_end(y, mo), "relative",
                        note="partial_month" if (y, mo) == (today.year, today.month) else None)
        monday = anchor - timedelta(days=anchor.weekday()) - (timedelta(days=7) if last else timedelta())
        return _win(monday, monday + timedelta(days=7), "relative")
    return None


# --------------------------------------------------------------------------- follow-up (§4.6)

@dataclass(frozen=True)
class PreviousSubject:
    """Mention-like: what the previous turn was about, marked `via="previous"`. `ref` is the
    original entity ref / document, so the planner needs no new resolution."""
    text: str
    kind: str                  # entity | document
    ref: Any
    start: int = 0
    end: int = 0
    via: str = "previous"
    entries: tuple = ()


def follow_up_subjects(previous_entities: Iterable[Any], previous_documents: Iterable[Any]) -> list[PreviousSubject]:
    out: list[PreviousSubject] = []
    for ent in previous_entities or ():
        text = ent if isinstance(ent, str) else (ent.get("name") or ent.get("mention") or "")
        if str(text).strip():
            out.append(PreviousSubject(str(text), "entity", ent))
    for doc in previous_documents or ():
        text = doc if isinstance(doc, str) else (doc.get("name") or doc.get("document") or doc.get("title") or "")
        if str(text).strip():
            out.append(PreviousSubject(str(text), "document", doc))
    return out


# --------------------------------------------------------------------------- phrasings (P2b)

MAX_PHRASINGS = 5
MAX_SYNONYMS = 3
MAX_YEARS = 10


def mention_free_words(question: str, mentions: Sequence[Mention]) -> list[str]:
    """The question's content words outside every mention span, filler and stopwords stripped."""
    chars = list(question)
    for m in mentions:
        for i in range(max(0, m.start), min(len(chars), m.end)):
            chars[i] = " "
    return _strip_filler("".join(chars))


def _class_synonyms(token: str, class_table: Mapping[str, str]) -> list[str]:
    """Class names (underscores as spaces, casefolded) whose description holds the token."""
    out: list[str] = []
    forms = _stems(token)
    for name, description in class_table.items():
        if description and forms & {w for t in re.findall(r"[a-z0-9]+", description.casefold()) for w in _stems(t)}:
            out.append(name.replace("_", " ").casefold())
    return out


def phrasings_slot(question: str, mentions: Sequence[Mention], search_term: str, cfg=None,
                   class_table: Mapping[str, str] | None = None) -> Slot:
    """Ways to ask for one concept, no LLM: [the search term] + at most MAX_SYNONYMS synonyms + one
    paraphrase, deduplicated casefold, at most MAX_PHRASINGS.

    Synonyms: the plan config's `synonyms:` per search-term token first, then the catalogue classes
    whose description holds the token. Paraphrase: the question with the named mentions' spans removed
    and filler stripped (so a person's name never narrows the concept). `source` lists what contributed."""
    term = " ".join((search_term or "").replace('"', " ").split())
    lookup = getattr(cfg, "synonym", None)
    paraphrase = " ".join(mention_free_words(question, mentions))
    # The synonyms are looked up per word of the term and of the paraphrase (the term may be a person's name).
    tokens = [t for t in dict.fromkeys([*_WORD_RE.findall(term.casefold()), *paraphrase.split()])
              if t not in STOPWORDS and len(t) > 1]
    synonyms: list[str] = []
    configured = False
    for token in tokens:
        for syn in (lookup(token) if callable(lookup) else None) or ():
            if syn.casefold() not in synonyms:
                synonyms.append(syn.casefold())
                configured = True
    classes = False
    for token in tokens:
        for syn in _class_synonyms(token, class_table or {}):
            if syn not in synonyms:
                synonyms.append(syn)
                classes = True
    synonyms = synonyms[:MAX_SYNONYMS]
    out: list[str] = []
    seen: set[str] = set()
    used_paraphrase = False
    for i, text in enumerate([term, *synonyms, paraphrase]):
        key = _norm(text)
        if key and key not in seen and len(out) < MAX_PHRASINGS:
            seen.add(key)
            out.append(text)
            used_paraphrase = i == len(synonyms) + 1
    parts = ["search_term"] if term else []
    parts += (["config_synonyms"] if configured else []) + (["catalogue_synonyms"] if classes else [])
    if used_paraphrase:
        parts.append("paraphrase")
    return Slot("phrasings", out, "+".join(parts) or "question", FULL)


def years_slot(window: Slot | None) -> Slot:
    """Each calendar year the date window spans (`to` is exclusive), at most MAX_YEARS."""
    if window is None:
        return Slot("years", [], "none", FULL)
    start = date.fromisoformat(window.value["from"])
    end = date.fromisoformat(window.value["to"]) - timedelta(days=1)
    years = list(range(start.year, max(start.year, end.year) + 1))[:MAX_YEARS]
    return Slot("years", years, "date_window", FULL)


def twin_ids_slot(primary: Mapping | None) -> Slot:
    """The primary entity's id followed by its twins' ids (a resolved `entities[]` item)."""
    ids: list = []
    if primary and primary.get("candidates"):
        ids = [primary["candidates"][0]["_id"], *(t["_id"] for t in primary.get("twins") or ())]
    return Slot("twin_ids", list(dict.fromkeys(i for i in ids if i is not None)), "twins", FULL)


def vocabulary_tokens(names: Iterable[str]) -> set[str]:
    """Every word (with its plural/singular stems) of a set of names, casefolded."""
    out: set[str] = set()
    for name in names:
        for word in re.findall(r"[a-z0-9]+", str(name).replace("_", " ").casefold()):
            out |= _stems(word)
    return out


def rare_term(words: Iterable[str], index_names: Iterable[str], class_names: Iterable[str]) -> bool:
    """True when some word has no stem in the catalogue name index or the class names. False when
    the catalogue is empty (nothing to compare with)."""
    vocab = vocabulary_tokens([*index_names, *class_names])
    if not vocab:
        return False
    return any(len(w) > 2 and not (_stems(w) & vocab) for w in words)


# Generic English cues for the plan facts `negative`, `latest` and `view_count`.
NEGATIVE_CUES = ("never", "ever", "at all", "any mention", "any reference", "any record", "no mention",
                 "no record", "nothing", "none of", "not mentioned", "not recorded", "missing", "without")
LATEST_CUES = ("latest", "current", "currently", "most recent", "newest", "up to date", "amended",
               "amendment", "last version", "final version", "updated version")
OCCURRENCE_CUES = ("visits", "visited", "times", "occasions", "trips to", "every time", "each time")
COUNT_CUES = ("how many", "number of", "count of", "how often", "total number")
_CONTRACTION_RE = re.compile(r"\b\w+n['\u2019]t\b")


def has_cue(question: str, cues: Iterable[str]) -> bool:
    return any(_has_word(question, cue) for cue in cues)


def has_negative_cue(question: str) -> bool:
    return has_cue(question, NEGATIVE_CUES) or bool(_CONTRACTION_RE.search(question.casefold()))


def has_latest_cue(question: str) -> bool:
    return has_cue(question, LATEST_CUES)


def has_occurrence_cue(question: str) -> bool:
    return has_cue(question, OCCURRENCE_CUES)


def has_count_cue(question: str) -> bool:
    return has_cue(question, COUNT_CUES)


# --------------------------------------------------------------------------- quality (§4.8)

def slot_quality(slots: Mapping[str, Slot], *, twins_found: bool = False, twins_unioned: bool = True,
                 entity_from_fuzzy: bool = False, search_term_empty: bool = False,
                 all_domains: bool = False) -> tuple[float, dict]:
    """Product of the §4.8 factors. The flags are authoritative; a `search_term` slot with an
    empty value, or a `scope` slot sourced `all`, also trips its factor. Returns the score and
    `{"factors": {name: factor}, "weakest": name | None}`."""
    st, sc = slots.get("search_term"), slots.get("scope")
    factors = {
        "mention": FUZZY_MENTION if entity_from_fuzzy else 1.0,
        "twins": TWINS_SINGLE if twins_found and not twins_unioned else 1.0,
        "search_term": EMPTY_SEARCH if search_term_empty or (st is not None and st.value == "") else 1.0,
        "scope": ALL_DOMAINS if all_domains or (sc is not None and sc.source == "all") else 1.0,
    }
    score = 1.0
    for f in factors.values():
        score *= f
    low = min(factors, key=factors.get)
    return round(score, 4), {"factors": factors, "weakest": low if factors[low] < 1.0 else None}
