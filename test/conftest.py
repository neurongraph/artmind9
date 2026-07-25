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


def _refuse_llm_call(model, prompt):
    raise RuntimeError(
        "A test reached artmind.extraction.call_llm. The suite is hermetic — no "
        "network, no local model. Stub the call in the test itself if the path "
        "under test needs one."
    )


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """Third instance of the hazard ``_no_live_neo4j`` guards, for the LLM.

    The structured ingest pipeline was LLM-free until ``pipeline.py`` gained a
    ``propose_semantics()`` hook. Every pre-existing fixture that ingests a CSV
    (test_db_cli.py, test_structured_*.py) suddenly reached a real ollama call
    and blocked on ARTMIND_OLLAMA_TIMEOUT — the suite went from ~6s to hanging.
    Exactly the drift this file's opening docstring describes: a hook added deep
    in a call path that older tests already exercise unmocked.

    Raising rather than returning a canned string is deliberate: an accidental
    LLM dependency should be loud where nothing catches it, and the ingest hook's
    own try/except already treats an unreachable model as a non-fatal warning,
    which is the behaviour we want to exercise anyway.

    Only ``artmind.extraction.call_llm`` is patched. Modules that bind the name
    at import (``from artmind.extraction import call_llm``, as text2sql does)
    keep their own reference and their existing per-test stubs still apply;
    callers that resolve it at call time — semantics.py — get this guard.
    """
    import artmind.extraction as extraction

    monkeypatch.setattr(extraction, "call_llm", _refuse_llm_call)
    yield


@pytest.fixture(autouse=True)
def _no_live_registry_db(tmp_path, monkeypatch):
    """Same hazard as ``_no_live_neo4j`` below, for the SQLite registry.

    ``artmind.db`` resolves ``DB_PATH`` from ``paths`` at import, defaulting to
    ``$ARTMIND_DATA_DIR/document_registry.db`` — a real user's data. Most tests
    monkeypatch ``db.DB_PATH`` themselves, but that is per-test discipline, and
    a test that merely imports ``_get_db`` and calls it (test_db_update.py did
    exactly this) silently reads and WRITES the developer's live registry.
    Observed on this machine: 5 bogus update_sessions and 465 update_drafts
    rows ("Alice is the CEO", alice@example.com), three more appended per run
    since 2026-07-16.

    Redirecting the module global for every test makes the whole class
    impossible. Tests that set ``db.DB_PATH`` themselves still win: the same
    function-scoped ``monkeypatch`` applies their value after this fixture's.
    """
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_registry.db")
    yield


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
