"""Which artmind-query reference a result points at (spec 2026-11-11 skill progressive disclosure §5.1).

A result that shows something the skill handles in a reference (a hub, twins, a replaced document, mart rows,
conflict rows, Cypher, a survey) carries `guide: [key, ...]`; the skill reads `references/<key>.md`. A key is the
reference file's stem, never a path: the agent may have loaded the skill from the vault, `<vault>/.artmind` or
`~/.artmind`. test/test_skill_references.py keeps keys and files in step. Stdlib only; never raises.
"""

from __future__ import annotations

from typing import Any, Callable

#: Every key, in precedence order (the first two that apply are kept).
GUIDES: tuple[str, ...] = ("hubs", "resolve", "temporal", "composites", "conflicts", "graph-model", "survey")
MAX_GUIDES = 2
_NOT_AN_ANSWER = ("needs_disambiguation", "no_match")


def reads_mart(sql: Any) -> bool:
    """True when generated SQL reads a mart table (`<domain>__mart_*`)."""
    return isinstance(sql, str) and "__mart_" in sql


#: Commands whose main list is not `rows` (mirrors cli_conventions.LIST_KEY; kept here so this module stays stdlib).
_LIST_KEY = {"query survey": "documents"}


def _rows(command: str, payload: dict) -> list[dict]:
    rows = payload.get(_LIST_KEY.get(command, "rows"))
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _hubs(command: str, payload: dict) -> bool:
    return command == "query entity-context" and any(r.get("hub") is True or r.get("cut") for r in _rows(command, payload))


def _resolve(command: str, payload: dict) -> bool:
    if command not in ("query entity-resolve", "query views run"):
        return False
    return bool(payload.get("twins")) or payload.get("status") in _NOT_AN_ANSWER


def _replaced(row: dict) -> bool:
    doc = row.get("doc")
    return bool(row.get("replaced")) or (isinstance(doc, dict) and bool(doc.get("replaced")))


def _temporal(command: str, payload: dict) -> bool:
    return command in ("query vector-text", "query survey", "query chunks") and any(
        _replaced(r) for r in _rows(command, payload))


def _composites(command: str, payload: dict) -> bool:
    return (command == "query text2sql" and not payload.get("dry_run") and bool(_rows(command, payload))
            and reads_mart(payload.get("generated_sql")))


def _conflicts(command: str, payload: dict) -> bool:
    return command == "query graph conflicts" and bool(_rows(command, payload))


def _graph_model(command: str, payload: dict) -> bool:
    if command == "query graph text2cypher":
        return isinstance(payload.get("generated_cypher"), str)
    return command.startswith("query graph pattern") and bool(_rows(command, payload))


def _survey(command: str, payload: dict) -> bool:
    return command in ("query survey", "query topics run") and bool(_rows(command, payload))


_RULES: dict[str, Callable[[str, dict], bool]] = {
    "hubs": _hubs, "resolve": _resolve, "temporal": _temporal, "composites": _composites,
    "conflicts": _conflicts, "graph-model": _graph_model, "survey": _survey,
}
assert tuple(_RULES) == GUIDES


def guides_for(command: str | None, payload: Any) -> list[str]:
    """The reference keys `payload` (printed by `command`, e.g. "query survey") points at; at most two."""
    if not isinstance(command, str) or not isinstance(payload, dict):
        return []
    try:
        return [k for k in GUIDES if _RULES[k](command, payload)][:MAX_GUIDES]
    except Exception:  # a guide is a hint; an odd payload never breaks the command
        return []


def with_guide(command: str | None, payload: Any) -> Any:
    """`payload` with `guide` added when a key applies; a `guide` the command set itself is kept."""
    if not isinstance(payload, dict) or "guide" in payload:
        return payload
    keys = guides_for(command, payload)
    return {**payload, "guide": keys} if keys else payload
