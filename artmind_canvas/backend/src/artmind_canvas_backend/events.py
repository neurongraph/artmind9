"""Change-event broadcast broker (ADR 0008).

When knowledge changes — today, a Vault write through the editor; later, an ingest
completing — the canvas backend **broadcasts a change event** so every affected Card
can re-query its own scoped slice rather than the client polling or reloading the whole
graph. This is the backend half of that: a tiny in-process pub/sub fanning one event out
to every connected SSE subscriber.

Single-process, single-user, local (consistent with the board store): a set of asyncio
queues is all the machinery this needs. The event payload carries *what changed* (a
resource kind + identifiers like document/domain/entity) so each Card decides on the
client whether it is affected — the backend does not track who is showing what.
"""

from __future__ import annotations

import asyncio


class EventBroker:
    """Fans a published event out to every current subscriber's queue."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        # Non-blocking fan-out; queues are unbounded so a slow client can't stall a write.
        for q in list(self._subscribers):
            q.put_nowait(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton: the vault route publishes to it, the events route streams from
# it. Both import this same instance (single process, so this is the shared bus).
broker = EventBroker()


def publish_change(resource: str, **fields: object) -> None:
    """Publish a ``change`` event describing what changed (ADR 0008).

    ``resource`` is the kind (e.g. ``"document"``); ``fields`` are the identifiers a Card
    matches on (``path`` for a document; ``domain``/``entity`` once graph Cards exist).
    """
    broker.publish({"type": "change", "resource": resource, **fields})
