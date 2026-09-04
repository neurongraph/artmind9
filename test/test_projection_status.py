"""`projection.status`'s `unembedded_chunks` key (docs/vault.md, "Embeddings").

`unembedded_chunks` is hand-copied into both of `status`'s return sites (the
early `known: False` dict and the main `known: True` dict) -- exactly the
kind of place a copy-paste slip (wrong key, stale query, forgotten branch)
hides silently, since both branches otherwise look plausible on their own.
"""
from __future__ import annotations


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeProjectionTx:
    """Minimal `tx`-like fake answering exactly the two query shapes
    `status` issues: the `:ProjectionState` lookup (`read_state`) and the
    unembedded-chunk count. Anything else raises, so a query this test
    doesn't expect is a loud failure rather than a silently-truthy result."""

    def __init__(self, state: dict | None, unembedded_count: int):
        self._state = state
        self._unembedded_count = unembedded_count

    def run(self, cypher, **params):
        if "ProjectionState" in cypher:
            rows = [{"p": self._state}] if self._state is not None else []
            return _Result(rows)
        if "DocChunk" in cypher and "count(c)" in cypher:
            return _Result([{"n": self._unembedded_count}])
        raise AssertionError(f"projection.status issued an unexpected query: {cypher!r}")


def test_unembedded_chunks_reported_when_projection_state_is_unknown():
    """The `known: False` branch -- no `:ProjectionState` node yet (a fresh
    graph, or one that predates the drift check)."""
    from artmind.projection import status

    tx = _FakeProjectionTx(state=None, unembedded_count=7)

    result = status(tx)

    assert result["known"] is False
    assert result["unembedded_chunks"] == 7


def test_unembedded_chunks_reported_when_projection_state_is_known():
    """The `known: True` branch -- a recorded `:ProjectionState`."""
    from artmind.projection import status

    tx = _FakeProjectionTx(
        state={
            "last_rebuilt_at": "2026-01-01T00:00:00Z",
            "same_as_hash": "abc",
            "schema_hash": "def",
        },
        unembedded_count=0,
    )

    result = status(tx)

    assert result["known"] is True
    assert result["unembedded_chunks"] == 0


def test_unembedded_chunks_value_differs_between_the_two_branches():
    """A cheap guard against the copy-paste slip: same fake, only `known`
    and the count differ, so a stale literal in one branch shows up as a
    wrong number rather than an omitted key."""
    from artmind.projection import status

    unknown = status(_FakeProjectionTx(state=None, unembedded_count=3))
    known = status(_FakeProjectionTx(state={"same_as_hash": "a", "schema_hash": "b"}, unembedded_count=9))

    assert unknown["unembedded_chunks"] == 3
    assert known["unembedded_chunks"] == 9
