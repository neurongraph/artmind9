"""Deterministic scoring for `query plan` (spec 2026-10-22 §4.4, "Lexical classifier").

The planner computes these scores for every decision, so this backend is always
available and is also the fallback when a decision endpoint fails mid-plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from artmind.query_plan.classify.base import Answer, Decision

STOPWORDS = frozenset("""
a about above after again all am an and any are as at be been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers
him his how i if in into is it its just me more most my no nor not now of off on once only or other our
out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would
you your yours
""".split())
VIEW_SCALE = 5.0
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATE_PATTERN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+" + _MONTH + r"\s+\d{4}|" + _MONTH + r"\s+\d{4}|(?:19|20)\d{2}"
    r"|today|yesterday|(?:last|this)\s+(?:year|month|week))"
)
_YEAR_PATTERN = r"(?:19|20)\d{2}"
_INFLECTION = r"(?:s|es|ed|ing|ent|ence)?"
# A fact slot inside a trigger stands for up to a few words (a name, a file name).
_SLOT_BRIDGE = r"(?:\S+\s+){1,6}?"
_DATE_RE = re.compile(r"\b" + _DATE_PATTERN + r"\b")


@dataclass(frozen=True)
class Facts:
    """What the question shows before any domain decision, for route scoring."""
    question: str          # casefolded
    mentions: int
    document: str | None
    has_store: bool
    has_date: bool

    def __post_init__(self):
        object.__setattr__(self, "question", self.question.casefold())


def has_date(question: str) -> bool:
    return _DATE_RE.search(question.casefold()) is not None


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and re.search(r"(?:ch|sh|x|ss)es$", word):
        return word[:-2]
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def content_tokens(text: str, *, drop_capitalised: bool = True) -> set[str]:
    """Lower-case content words. Capitalised words after the first are names,
    which differ between a view's examples and a real question, so they are dropped."""
    out: set[str] = set()
    first = True
    for match in _WORD_RE.finditer(text.replace("\u2019", "'")):
        word = match.group(0)
        was_first, first = first, False
        if drop_capitalised and not was_first and word[:1].isupper():
            continue
        low = word.casefold().strip("'-")
        if low.endswith("'s"):
            low = low[:-2]
        if len(low) >= 3 and low not in STOPWORDS:
            out.add(_stem(low))
    return out


@lru_cache(maxsize=512)
def compile_trigger(trigger: str) -> tuple[re.Pattern | None, frozenset[str]]:
    """A trigger's regex (None when it is only a fact slot) and the facts it needs."""
    text = trigger.casefold().strip()
    needs = frozenset(slot for slot in ("entity", "document") if "{" + slot + "}" in text)
    pieces: list[tuple[str, bool, bool] | None] = []  # (regex, starts on a word char, ends on one); None = fact slot
    for token in re.split(r"(\{date\}|\{year\}|\{entity\}|\{document\})", text):
        if token in ("{entity}", "{document}"):
            if pieces and pieces[-1] is not None:
                pieces.append(None)
        elif token == "{date}":
            pieces.append((_DATE_PATTERN, True, True))
        elif token == "{year}":
            pieces.append((_YEAR_PATTERN, True, True))
        elif token.split():
            words = token.split()
            pieces.append((r"\s+".join(re.escape(w) for w in words),
                           bool(re.match(r"\w", words[0][0])), bool(re.match(r"\w", words[-1][-1]))))
    while pieces and pieces[0] is None:
        pieces.pop(0)
    while pieces and pieces[-1] is None:
        pieces.pop()
    if not pieces:
        return None, needs
    body = ""
    for i, piece in enumerate(pieces):
        before = pieces[i - 1] if i else False
        if piece is None:
            body += r"\s+" + _SLOT_BRIDGE
        else:
            body += (r"\s+" if before else "") + piece[0]
    if len(pieces) == 1 and re.fullmatch(r"[a-z]+", text):
        body += _INFLECTION
    start = r"\b" if pieces[0][1] else ""
    end = r"\b" if pieces[-1][2] else ""
    return re.compile(start + body + end), needs


def _trigger_hits(route, facts: Facts) -> list[str]:
    hits = []
    for trigger in route.triggers:
        regex, needs = compile_trigger(trigger)
        if "entity" in needs and facts.mentions == 0:
            continue
        if "document" in needs and not facts.document:
            continue
        if regex is None or regex.search(facts.question):
            hits.append(trigger)
    return hits


def _needs_met(needs: tuple[str, ...], facts: Facts) -> bool:
    checks = {
        "entity": facts.mentions >= 1,
        "two_entities": facts.mentions >= 2,
        "document": bool(facts.document),
        "store": facts.has_store,
        "date": facts.has_date,
    }
    return all(checks.get(need, True) for need in needs)


def route_scores(routes, facts: Facts) -> dict[str, float]:
    """One point per matched trigger, plus 0.5 when the route's needs are met."""
    scores = {}
    for route in routes:
        hits = len(_trigger_hits(route, facts))
        scores[route.id] = float(hits) + (0.5 if hits and _needs_met(route.needs, facts) else 0.0)
    return scores


def triggered(route, facts: Facts) -> list[str]:
    return _trigger_hits(route, facts)


def _overlap(question: set[str], text: set[str]) -> float:
    return len(question & text) / len(text) if text else 0.0


def view_scores(question: str, specs) -> dict[str, float]:
    """VIEW_SCALE x the best share of a view example's (or summary's) content words in the question."""
    q = content_tokens(question)
    return {
        spec.name: VIEW_SCALE * max(_overlap(q, content_tokens(t)) for t in (spec.summary, *spec.examples))
        for spec in specs
    }


def domain_scores(question: str, options) -> dict[str, float]:
    """Words shared with a domain option's name, document names and classes."""
    q = content_tokens(question, drop_capitalised=False)
    scores = {}
    for option in options:
        words = set(re.split(r"[._\s-]+", option.key.casefold()))
        for text in (*option.documents, *option.top_classes):
            words |= content_tokens(text.replace("_", " ").replace("-", " "), drop_capitalised=False)
        scores[option.key] = float(len(q & {_stem(w) for w in words if len(w) >= 3}))
    return scores


def value_scores(question: str, values) -> dict[str, float]:
    low = question.casefold()
    scores = {}
    for v in values:
        pattern = r"(?<!\w)" + re.escape(v.casefold()) + r"(?!\w)"
        scores[v] = 1.0 if v.strip() and re.search(pattern, low) else 0.0
    return scores


def answer(decision: Decision) -> Answer:
    """Scores → probabilities, with `none_mass` added to the default option."""
    keys = list(decision.options)
    scores = {k: max(0.0, float(decision.lexical_scores.get(k, 0.0))) for k in keys}
    total = sum(scores.values())
    if total == 0:
        return Answer(decision.default, {k: (1.0 if k == decision.default else 0.0) for k in keys},
                      decision.default_confidence)
    denominator = total + decision.none_mass
    probabilities = {k: s / denominator for k, s in scores.items()}
    probabilities[decision.default] = probabilities.get(decision.default, 0.0) + decision.none_mass / denominator
    choice = max(keys, key=lambda k: (round(probabilities[k], 9), k == decision.default, -keys.index(k)))
    return Answer(choice, {k: round(p, 4) for k, p in probabilities.items()}, round(probabilities[choice], 4))


class LexicalClassifier:
    backend = "lexical"
    model = None

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        return {d.name: answer(d) for d in decisions}
