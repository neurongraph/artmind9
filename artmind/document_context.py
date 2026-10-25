"""The document context every chunk extraction prompt carries.

Chunks extract in isolation; an entity whose meaning depends on the whole
document is named differently in each chunk unless each chunk can see what the
document is. This module decides WHAT that context says: the document's
metadata, its heading outline with the chunk's position, and a summary. It does
no I/O and calls no LLM -- `ingest.extract_kg` does both and passes the results
in. Schema-agnostic: nothing here names a domain.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The block's length cap in characters. Only the legacy `document_header: {chars: N}` setting changes it; `document_context` has no cap key.
DEFAULT_CAP = 2000


@dataclass(frozen=True)
class ContextSettings:
    enabled: bool
    summary: bool
    focus: str
    cap: int


_OFF = ContextSettings(enabled=False, summary=False, focus="", cap=DEFAULT_CAP)


def context_settings(schema: dict) -> ContextSettings:
    """What this schema asks for. `document_context` is the key; the older
    `document_header` (commit cf787bd) is read when `document_context` is absent:
    `false` turns the block off, `{chars: N}` sets the cap. Only an explicit
    `false` turns the summary off."""
    if "document_context" in schema:
        setting = schema["document_context"]
        if setting is False:
            return _OFF
        if isinstance(setting, dict):
            focus = setting.get("focus")
            return ContextSettings(
                enabled=True,
                summary=setting.get("summary", True) is not False,
                focus=focus.strip() if isinstance(focus, str) else "",
                cap=DEFAULT_CAP,
            )
        return ContextSettings(enabled=True, summary=True, focus="", cap=DEFAULT_CAP)

    legacy = schema.get("document_header", True)
    if legacy is False:
        return _OFF
    cap = DEFAULT_CAP
    if isinstance(legacy, dict):
        try:
            cap = max(int(legacy.get("chars", DEFAULT_CAP)), 0)
        except (TypeError, ValueError):
            cap = DEFAULT_CAP
    return ContextSettings(enabled=True, summary=True, focus="", cap=cap)
