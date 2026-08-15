"""The canvas backend's render-event contract — a superset of artmind's 7
neutral UI events (``artmind.webui.backends.base``) with one addition: ``render``.

The 7 trace events are string-only and clipped to 600 chars (``base.clip``).
A ``render`` event is different: it carries a structured, *unclipped* card spec
telling the client to spawn/update a Card on the Canvas. Two shapes (ADR 0014):

    {"type": "render", "card": {"cardType": "<type>", "props": {...}}}   # declarative
    {"type": "render", "card": {"cardType": "micro-ui", "html": "<...>"}}  # tier c (Phase 5)

Each first-class ``cardType`` has a client-owned *Card contract* (the shape of
``props``); the backend only has to emit conforming payloads. Phase 0 emits just
the declarative ``document`` card.
"""

from typing import Any

RENDER = "render"


def render_event(card: dict[str, Any]) -> dict[str, Any]:
    """Wrap a card spec in a ``render`` UI event."""
    return {"type": RENDER, "card": card}


def document_card(vault_path: str, block_id: str | None = None) -> dict[str, Any]:
    """A ``document`` Card spec — contract: ``{vaultPath, blockId?}`` (ADR 0014)."""
    props: dict[str, Any] = {"vaultPath": vault_path}
    if block_id is not None:
        props["blockId"] = block_id
    return {"cardType": "document", "props": props}
