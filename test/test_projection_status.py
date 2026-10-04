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
    """Minimal `tx`-like fake answering exactly the query shapes `status`
    issues: the `:ProjectionState` lookup and the three per-domain gap counts
    (un-embedded entities, un-projected observation keys, un-embedded chunks).
    Anything else raises, so an unexpected query is a loud failure rather than
    a silently-truthy result. Every query is recorded in `queries`."""

    def __init__(self, state: dict | None, unembedded_count: int = 0, *,
                 entity_rows=(), unprojected_rows=(), chunk_rows=None):
        self._state = state
        self.entity_rows = list(entity_rows)
        self.unprojected_rows = list(unprojected_rows)
        if chunk_rows is None:
            chunk_rows = [{"domain": "general", "n": unembedded_count}] if unembedded_count else []
        self.chunk_rows = list(chunk_rows)
        self.queries: list[str] = []

    def run(self, cypher, **params):
        self.queries.append(cypher)
        if "ProjectionState" in cypher:
            rows = [{"p": self._state}] if self._state is not None else []
            return _Result(rows)
        if "MATCH (e:Entity)" in cypher and "embedding" in cypher:
            return _Result(self.entity_rows)
        if "MATCH (o:Observation)" in cypher and "AGGREGATES" in cypher:
            return _Result(self.unprojected_rows)
        if "MATCH (c:DocChunk)" in cypher and "count(c)" in cypher:
            return _Result(self.chunk_rows)
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


# ── B3: what is missing, per domain ─────────────────────────────────────────


def test_status_counts_unembedded_entities_and_unprojected_keys_per_domain():
    from artmind.projection import status

    tx = _FakeProjectionTx(
        state=None,
        entity_rows=[{"domain": "general", "n": 2801}, {"domain": "habits", "n": 1}],
        unprojected_rows=[{"domain": "general", "n": 40}],
        chunk_rows=[{"domain": "habits", "n": 4}, {"domain": None, "n": 2}],
    )

    result = status(tx)

    assert (result["unembedded_entities"], result["unprojected_keys"], result["unembedded_chunks"]) == (2802, 40, 6)
    assert result["domains"] == {
        "(none)": {"unembedded_entities": 0, "unprojected_keys": 0, "unembedded_chunks": 2},
        "general": {"unembedded_entities": 2801, "unprojected_keys": 40, "unembedded_chunks": 0},
        "habits": {"unembedded_entities": 1, "unprojected_keys": 0, "unembedded_chunks": 4},
    }


def test_status_counts_with_the_embed_sweeps_own_predicates():
    """`unembedded_entities` must count exactly what the entity sweep would
    pick up, and an observation is unprojected when no Entity AGGREGATES it --
    a same-as member's observations hang off the canonical's Entity, so
    matching on the key alone would count every merged alias."""
    from artmind.projection import status

    tx = _FakeProjectionTx(state=None)
    status(tx)

    (entity_q,) = [q for q in tx.queries if "MATCH (e:Entity)" in q]
    assert "e.name IS NOT NULL" in entity_q
    assert "e.embedding IS NULL OR e.embedding_stale" in entity_q
    (obs_q,) = [q for q in tx.queries if "MATCH (o:Observation)" in q]
    assert "NOT EXISTS { MATCH (:Entity)-[:AGGREGATES]->(o) }" in obs_q
    assert "count(DISTINCT o.key)" in obs_q
    assert len(tx.queries) == 4, "one state read and three grouped counts, never a query per domain"


def test_drift_is_one_flag(monkeypatch):
    import artmind.projection as projection
    import artmind.same_as as same_as

    monkeypatch.setattr(same_as, "content_hash", lambda path=None: "a")
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "b")

    assert projection.status(_FakeProjectionTx(state=None))["drift"] is True
    assert projection.status(_FakeProjectionTx(state={"same_as_hash": "a", "schema_hash": "b"}))["drift"] is False
    assert projection.status(_FakeProjectionTx(state={"same_as_hash": "x", "schema_hash": "b"}))["drift"] is True
