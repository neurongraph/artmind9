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
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_DATE_PATTERN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]{3,9}\s+\d{4}|[a-z]{3,9}\s+\d{4}|(?:19|20)\d{2}"
    r"|today|yesterday|last\s+(?:year|month|week))"
)
_DATE_RE = re.compile(r"\b" + _DATE_PATTERN + r"\b")


@dataclass(frozen=True)
class Facts:
    """What the question shows before any domain decision, for route scoring."""
    question: str          # casefolded
    mentions: int
    document: str | None
    has_store: bool
    has_date: bool


def has_date(question: str) -> bool:
    return _DATE_RE.search(question.casefold()) is not None


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def content_tokens(text: str, *, drop_capitalised: bool = True) -> set[str]:
    """Lower-case content words. Capitalised words after the first are names,
    which differ between a view's examples and a real question, so they are dropped."""
    out: set[str] = set()
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if drop_capitalised and match.start() > 0 and word[:1].isupper():
            continue
        low = word.casefold().strip("'’-")
        if len(low) >= 3 and low not in STOPWORDS:
            out.add(_stem(low))
    return out


@lru_cache(maxsize=512)
def compile_trigger(trigger: str) -> tuple[re.Pattern | None, frozenset[str]]:
    """A trigger's regex (None when it is only a fact slot) and the facts it needs."""
    text = trigger.casefold().strip()
    needs = frozenset(slot for slot in ("entity", "document") if "{" + slot + "}" in text)
    for slot in needs:
        text = text.replace("{" + slot + "}", " ")
    pieces = []
    for token in re.split(r"(\{date\}|\{year\})", text):
        if token == "{date}":
            pieces.append(_DATE_PATTERN)
        elif token == "{year}":
            pieces.append(r"(?:19|20)\d{2}")
        elif token.split():
            pieces.append(r"\s+".join(re.escape(w) for w in token.split()))
    if not pieces:
        return None, needs
    return re.compile(r"\b" + r"\s+".join(pieces) + r"\b"), needs


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
    return {v: (1.0 if v.casefold() in low else 0.0) for v in values}


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
    choice = max(keys, key=lambda k: (probabilities[k], -keys.index(k)))
    return Answer(choice, {k: round(p, 4) for k, p in probabilities.items()}, round(probabilities[choice], 4))


class LexicalClassifier:
    backend = "lexical"
    model = None

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        return {d.name: answer(d) for d in decisions}
