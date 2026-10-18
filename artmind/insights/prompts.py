"""Narration prompts and their post-checks (spec 2026-10-17 §11).

Bump PROMPT_VERSION whenever a template or a check changes: it is part of
every cached narration's key, so old captions are rewritten under the new rules.
"""

from __future__ import annotations

import json
import re

PROMPT_VERSION = 1
MAX_WORDS = 40
CIRCLE_MAX_WORDS = 3
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_CAUSAL = re.compile(
    r"\b(cause[sd]?|causing|because|leads? to|led to|results? in|makes?|made|due to|drives?|so that)\b", re.I)
_CAPITALISED = re.compile(r"\b[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3}")

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


def check_narration(text: str, finding, known_names: set[str]) -> tuple[bool, str]:
    """(ok, reason). known_names are lowercased names of every entity in the domain."""
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if len(text.split()) > MAX_WORDS:
        return False, "too long"
    if finding.detector == "outcome_lift" and _CAUSAL.search(text):
        return False, "causal wording"
    allowed = {str(s.get("name", "")).casefold() for s in finding.subjects}
    for m in _CAPITALISED.finditer(text):
        words = m.group(0).split()
        for i in range(len(words)):
            for j in range(len(words), i, -1):
                cand = " ".join(words[i:j]).strip(".,;:'’").casefold()
                if cand in known_names and cand not in allowed:
                    return False, f"names {' '.join(words[i:j]).strip('.,;:')}"
    numbers = allowed_numbers(finding.stats)
    for n in _NUMBER.findall(text):
        if n not in numbers:
            return False, f"number {n} not in stats"
    return True, ""


def check_circle_name(name: str) -> tuple[bool, str]:
    name = (name or "").strip().strip('"')
    if not name:
        return False, "empty"
    if len(name.split()) > CIRCLE_MAX_WORDS or len(name) > 30:
        return False, "too long"
    if any(ch.isdigit() for ch in name):
        return False, "has digits"
    return True, ""
