"""Assert the Cypher and params actually SENT (pattern: test_kg_views_runner.py)."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable

from artmind.insights.model import InsightsError
from artmind.insights.projections import PROJECTIONS, Projector
from artmind.text2cypher import validate_domain_scoped, validate_read_only


class Recorder:
    def __init__(self, rows=(), raises=None):
        self.calls = []
        self.rows, self.raises = list(rows), raises

    def factory(self):
        @contextmanager
        def session_cm():
            session = MagicMock()
            session.run.side_effect = self._run
            yield session
        return session_cm

    def _run(self, query, parameters=None, **kwargs):
        self.calls.append((getattr(query, "text", query), parameters, getattr(query, "timeout", None)))
        if self.raises:
            raise self.raises
        result = MagicMock()
        result.__iter__.return_value = iter(self.rows)
        return result


@pytest.mark.parametrize("name", sorted(PROJECTIONS))
def test_every_projection_is_read_only_and_domain_scoped(name):
    validate_read_only(PROJECTIONS[name])
    validate_domain_scoped(PROJECTIONS[name])


def test_projector_sends_domains_params_and_timeout():
    rec = Recorder(rows=[{"_id": "e1"}])
    p = Projector(["demo", "demo.sub"], session_factory=rec.factory())
    assert p.rows("relation_triples", max_targets=10) == [{"_id": "e1"}]
    cypher, params, timeout = rec.calls[0]
    assert cypher == PROJECTIONS["relation_triples"]
    assert params == {"max_targets": 10, "domains": ["demo", "demo.sub"]}
    assert timeout == 60


def test_projector_memoises_per_name_and_params():
    rec = Recorder(rows=[{"x": 1}])
    p = Projector(["demo"], session_factory=rec.factory())
    p.rows("documents")
    p.rows("documents")
    p.rows("relation_triples", max_targets=5)
    assert len(rec.calls) == 2


def test_projector_wraps_driver_errors():
    rec = Recorder(raises=ServiceUnavailable("down"))
    with pytest.raises(InsightsError, match="projection documents failed"):
        Projector(["demo"], session_factory=rec.factory()).rows("documents")


def test_open_conflicts_counts_distinct_conflicts():
    # A Conflict has CONFLICT_OF edges to both entities of a pair: count nodes, not edges.
    assert "count(DISTINCT c)" in PROJECTIONS["open_conflicts"]
    assert "count(*)" not in PROJECTIONS["open_conflicts"]


def test_sampled_lists_are_ordered_before_collection():
    stats = PROJECTIONS["property_stats"]
    assert "ORDER BY sv" in stats
    assert stats.index("ORDER BY sv") < stats.index("collect(DISTINCT sv)")
    triples = PROJECTIONS["relation_triples"]
    assert "WITH a, r, b ORDER BY b._id" in triples
    assert triples.index("WITH a, r, b ORDER BY b._id") < triples.index("collect(DISTINCT b._id)")


def test_unknown_projection_and_empty_domains():
    with pytest.raises(InsightsError):
        Projector(["demo"], session_factory=Recorder().factory()).rows("nope")
    with pytest.raises(InsightsError):
        Projector([])
