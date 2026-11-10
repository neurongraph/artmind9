"""The shape rule (spec 2026-11-10 §6.2): resolve `--by auto` and `--split auto` from orient's
peel-1 facts for the scoped domains (`time.dated.from_content`, `time.span`,
`unit.documents`, family). Deterministic code, no LLM.

- by: **year** when at least 80 % of documents are dated from content and the span exceeds
  24 months; **month** when the span is 3–24 months and there are at least 60 documents;
  otherwise **none**.
- split: **domain** when a scoped domain is a family root with two or more children holding
  a keyword match for some subject (decided after the count, `split_from_counts`).
"""
from __future__ import annotations

from datetime import date

DATED_SHARE = 0.8
YEAR_MONTHS = 24
MONTH_MIN, MONTH_DOCS = 3, 60


def _months(span) -> float | None:
    try:
        start, end = date.fromisoformat(str(span[0])[:10]), date.fromisoformat(str(span[1])[:10])
    except (TypeError, ValueError, IndexError):
        return None
    return (end - start).days / 30.44


def facts(domains: list[str], cards: list[dict] | None = None) -> dict:
    """The facts the rule reads, summed over the scoped domains' cards (a family root's
    card already rolls up its children, so a child of a scoped root is skipped)."""
    if cards is None:
        from artmind import orient

        cards = orient.corpus().get("domains") or []
    by_name = {c.get("domain"): c for c in cards if isinstance(c, dict)}
    scoped = [d for d in domains if d in by_name and not any(d.startswith(o + ".") for o in domains if o != d)]
    documents = dated = 0
    starts, ends = [], []
    children = 0
    for d in scoped:
        c = by_name[d]
        documents += int((c.get("unit") or {}).get("documents") or 0)
        t = c.get("time") or {}
        dated += int((t.get("dated") or {}).get("from_content") or 0)
        span = t.get("span") or []
        if len(span) == 2 and span[0] and span[1]:
            starts.append(str(span[0]))
            ends.append(str(span[1]))
        children += sum(1 for name in by_name if name.startswith(d + ".") and name.count(".") == d.count(".") + 1)
    span = [min(starts), max(ends)] if starts else None
    return {"documents": documents, "dated_from_content": dated, "span": span,
            "months": None if span is None else round(_months(span) or 0.0, 1), "children": children,
            "domains": scoped}


def resolve_by(by: str, f: dict) -> str:
    if by != "auto":
        return by
    documents, months = f.get("documents") or 0, f.get("months")
    if not documents or months is None:
        return "none"
    if f.get("dated_from_content", 0) / documents >= DATED_SHARE and months > YEAR_MONTHS:
        return "year"
    if MONTH_MIN <= months <= YEAR_MONTHS and documents >= MONTH_DOCS:
        return "month"
    return "none"


def split_from_counts(split: str, domains: list[str], counted: list[list[dict]]) -> str:
    """`auto` → domain when the matches fall in two or more children of a scoped domain."""
    if split != "auto":
        return split
    from artmind.topics.grid import cell_domain

    children = {cell_domain(d.get("domain"), domains) for docs in counted for d in docs}
    children = {c for c in children if c and c not in domains}
    return "domain" if len(children) >= 2 else "none"


# ── cost (an estimate for the dry run and ARTMIND_TOPIC_MAX_COST_USD) ──────────

#: USD per million tokens, overridable per vault; an estimate, never a bill.
PRICE_IN_ENV, PRICE_OUT_ENV = "ARTMIND_TOPIC_PRICE_IN_PER_MTOK", "ARTMIND_TOPIC_PRICE_OUT_PER_MTOK"
DEFAULT_PRICE_IN, DEFAULT_PRICE_OUT = 3.0, 15.0
PROMPT_OVERHEAD_CHARS = 1500
MAP_OUT_TOKENS, REDUCE_OUT_TOKENS = 600, 1500


def _price(name: str, default: float) -> float:
    import os

    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def estimate_cost(map_input_chars: list[int], reduce_calls: int) -> float:
    """Tokens ≈ characters / 4. Map input is each call's passages; a reduce reads about
    what the maps wrote."""
    pin, pout = _price(PRICE_IN_ENV, DEFAULT_PRICE_IN), _price(PRICE_OUT_ENV, DEFAULT_PRICE_OUT)
    map_in = sum((c + PROMPT_OVERHEAD_CHARS) / 4 for c in map_input_chars)
    map_out = MAP_OUT_TOKENS * len(map_input_chars)
    reduce_in = map_out + reduce_calls * PROMPT_OVERHEAD_CHARS / 4
    reduce_out = REDUCE_OUT_TOKENS * reduce_calls
    return round(((map_in + reduce_in) * pin + (map_out + reduce_out) * pout) / 1_000_000, 4)
