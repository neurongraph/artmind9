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


def provenance_card(
    domains: list[str],
    *,
    entity_id: str | None = None,
    reference: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """A ``provenance`` Card spec — contract: ``{domains, entityId?, reference?, title?}``.

    Shows where a fact came from (source docs/chunks). Supply ``entity_id`` when
    known (skips resolve) or a free-text ``reference`` to resolve first (ADR 0014).
    """
    props: dict[str, Any] = {"domains": domains}
    if entity_id is not None:
        props["entityId"] = entity_id
    if reference is not None:
        props["reference"] = reference
    if title is not None:
        props["title"] = title
    return {"cardType": "provenance", "props": props}


def placement_card(
    vault_path: str,
    *,
    domains: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """A ``placement`` Card spec — contract: ``{vaultPath, domains?, title?}``.

    The propose→review→confirm surface (ADR 0012): the Card fetches an A6
    placement proposal for the document at ``vaultPath``, renders it for the user
    to accept/adjust, and on confirm writes the accepted filing facets into the
    doc's frontmatter (doc-first, ADR 0002). ``domains`` optionally scopes the
    proposal. Never a silent auto-file.
    """
    props: dict[str, Any] = {"vaultPath": vault_path}
    if domains:
        props["domains"] = domains
    if title is not None:
        props["title"] = title
    return {"cardType": "placement", "props": props}


def micro_ui_card(html: str, *, title: str | None = None) -> dict[str, Any]:
    """A ``micro-ui`` Card spec — tier (c), arbitrary agent-authored HTML/JS.

    The ``html`` is dropped into a **sandboxed iframe** (``srcdoc`` + strict
    ``sandbox``) on the client, isolated from the app DOM and data (ADR 0014).
    Its only contract is "sandboxed HTML" — deliberately schema-free. We carry
    the markup on ``props.html`` (not a top-level ``card.html``) so it rides the
    same declarative props path as every other Card and round-trips through board
    persistence unchanged.
    """
    props: dict[str, Any] = {"html": html}
    if title is not None:
        props["title"] = title
    return {"cardType": "micro-ui", "props": props}
