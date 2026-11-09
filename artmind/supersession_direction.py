"""Which of two documents is the newer one (P2 batch, task 3).

Direction is decided from what the documents say about themselves, then from
their effective dates. Version strings are never used: a v2.1 may be older than
a v2.0 (the KYC pair). `None` means "cannot tell" and the caller applies
nothing -- an edge written the wrong way retires the current document.

Each side is `{id, name, _valid_from, version, meta, path?}`; `meta` is the
parsed header table (`Status`, `Supersedes`, `Superseded By`).
"""
from __future__ import annotations

import re
from pathlib import Path

META_LABELS = ("Status", "Supersedes", "Superseded By")
_LINK_RE = re.compile(r"\[\[([^\]|#]+)")
_ACTIVE = {"active", "current"}


def meta_for(doc_row: dict) -> dict:
    """The header-table values of a `:Document` row, read from its body.
    Empty when the body cannot be read."""
    from artmind import temporal

    try:
        body = temporal._read_doc_body_for(doc_row)
    except Exception:
        body = None
    if not body:
        return {}
    out = {}
    for label in META_LABELS:
        value = temporal._find_header_value(body, [label])
        if value:
            out[label] = value
    return out


def _stem(name: str) -> str:
    return Path(str(name).strip()).stem.casefold()


def _targets(raw: str | None) -> set[str]:
    if not raw or raw.strip().lower() == "none":
        return set()
    links = _LINK_RE.findall(raw)
    return {_stem(link) for link in links}


def _names(side: dict, label: str) -> set[str]:
    return _targets((side.get("meta") or {}).get(label))


def _status(side: dict) -> str:
    return str((side.get("meta") or {}).get("Status") or "").strip().casefold()


def _link_claims(a: dict, b: dict) -> tuple[bool, bool]:
    """(a is newer, b is newer) as the two documents' own links say."""
    sa, sb = _stem(a.get("name") or ""), _stem(b.get("name") or "")
    b_newer = sa in _names(b, "Supersedes") or sb in _names(a, "Superseded By")
    a_newer = sb in _names(a, "Supersedes") or sa in _names(b, "Superseded By")
    return a_newer, b_newer


def contradictory(a: dict, b: dict) -> bool:
    """True when the two documents' links claim opposite directions."""
    a_newer, b_newer = _link_claims(a, b)
    return a_newer and b_newer


def decide(a: dict, b: dict) -> tuple[dict, dict] | None:
    """`(newer, older)` or `None` when the direction is unknown or contradictory."""
    # 1. explicit links
    a_newer, b_newer = _link_claims(a, b)
    if a_newer and b_newer:
        return None
    if b_newer:
        return b, a
    if a_newer:
        return a, b
    # 2. explicit status
    stat_a, stat_b = _status(a), _status(b)
    if stat_a == "superseded" and stat_b in _ACTIVE:
        return b, a
    if stat_b == "superseded" and stat_a in _ACTIVE:
        return a, b
    # 3. dates
    da, db = str(a.get("_valid_from") or ""), str(b.get("_valid_from") or "")
    if da and db and da != db:
        return (a, b) if da > db else (b, a)
    return None
