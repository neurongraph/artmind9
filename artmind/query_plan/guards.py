"""B12: confidence guards (spec 2026-11-07-b12-confidence-guards-design.md).

A guard names a shape the decision model plans wrongly at high confidence. When one fires the planner caps
the plan at `planner.GUARD_CAP` and records the reason; it never reroutes. Pure functions, no I/O.
"""

from __future__ import annotations

import re

#: Superseded-version wording: the question asks which version of a policy applies, not whether sources disagree.
_VERSION_RE = re.compile(
    r"\b(?:old|older|previous|earlier|outdated|obsolete|superseded|replaced|prior)\s+"
    r"(?:\w+\s+)?(?:polic(?:y|ies)|versions?|procedures?|documents?|rules?|guidelines?|manuals?|process(?:es)?)\b"
    r"|\bwhich\s+version\b|\b(?:current|latest|newest|new)\s+version\b|\bsuperseded\b",
    re.I)


#: The plan answers the previous turn's question, which the user's short reply only accepted.
# A matched saved view is exact, so it is not capped (planner).
INHERITED = {"id": "inherited.question",
             "why": "the question was taken from the previous turn; the plan is only as good as that turn's plan"}


def _named(entities: list[dict]) -> list[dict]:
    return [e for e in entities if e.get("via") != "first_person" and e.get("candidates")]


def evaluate(route: str | None, intent: str, question: str, entities: list[dict]) -> list[dict]:
    """The guards that fire, as `{id, why}`, in a fixed order."""
    hits: list[dict] = []
    if route == "path":
        named = [e for e in entities if e.get("via") != "first_person" and e.get("candidates")]
        if len(named) >= 2 and all(e.get("via") == "catalogue" for e in named[:2]):
            hits.append({"id": "path.unnamed",
                         "why": "both entities are group names found in the catalogue, not names the question spells out"})
    if intent == "follow_up_why" and len(_named(entities)) >= 2:
        hits.append({"id": "follow_up_why.names",
                     "why": "the question names several entities; resolve each one rather than surveying the sentence"})
    if route == "conflicts" and _VERSION_RE.search(question or ""):
        hits.append({"id": "conflicts.version",
                     "why": "the question is about which version applies (time_history), not whether sources disagree"})
    return hits
