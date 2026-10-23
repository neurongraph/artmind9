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
# A number is a whole token: not glued to word characters or a hyphen (so not inside "2025-05-01" or
# "-5"), not part of a dotted version ("1.2.3") or a comma list ("1,2,3"), but a sentence-final "."
# or "," after it is fine. Thousands groups ("2,500") are one number; digit runs are capped.
_NUMBER_RE = re.compile(
    r"(?<![\w-])(?<!\d\.)(?<!\d,)"
    r"(\d{1,3}(?:,\d{3}){1,5}(?:\.\d{1,15})?|\d{1,15}(?:\.\d{1,15})?)"
    r"(?![\w-])(?!\.\d)(?!,\d)"
)
_RELATIVE_YEAR_RE = re.compile(r"\b(last|this)\s+year\b", re.I)
_TODAY_RE = re.compile(r"\btoday\b", re.I)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_MY_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b")
# Any non-empty quote: a one-character string value is still a value. candidates.py's _QUOTED_RE needs 2+ characters (a name).
_QUOTED_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def _free(span: tuple[int, int], used: set) -> bool:
    return not any(a < span[1] and span[0] < b for a, b in used)


def _in_range(spec: ParamSpec, value: float) -> bool:
    return (spec.min is None or value >= spec.min) and (spec.max is None or value <= spec.max)


def _number(spec: ParamSpec, question: str, today: date, used: set):
    """The first free in-range number in the question, else (for a param whose range admits a
    year) "last year" / "this year". Negative numbers are not filled."""
    for match in _NUMBER_RE.finditer(question):
        span = match.span(1)
        text = match.group(1)
        if spec.type == "int":
            if "." in text:
                continue
            value = int(text.replace(",", ""))
        else:
            value = float(text.replace(",", ""))
        if _free(span, used) and _in_range(spec, value):
            return value, text, span
    if (spec.min is not None or spec.max is not None) and _in_range(spec, today.year):
        for match in _RELATIVE_YEAR_RE.finditer(question):
            year = today.year - 1 if match.group(1).casefold() == "last" else today.year
            if _free(match.span(), used) and _in_range(spec, year):
                return year, match.group(0), match.span()
    return None


def _date(question: str, today: date, used: set):
    """Explicit dates first (ISO, "15 January 2026", "January 2026"), then the word "today".
    A day that does not exist ("30 February 2026") consumes its span for this call, so the
    month-year pattern cannot pick up the tail of it."""
    consumed: set = set()
    for regex, build in (
        (_ISO_RE, lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (_DMY_RE, lambda m: date(int(m[3]), MONTHS[m[2].casefold()], int(m[1]))),
        (_MY_RE, lambda m: date(int(m[2]), MONTHS[m[1].casefold()], 1)),
    ):
        for match in regex.finditer(question):
            if not _free(match.span(), used) or not _free(match.span(), consumed):
                continue
            try:
                value = build(match)
            except KeyError:
                continue
            except ValueError:
                consumed.add(match.span())
                continue
            return value.isoformat(), match.group(0), match.span()
    for match in _TODAY_RE.finditer(question):
        if _free(match.span(), used) and _free(match.span(), consumed):
            return today.isoformat(), match.group(0), match.span()
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
