"""Fill a matched view's scalar params from the question (spec 2026-10-22 §4.2).

Numbers and dates by pattern, strings only from a quoted span, bool and list
from their defaults. Nothing is invented: a required param with no value is
`missing_param` for the planner.
"""

from __future__ import annotations

import itertools
import re
from datetime import date

from artmind.kg_views.model import ParamSpec, jsonable

MONTHS = {
    name: i + 1
    for i, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
        ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
        ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ])
    for name in names
}
_NUMBER_RE = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)(?![\w.-])")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_MY_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b")
_QUOTED_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def _free(span: tuple[int, int], used: set) -> bool:
    return not any(a < span[1] and span[0] < b for a, b in used)


def _in_range(spec: ParamSpec, value: float) -> bool:
    return (spec.min is None or value >= spec.min) and (spec.max is None or value <= spec.max)


def _number(spec: ParamSpec, question: str, today: date, used: set):
    low = question.casefold()
    for phrase, year in (("last year", today.year - 1), ("this year", today.year)):
        index = low.find(phrase)
        span = (index, index + len(phrase))
        if index >= 0 and _free(span, used) and _in_range(spec, year):
            return year, phrase, span
    for match in _NUMBER_RE.finditer(question):
        span = match.span(1)
        text = match.group(1)
        value = float(text) if spec.type == "float" or "." in text else int(text)
        if spec.type == "int" and isinstance(value, float):
            continue
        if _free(span, used) and _in_range(spec, value):
            return value, text, span
    return None


def _date(question: str, today: date, used: set):
    low = question.casefold()
    if "today" in low:
        index = low.index("today")
        return today.isoformat(), "today", (index, index + 5)
    for regex, build in (
        (_ISO_RE, lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (_DMY_RE, lambda m: date(int(m[3]), MONTHS[m[2].casefold()], int(m[1]))),
        (_MY_RE, lambda m: date(int(m[2]), MONTHS[m[1].casefold()], 1)),
    ):
        for match in regex.finditer(question):
            try:
                value = build(match)
            except (KeyError, ValueError):
                continue
            if _free(match.span(), used):
                return value.isoformat(), match.group(0), match.span()
    return None


def _quoted(question: str, used: set):
    for match in _QUOTED_RE.finditer(question):
        if _free(match.span(), used):
            return (match.group(1) or match.group(2)), match.group(0), match.span()
    return None


def fill_scalar(spec: ParamSpec, question: str, today: date, used: set) -> dict | None:
    """`{type, via, input, value}`, the default, or None for a required param with no value.
    `used` holds character spans already taken by other params."""
    found = None
    if spec.type in ("int", "float"):
        found = _number(spec, question, today, used)
    elif spec.type == "date":
        found = _date(question, today, used)
    elif spec.type == "string":
        found = _quoted(question, used)
    if found is not None:
        value, text, span = found
        used.add(span)
        return {"type": spec.type, "via": "quoted" if spec.type == "string" else "regex", "input": text,
                "value": value}
    if not spec.required:
        return {"type": spec.type, "via": "default", "value": jsonable(spec.default)}
    return None


def combinations(groups: dict[str, list[dict]], cap: int) -> list[dict] | None:
    """Every pick of one entity per param; None when there are more than `cap`."""
    names = list(groups)
    count = 1
    for name in names:
        count *= len(groups[name])
    if count > cap:
        return None
    return [dict(zip(names, picks)) for picks in itertools.product(*(groups[n] for n in names))]
