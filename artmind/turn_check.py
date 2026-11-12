"""Turn check: what a turn left open, from its trace spans (spec docs/superpowers/specs/2026-11-11-turn-check-design.md).

Pure and stdlib-only: `open_items(spans)` takes a turn's spans in the order they ran and returns one row per open item.
No I/O, no LLM, never raises on odd shapes. A span is a dict with `command` ("query survey"), `args` (dict),
`rows`, `rows_total`, `truncated` and `evidence` (the dict `traces/evidence.summarize` stored: `next`, `warnings`,
`sql`, `status`, ...).

Built first for the retrospective dry run (`scripts/turn_check_retro.py`) over stored benchmark turns; the
`artmind query check` command and the Stop hook come only if open items turn out to predict missed must items.
"""

import re
import shlex
from typing import Any

KINDS = ("next_unfollowed", "partial_relied_on", "mart_unchecked", "unread_part", "identity_open")

#: Commands that read the rest of a cut result.
_PAGE_COMMANDS = ("query page",)
#: Commands that check mart rows against the documents (spec §3, `mart_unchecked`).
_MART_CHECKS = ("query survey", "query vector-text", "query topics run")
_WORD = re.compile(r"^[a-z][a-z0-9-]*$")
#: Parts a result says it did not read. "health not cached" is excluded: the agent cannot close it (the background
#: catalogue job builds it).
_UNREAD = re.compile(r"\b(not read|unreadable|summaries missing)\b")
_TWINS = re.compile(r"share the name")


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def next_command(text: Any) -> list[str]:
    """The command-path candidates a `next` names, longest first ("query graph pattern5", "query graph"); [] when
    it is not an `artmind ...` command."""
    if not isinstance(text, str):
        return []
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens or tokens[0] != "artmind":
        return []
    words = []
    for token in tokens[1:4]:
        if not _WORD.match(token):
            break
        words.append(token)
    return [" ".join(words[:n]) for n in range(len(words), 1, -1)]


def _ran(later: list[dict], paths: list[str]) -> bool:
    return any(s.get("command") in paths for s in later)


def _is_partial(span: dict) -> bool:
    rows, total = span.get("rows"), span.get("rows_total")
    return bool(span.get("truncated")) or (isinstance(rows, int) and isinstance(total, int) and total > rows)


def _pages(span: dict, later: list[dict]) -> bool:
    for s in later:
        if s.get("command") in _PAGE_COMMANDS:
            return True
        args = _dict(s.get("args"))
        if s.get("command") == span.get("command") and (args.get("all") is True or args.get("offset")):
            return True
    return False


def _uses_an_id(later: list[dict]) -> bool:
    return any(_dict(s.get("args")).get(k) for s in later for k in ("entity_id", "entityId", "id_list"))


def open_items(spans: list[dict]) -> list[dict]:
    """Open items of one turn, in span order: {n, kind, span (index), command, detail, close}."""
    spans = [s for s in spans if isinstance(s, dict) and isinstance(s.get("command"), str)]
    items: list[dict] = []

    def add(kind: str, i: int, detail: str, close: str | None) -> None:
        items.append({"n": len(items) + 1, "kind": kind, "span": i, "command": spans[i]["command"],
                      "detail": detail, "close": close})

    for i, span in enumerate(spans):
        later, ev = spans[i + 1:], _dict(span.get("evidence"))
        nxt = ev.get("next")
        paths = next_command(nxt)
        followed = bool(paths) and _ran(later, paths)
        if paths and not followed:
            add("next_unfollowed", i, paths[0], nxt)
        if _is_partial(span) and not _pages(span, later) and not followed:
            add("partial_relied_on", i, f"{span.get('rows')} of {span.get('rows_total') or '?'}",
                nxt if paths else "artmind query page <handle> --compact")
        sql = ev.get("sql")
        if (span["command"] == "query text2sql" and isinstance(sql, str) and "__mart_" in sql
                and (span.get("rows") or 0) > 0 and not _ran(later, list(_MART_CHECKS))):
            add("mart_unchecked", i, "mart rows not checked against the documents", nxt if paths else None)
        warnings = [w for w in (ev.get("warnings") or []) if isinstance(w, str)]
        for w in warnings:
            if _UNREAD.search(w):
                add("unread_part", i, w[:120], None)
        if span["command"] == "query entity-resolve" and (
                ev.get("status") == "needs_disambiguation" or any(_TWINS.search(w) for w in warnings)):
            if not _uses_an_id(later):
                add("identity_open", i, "twins or ambiguous name, no later span named an id", None)
    return items
