"""Narration prompts and their post-checks (spec 2026-10-17 §11).

Bump PROMPT_VERSION whenever a template or a check changes: it is part of
every cached narration's key, so old captions are rewritten under the new rules.
"""

from __future__ import annotations

import json
import re

PROMPT_VERSION = 3
MAX_WORDS = 40
CIRCLE_MAX_WORDS = 3
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\d])")
_CAUSAL_LIGHT = re.compile(r"\b(cause[sd]?|because|leads? to|led to|due to)\b", re.I)
_CAUSAL = re.compile(
    r"\b(cause[sd]?|causing|because|leads? to|led to|leading to|results? in|resulting in|makes?|made|due to|"
    r"drives?|driven by|driving|so that|therefore|thanks to|as a result|boosts?|increases?|improves?|reduces?|"
    r"triggers?|contributes?|explains?|affects?|responsible for)\b", re.I)
_NUMBER_WORDS = re.compile(
    r"\b(twice|half|dozen|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|hundred|thousand)\b", re.I)
_UNSAFE_CHARS = re.compile(r"[<>\[\]`{}]|http", re.I)
MAX_NAME_TOKENS = 6

FINDING_TEMPLATE = """You write one-sentence captions for findings computed from a personal knowledge graph.

Rules:
- Use only the facts in each finding's JSON. Add no facts, causes, advice or judgement.
- For detector "outcome_lift" describe co-occurrence only. Never write "causes", "because", "leads to", "makes" or "due to".
- Name only the subjects listed in that finding.
- Every number you write must appear in that finding's stats; round to at most one decimal place.
- At most 30 words per caption. Plain, neutral language.

Return only JSON: an object mapping each finding's "key" to its caption, with exactly these keys.

Findings:
{items}
"""

CIRCLE_TEMPLATE = """Name each group of people from a personal knowledge graph with a short label of 1 to 3 words,
such as "Family", "Work team" or "College friends". Use only the information given. Do not use a member's
name. Do not use numbers.

Return only JSON: an object mapping each circle's "id" to its label.

Circles:
{items}
"""


def compact_stats(stats: dict, max_items: int = 5, max_keys: int = 12) -> dict:
    out = {}
    for k, v in stats.items():
        if isinstance(v, list):
            out[k] = v[:max_items]
        elif isinstance(v, dict):
            out[k] = dict(list(v.items())[:max_keys])
        else:
            out[k] = v
    return out


def finding_prompt(findings) -> str:
    items = [{"key": f.key, "detector": f.detector, "title": f.title,
              "subjects": [{"name": s.get("name"), "class": s.get("class")} for s in f.subjects[:10]],
              "stats": compact_stats(f.stats)} for f in findings]
    return FINDING_TEMPLATE.format(items=json.dumps(items, ensure_ascii=False, indent=1))


def circle_prompt(circles: list[dict]) -> str:
    return CIRCLE_TEMPLATE.format(items=json.dumps(circles, ensure_ascii=False, indent=1))


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def allowed_numbers(stats: dict) -> set[str]:
    """Every number a caption may use: each numeric stat with its rounded and percentage forms,
    plus digit runs inside strings (dates, periods)."""
    out: set[str] = set()
    for v in _walk(stats):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            for x in (v, v * 100) if 0 < abs(v) <= 1 else (v,):
                out.update({f"{x:g}", f"{round(x, 1):g}", f"{round(x, 2):g}", str(int(round(x))), str(int(x))})
        else:
            out.update(_NUMBER.findall(str(v)))
    return out


def _walk_lists(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk_lists(v)
    elif isinstance(value, (list, tuple)):
        yield value
        for v in value:
            yield from _walk_lists(v)


def _split_tokens(text: str) -> list[tuple[str, str, bool]]:
    """(casefolded, original, starts_sentence) per token: split on whitespace and hyphens,
    surrounding punctuation and a trailing possessive stripped."""
    out = []
    start = True
    for m in re.finditer(r"[^\s\-\u2010-\u2015]+", text):
        raw = m.group(0)
        tok = re.sub(r"^\W+|\W+$", "", raw)
        tok = re.sub(r"['\u2019]s$", "", tok, flags=re.I)
        if tok:
            out.append((tok.casefold(), tok, start))
            start = False
        if raw[-1] in ".!?":
            start = True
    return out


def _foreign_name(text: str, known_names, subject_names) -> str | None:
    """The first phrase in text that names a known entity other than the finding's own subjects."""
    known = set()
    longest = 1
    for n in known_names:
        toks = tuple(t[0] for t in _split_tokens(n))
        if toks:
            known.add(toks)
            longest = max(longest, len(toks))
    longest = min(longest, MAX_NAME_TOKENS)
    subjects = [tuple(t[0] for t in _split_tokens(n)) for n in subject_names]
    subjects = [s for s in subjects if s]
    toks = _split_tokens(text)
    for i in range(len(toks)):
        for n in range(min(longest, len(toks) - i), 0, -1):
            window = toks[i:i + n]
            key = tuple(t[0] for t in window)
            if key not in known:
                continue
            if n == 1:
                _cf, orig, at_start = window[0]
                if not ((orig[:1].isupper() and not at_start) or (orig.isupper() and len(orig) >= 2)):
                    continue
            if any(key == s or set(key) <= set(s) for s in subjects):
                continue
            return " ".join(t[1] for t in window)
    return None


def _to_float(n: str) -> float | None:
    try:
        return float(n)
    except ValueError:
        return None


def check_narration(text: str, finding, known_names: set[str]) -> tuple[bool, str]:
    """(ok, reason). known_names are lowercased names of every entity in the domain."""
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if len(text.split()) > MAX_WORDS:
        return False, "too long"
    if _CAUSAL_LIGHT.search(text) or (finding.detector == "outcome_lift" and _CAUSAL.search(text)):
        return False, "causal wording"
    phrase = _foreign_name(text, known_names, [str(s.get("name", "")) for s in finding.subjects])
    if phrase:
        return False, f"names {phrase}"
    if _NUMBER_WORDS.search(text):
        return False, "number word"
    numbers = allowed_numbers(finding.stats) | set(_NUMBER.findall(finding.title or ""))
    numbers |= {str(len(v)) for v in _walk_lists(finding.stats)}
    allowed_floats = [x for x in (_to_float(n) for n in numbers) if x is not None]
    for n in _NUMBER.findall(_THOUSANDS.sub(lambda m: m.group(0).replace(",", ""), text)):
        v = _to_float(n)
        if v is None or not any(abs(v - a) <= 1e-9 for a in allowed_floats):
            return False, f"number {n} not in stats"
    return True, ""


def check_circle_name(name: str, member_names=()) -> tuple[bool, str]:
    name = (name or "").strip().strip('"')
    if not name:
        return False, "empty"
    if len(name.split()) > CIRCLE_MAX_WORDS or len(name) > 30:
        return False, "too long"
    if any(ch.isdigit() for ch in name):
        return False, "has digits"
    if _UNSAFE_CHARS.search(name):
        return False, "unsafe characters"
    for member in member_names:
        parts = str(member or "").split()
        forms = [" ".join(parts)] + ([parts[0]] if parts and len(parts[0]) >= 3 else [])
        if any(f and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", name, re.I) for f in forms):
            return False, "names a member"
    return True, ""
