"""Post-checks on a reduced page body (spec 2026-11-01 §3.2 step 5). Pure.

Citations are markers `[#<id>]`. A marker naming an id the build did not supply
is removed. Each paragraph (not a heading) without a marker is counted. Each
number in a sentence that appears in none of the passages the sentence cites
(or, without its own marker, its paragraph cites) is counted. A four-digit year
(1990-2100) is the one exception: the model derives dates ("the first anniversary
of 2023"), so it counts as supported when it is within `YEAR_SLACK` years of a
year in those passages. Figures, percentages and clause numbers stay strict.

Date tokenisation: d.m.yy / d.m.yyyy / d/m/yyyy / 'D Month YYYY' / 'Month YYYY'
forms are recognised in both sentence and passage text. A sentence date is
considered supported (and its component numbers exempted from bare-number checks)
when the same (day, month, year) tuple — or the same (month, year) for a partial
date — appears in any cited passage in any of the above forms. Two-digit years
00-69 expand to 20xx, 70-99 to 19xx. YEAR_SLACK still applies to bare 4-digit
years; it is NOT applied to date matching (dates require an exact year match).

Identifier exemption: tokens of the form '<1-6 uppercase-starting letters>
[space or hyphen] <digits>' (e.g. 'AB 7', 'XY 12345', 'QRS-2026-001',
'ZZ-90210') are not counted as figures. Currency, percentage and structural
references ('paragraph 9.4', 'clause 12.2') remain strict.
"""
from __future__ import annotations

import re

MARKER = re.compile(r"\[#([A-Za-z0-9_.:\-]+)\]")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_YEAR = re.compile(r"(?<![\w.,])(?:199\d|20\d\d|2100)(?![\w,]|\.\d)")
YEAR_SLACK = 5

# ---------------------------------------------------------------------------
# Date tokenisation
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
# Longest first so alternation matches greedily.
_MONTHS_PAT = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))

# d.m.yy  d.m.yyyy  d/m/yyyy  (day 1-31, month 1-12)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
# D MonthName YYYY  or  D MonthName YY
_DATE_WORD_FULL = re.compile(
    r"\b(\d{1,2})\s+(" + _MONTHS_PAT + r")\s+(\d{2,4})\b", re.IGNORECASE
)
# dd/Mon/yyyy and dd-Mon-yy  (01/Sep/2025)
_DATE_SLASH_MON = re.compile(
    r"\b(\d{1,2})[/-](" + _MONTHS_PAT + r")[/-](\d{2,4})\b", re.IGNORECASE
)
# Mon'yy  (Aug'26 — partial date)
_DATE_MON_APOS = re.compile(r"\b(" + _MONTHS_PAT + r")\s*['\u2019](\d{2})\b", re.IGNORECASE)
# MonthName YYYY  (no day — partial date)
_DATE_WORD_MONTH = re.compile(
    r"\b(" + _MONTHS_PAT + r")\s+(\d{4})\b", re.IGNORECASE
)


def _expand_year(y: int) -> int:
    """Two-digit year: 00-69 → 20xx, 70-99 → 19xx."""
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def _parse_dates(text: str) -> tuple[list[tuple[int, int, int]], list[tuple[int, int]]]:
    """Extract date expressions from *text*.

    Returns ``(dates, spans)`` where each date is ``(day, month, year)``
    (day=0 means absent — partial date) and each span is ``(start, end)``
    covering the matched characters.
    """
    dates: list[tuple[int, int, int]] = []
    spans: list[tuple[int, int]] = []

    for m in _DATE_NUMERIC.finditer(text):
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), _expand_year(int(m.group(3)))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                dates.append((d, mo, y))
                spans.append((m.start(), m.end()))
        except ValueError:
            pass

    for m in _DATE_WORD_FULL.finditer(text):
        try:
            d = int(m.group(1))
            mo = _MONTH_NAMES.get(m.group(2).lower(), 0)
            y = _expand_year(int(m.group(3)))
            if mo:
                dates.append((d, mo, y))
                spans.append((m.start(), m.end()))
        except ValueError:
            pass

    for m in _DATE_SLASH_MON.finditer(text):
        try:
            d = int(m.group(1))
            mo = _MONTH_NAMES.get(m.group(2).lower(), 0)
            y = _expand_year(int(m.group(3)))
            if mo and 1 <= d <= 31:
                dates.append((d, mo, y))
                spans.append((m.start(), m.end()))
        except ValueError:
            pass

    for m in _DATE_MON_APOS.finditer(text):
        mo = _MONTH_NAMES.get(m.group(1).lower(), 0)
        if mo:
            dates.append((0, mo, _expand_year(int(m.group(2)))))
            spans.append((m.start(), m.end()))

    for m in _DATE_WORD_MONTH.finditer(text):
        mo = _MONTH_NAMES.get(m.group(1).lower(), 0)
        try:
            y = int(m.group(2))
            if mo:
                dates.append((0, mo, y))
                spans.append((m.start(), m.end()))
        except ValueError:
            pass

    return dates, spans


def _dates_match_exact(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    """True when all non-zero components of *a* agree with *b* (exact year match).

    A zero day or month means that component is absent; absent components match
    anything.  Years must be equal.
    """
    da, ma, ya = a
    db, mb, yb = b
    if ya != yb:
        return False
    if ma and mb and ma != mb:
        return False
    if da and db and da != db:
        return False
    return True


# ---------------------------------------------------------------------------
# Identifier exemption
# ---------------------------------------------------------------------------

# Space-separated identifier: 2-6 ALL-UPPERCASE letters + space + digits.
# Examples: AB 7, XY 12345.  Requires ≥2 letters so single-letter words
# ("A", "I") before a number are not mistakenly exempt.
_IDENT_SPACE = re.compile(r"\b([A-Z]{2,6}) (\d+)\b")

# Hyphen-compound identifier: 1-6 ALL-UPPERCASE letters + at least one
# hyphen segment containing alphanumerics.
# Examples: QRS-2026-001, ZZ-90210, B-0338.
_IDENT_HYPHEN = re.compile(r"\b([A-Z]{1,6})(?:-[A-Za-z0-9]+)+\b")

# Tokens with digits directly attached (ABC47, B0338, SE3000) are already
# blocked by _NUMBER's word-char lookbehind and need no separate pattern.


def _identifier_spans(text: str) -> list[tuple[int, int]]:
    """Return character spans covered by identifier tokens in *text*.

    Only tokens with an ALL-UPPERCASE prefix are considered — this naturally
    excludes structural references such as 'clause 12', 'part 3', 'Appendix 5'
    whose prefix contains lowercase letters.
    """
    spans = []
    for m in _IDENT_SPACE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in _IDENT_HYPHEN.finditer(text):
        spans.append((m.start(), m.end()))
    return spans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spans_overlap(pos_s: int, pos_e: int, spans: list[tuple[int, int]]) -> bool:
    return any(pos_s < e and pos_e > s for s, e in spans)


def _plain_number(n: str) -> str:
    return n.replace(",", "").rstrip(".")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check(
    body: str,
    texts: dict[str, str],
    *,
    titles: dict[str, str] | None = None,
    topic: str | None = None,
) -> tuple[str, dict]:
    """Check *body* against cited *texts* and return the cleaned body plus a
    stats dict.

    Parameters
    ----------
    body:
        The reduced page markdown.  Citation markers ``[#<id>]`` are validated
        against *texts*; unknown ones are stripped.
    texts:
        ``{passage_id: passage_text}`` for every id the build supplied.
    titles:
        Optional ``{passage_id: document_title}`` map.  Numbers that appear in
        a cited passage's document title count as supported.
    topic:
        The topic text the page was built for. Numbers and dates the reader supplied
        in it (a deposit of 15,000, "Q4 2025") are not corpus claims, so they count
        as supported in every sentence.
    """
    removed = 0

    def keep(match: re.Match) -> str:
        nonlocal removed
        if match.group(1) in texts:
            return match.group(0)
        removed += 1
        return ""

    body = MARKER.sub(keep, body)
    uncited = unsupported = 0

    for paragraph in re.split(r"\n\s*\n", body):
        p = paragraph.strip()
        if not p or p.startswith("#"):
            continue
        para_ids = MARKER.findall(p)
        if not para_ids:
            uncited += 1

        for sentence in _SENTENCE.split(p):
            ids = MARKER.findall(sentence) or para_ids
            if not ids:
                continue

            sent_clean = MARKER.sub("", sentence)

            # --- date spans in the sentence ---
            sent_dates, sent_date_spans = _parse_dates(sent_clean)

            # --- identifier spans in the sentence ---
            sent_ident_spans = _identifier_spans(sent_clean)

            # --- build cited text (commas stripped for substring check) ---
            cited = " ".join(texts[i].replace(",", "") for i in ids if i in texts)
            if titles:
                for i in ids:
                    if i in titles:
                        cited += " " + titles[i].replace(",", "")

            if topic:
                cited += " " + topic.replace(",", "")

            cited_years = [int(y) for y in _YEAR.findall(cited)]
            cited_dates, _ = _parse_dates(cited)

            # --- find which sentence-date spans are supported exactly ---
            supported_date_spans: list[tuple[int, int]] = []
            for sd, ss in zip(sent_dates, sent_date_spans):
                if any(_dates_match_exact(sd, cd) for cd in cited_dates):
                    supported_date_spans.append(ss)

            # Numbers covered by a *supported* date span or by an identifier
            # span are exempt from the bare-number check.
            exempt = supported_date_spans + sent_ident_spans

            numbers: list[str] = []
            for m in _NUMBER.finditer(sent_clean):
                if not _spans_overlap(m.start(), m.end(), exempt):
                    numbers.append(_plain_number(m.group()))

            if not numbers:
                continue

            for n in numbers:
                if n in cited:
                    continue
                if _YEAR.fullmatch(n) and any(
                    abs(int(n) - y) <= YEAR_SLACK for y in cited_years
                ):
                    continue
                unsupported += 1

    return body, {
        "uncited_paragraphs": uncited,
        "unsupported_numbers": unsupported,
        "removed_citations": removed,
    }
