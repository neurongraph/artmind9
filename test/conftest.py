"""Suite-wide hermeticity guard: no test may reach a live Neo4j.

The test suite's own contract (docs/superpowers/plans/2026-07-23-structured-
data-ingestion-plan.md, "Tests are hermetic ... no Neo4j, no network") is
normally upheld per-test, by monkeypatching whichever module-local name a
test's code path resolves ``neo4j_session`` through. That works fine for
tests written *with* a given hook in mind, but it silently stops working the
moment a hook is added deeper in a call path some other, older test already
exercises unmocked — e.g. ``ingest_structured_file``/``refresh_table``
gained a ``project_catalogue()`` call (a real ``DETACH DELETE`` + ``MERGE``)
that dozens of pre-existing fixtures never anticipated. On a dev machine with
a real Neo4j reachable at the configured URI, that is a silent, permanent,
unrecoverable data loss (Neo4j writes have no tmp_path-style teardown).

This autouse, session-independent fixture makes that class of bug structurally
impossible by default: it intercepts the connection primitive itself before
any test body runs, for every test, regardless of whether that test's author
knew a Neo4j-touching call was reachable.

Two separate patch targets are required, not one, because ``neo4j_session``
is captured by ``from artmind.graph_query import neo4j_session`` in multiple
modules — each such import binds its own local name in that module's
namespace, independent of ``artmind.graph_query.neo4j_session`` itself:

- ``artmind.graph_query.neo4j_session`` — patching the origin module also
  covers every caller that resolves ``neo4j_session`` via graph_query's own
  module globals at call time rather than a separate import, which includes
  ``read_session()`` and therefore ``entity_listing()`` (the read path behind
  ``structured/mappings.py``'s ``propose_mappings`` hook).
- ``artmind.structured.catalogue.neo4j_session`` — ``catalogue.py`` imports
  the name directly, so it needs its own patch (mirrors the existing
  per-test pattern in test_structured_catalogue.py).

Individual tests that want to assert on the calls made (e.g.
test_structured_catalogue.py) still monkeypatch these same targets
themselves within the test body — that's fine and takes precedence, since it
runs after (and un-does, for that one test, via the same function-scoped
``monkeypatch`` fixture) this fixture's default stub.
"""

import pytest


class _NullResult:
    """Stand-in for a neo4j Result — nothing in this codebase's write paths
    reads a ``session.run(...)`` return value, and the read paths
    (``_run_read_query``) only ever iterate it, so an empty list already
    satisfies both without needing a richer fake here."""

    def data(self):
        return []


class _NullSession:
    def run(self, cypher, **params):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _null_neo4j_session(*args, **kwargs):
    return _NullSession()


@pytest.fixture(autouse=True)
def _no_live_neo4j(monkeypatch):
    import artmind.graph_query as graph_query

    monkeypatch.setattr(graph_query, "neo4j_session", _null_neo4j_session)

    try:
        import artmind.structured.catalogue as catalogue
    except ImportError:
        # openpyxl (or another structured-store dependency) may be absent in
        # a minimal test environment; the structured suite already skips
        # itself via pytest.importorskip in that case.
        catalogue = None
    if catalogue is not None:
        monkeypatch.setattr(catalogue, "neo4j_session", _null_neo4j_session)

    yield
