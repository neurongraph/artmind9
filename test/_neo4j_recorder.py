# test/_neo4j_recorder.py
"""A fake Neo4j session/transaction that records every query.

Tests assert on `calls` (cypher, params): which query ran and what it was sent.
`respond(cypher, params)` returns the rows for one call (default: none).
"""


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class Recorder:
    def __init__(self, respond=None):
        self.calls: list[tuple[str, dict]] = []
        self._respond = respond or (lambda cypher, params: [])

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return FakeResult(self._respond(cypher, params))

    def queries_containing(self, fragment: str) -> list[tuple[str, dict]]:
        return [(c, p) for c, p in self.calls if fragment in c]

    # context-manager form so it can stand in for `neo4j_session()` / `read_session()`
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
