"""The ``show_card`` in-process agent tool (Claude-SDK-only, ADR 0014, Phase C).

Lets the canvas agent spawn a Card on the user's Canvas mid-answer — e.g. when
asked where a fact came from, to see an entity's connections, or to open a
document. It is a thin dispatcher: it validates the model's args and reuses the
**same** ``render_events`` builders the canned ``/…-test`` hooks use, so the
spawned Card self-fetches live data via the client ``/api/*`` endpoints. No graph
logic lives here (or anywhere in the canvas) — the pure-client rule is intact.

Seam: the tool's *return value* goes to the model, not the SSE stream. So the
handler **enqueues** a ``render`` event onto a queue owned by the
``CanvasBackend`` instance; ``CanvasBackend.receive_events`` drains it into the
neutral event stream. The harness ``EventMapper`` and its 7-event contract are
untouched — ``render`` stays the canvas superset, and the queued spec is the
structured, unclipped card (not the ``clip()``-truncated tool input).

``micro-ui`` is deliberately excluded: agent-authored sandboxed HTML is a
separate tier the agent would author inline, not a declarative card spec.
"""

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from artmind_canvas_backend.render_events import (
    document_card,
    graph_view_card,
    placement_card,
    provenance_card,
    render_event,
)

TOOL_NAME = "show_card"
# Fully-qualified name the SDK exposes for an in-process MCP server named
# ``canvas`` — what ``allowed_tools`` and the trace drawer reference.
QUALIFIED_TOOL_NAME = "mcp__canvas__show_card"

SHOW_CARD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cardType": {
            "type": "string",
            "enum": ["provenance", "graph-view", "document", "placement"],
            "description": (
                "Which Card to open. 'provenance' = where a fact came from "
                "(source docs/chunks); 'graph-view' = an entity's one-hop "
                "neighbourhood; 'document' = a Vault markdown file; 'placement' "
                "= the propose→review→confirm filing surface for a document."
            ),
        },
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Domain(s) to scope the lookup. Required for 'provenance' and "
                "'graph-view'; optional context for 'placement'; unused for "
                "'document'."
            ),
        },
        "reference": {
            "type": "string",
            "description": (
                "Free-text name of the entity to resolve (e.g. 'Acme Corp'). "
                "Use for 'provenance'/'graph-view' when you don't have an id."
            ),
        },
        "entityId": {
            "type": "string",
            "description": (
                "Known entity id (skips resolution). Alternative to 'reference' "
                "for 'provenance'/'graph-view'."
            ),
        },
        "vaultPath": {
            "type": "string",
            "description": (
                "Vault-relative path of the document. Required for 'document' "
                "and 'placement'."
            ),
        },
        "title": {
            "type": "string",
            "description": "Optional heading override for the Card.",
        },
    },
    "required": ["cardType"],
}


def _build_card(
    card_type: str,
    domains: list[str],
    reference: str | None,
    entity_id: str | None,
    vault_path: str | None,
    title: str | None,
) -> dict[str, Any]:
    """Validate args per ``cardType`` and build the card spec, or raise ``ValueError``.

    Requirements are per-type rather than a blanket schema ``required`` because
    the four card types key on different selectors (entity vs. Vault path).
    """
    if card_type in ("provenance", "graph-view"):
        if not domains:
            raise ValueError(f"{card_type} card requires 'domains'")
        if not reference and not entity_id:
            raise ValueError(
                f"{card_type} card requires 'reference' (free text) or 'entityId'"
            )
        builder = provenance_card if card_type == "provenance" else graph_view_card
        return builder(domains, entity_id=entity_id, reference=reference, title=title)
    if card_type == "document":
        if not vault_path:
            raise ValueError("document card requires 'vaultPath'")
        return document_card(vault_path)
    if card_type == "placement":
        if not vault_path:
            raise ValueError("placement card requires 'vaultPath'")
        return placement_card(vault_path, domains=domains or None, title=title)
    raise ValueError(f"unknown cardType: {card_type!r}")


def make_show_card_tool(queue: "asyncio.Queue[dict[str, Any]]") -> SdkMcpTool[Any]:
    """Build the ``show_card`` SDK tool bound to a ``CanvasBackend``'s render queue."""

    @tool(
        TOOL_NAME,
        "Open a Card on the user's spatial Canvas: provenance (where a fact came "
        "from), graph-view (an entity's connections), document (a Vault file), or "
        "placement (file a document). The Card fetches its own live data. Keep "
        "answering in prose — the Card complements your text, it does not replace it.",
        SHOW_CARD_INPUT_SCHEMA,
    )
    async def show_card(args: dict[str, Any]) -> dict[str, Any]:
        card_type = args.get("cardType")
        domains = args.get("domains") or []
        reference = args.get("reference")
        entity_id = args.get("entityId")
        vault_path = args.get("vaultPath")
        title = args.get("title")
        try:
            card = _build_card(
                card_type, domains, reference, entity_id, vault_path, title
            )
        except ValueError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
        await queue.put(render_event(card))
        label = title or reference or entity_id or vault_path or card_type
        return {
            "content": [
                {"type": "text", "text": f"Opened a {card_type} card for {label!r}."}
            ]
        }

    return show_card
