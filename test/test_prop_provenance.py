"""A1e — property provenance ledger.

Each shared Entity keeps its clean merged property value materialized exactly as
a plain ingest would (``"A | B"``), plus a ``_prop_sources`` JSON ledger recording
which document contributed which raw value. Ingest adds a document's contribution;
a later purge (A1d) can drop it and recompute the clean value by folding the
remaining ledger. These tests exercise the fold semantics, the ``_upsert_entity``
rework, the single strip point, and the entity_history exclusion — asserting on
the props/ledger actually written, never on summary counts (per CLAUDE.md).
"""
import json

import artmind.ingest as ing
from artmind.graph_query import strip_internal_props


# ── ledger primitives (pure) ────────────────────────────────────────────────

def test_fold_reproduces_accretive_string_merge():
    # Two docs contributing a string fold to today's "A | B" append result.
    assert ing._fold_ledger([["docA", "desc A"], ["docB", "desc B"]]) == "desc A | desc B"


def test_fold_unions_list_contributions():
    assert ing._fold_ledger([["docA", ["x"]], ["docB", ["y", "x"]]]) == ["x", "y"]


def test_fold_of_empty_ledger_is_none():
    # An emptied ledger => the property should be removed from the node.
    assert ing._fold_ledger([]) is None


def test_fold_keeps_sole_legacy_contribution():
    # A property whose only remaining source is the un-attributable legacy base
    # survives — purge must never drop it.
    assert ing._fold_ledger([["__legacy__", "keep me"]]) == "keep me"


def test_dropping_a_source_recomputes_the_clean_value():
    # Simulates A1d's per-document rollback at the ledger level.
    contributions = [["docA", "A"], ["docB", "B"]]
    after_drop_a = [e for e in contributions if e[0] != "docA"]
    assert ing._fold_ledger(after_drop_a) == "B"
    after_drop_all = [e for e in after_drop_a if e[0] != "docB"]
    assert ing._fold_ledger(after_drop_all) is None


def test_ledger_upsert_is_idempotent_per_document():
    ledger: dict = {}
    ing._ledger_upsert(ledger, "description", "docA", "v1")
    ing._ledger_upsert(ledger, "description", "docB", "v2")
    # Re-feeding docA replaces its contribution in place, never appends a second.
    ing._ledger_upsert(ledger, "description", "docA", "v1-edited")
    assert ledger["description"] == [["docA", "v1-edited"], ["docB", "v2"]]


def test_parse_prop_sources_tolerates_absent_and_malformed():
    assert ing._parse_prop_sources(None) == {}
    assert ing._parse_prop_sources("") == {}
    assert ing._parse_prop_sources("not json") == {}
    assert ing._parse_prop_sources("[1,2]") == {}  # JSON, but not a dict
    assert ing._parse_prop_sources('{"description": [["d", "v"]]}') == {
        "description": [["d", "v"]]
    }


# ── _upsert_entity end-to-end against a stateful fake session ────────────────

class _Result:
    def __init__(self, node):
        self._node = node

    def single(self):
        return {"p": dict(self._node)} if self._node is not None else None


class _StatefulSession:
    """Holds a single entity's props, so sequential upserts see each other's
    writes — the minimum needed to exercise merge-onto-existing and re-ingest."""

    def __init__(self, node=None):
        self.node = dict(node) if node is not None else None
        self.writes: list = []

    def run(self, cypher, **kw):
        if "properties(n) AS p" in cypher:
            return _Result(self.node)
        props = dict(kw["props"])
        kind = "CREATE" if cypher.lstrip().startswith("CREATE") else "SET"
        self.node = props
        self.writes.append((kind, props))
        return _Result(None)


def _ent(**over):
    base = {"name": "Bank", "entity_class": "ORG", "domain": "banking"}
    base.update(over)
    return base


def test_create_records_the_contributing_document():
    sess = _StatefulSession()
    ing._upsert_entity(sess, _ent(description="desc A"), None, doc_id="docA")
    assert sess.writes[0][0] == "CREATE"
    assert sess.node["description"] == "desc A"
    ledger = json.loads(sess.node["_prop_sources"])
    assert ledger["description"] == [["docA", "desc A"]]


def test_identity_props_are_never_tracked_in_the_ledger():
    sess = _StatefulSession()
    ing._upsert_entity(sess, _ent(description="d"), {"fee": "10"}, doc_id="docA")
    ledger = json.loads(sess.node["_prop_sources"])
    # Only accretive props (description + domain props) are attributed; fixed
    # identity keys are not.
    assert set(ledger) == {"description", "fee"}
    for identity_key in ("name", "entity_class", "domain", "id"):
        assert identity_key not in ledger


def test_second_document_merges_value_and_appends_to_ledger():
    sess = _StatefulSession()
    ing._upsert_entity(sess, _ent(description="desc A"), None, doc_id="docA")
    ing._upsert_entity(sess, _ent(description="desc B"), None, doc_id="docB")
    assert sess.writes[1][0] == "SET"
    assert sess.node["description"] == "desc A | desc B"
    ledger = json.loads(sess.node["_prop_sources"])
    assert ledger["description"] == [["docA", "desc A"], ["docB", "desc B"]]


def test_reingesting_same_document_is_idempotent():
    sess = _StatefulSession()
    ing._upsert_entity(sess, _ent(description="desc A"), None, doc_id="docA")
    ing._upsert_entity(sess, _ent(description="desc B"), None, doc_id="docB")
    # docA re-ingested unchanged: no double-append, clean value unchanged.
    ing._upsert_entity(sess, _ent(description="desc A"), None, doc_id="docA")
    ledger = json.loads(sess.node["_prop_sources"])
    assert ledger["description"] == [["docA", "desc A"], ["docB", "desc B"]]
    assert sess.node["description"] == "desc A | desc B"


def test_pre_ledger_node_seeds_a_legacy_base_on_first_touch():
    # An entity written before A1e has a clean value but no _prop_sources.
    sess = _StatefulSession(
        node=_ent(description="legacy value", id="phys-1")
    )
    ing._upsert_entity(sess, _ent(description="fresh"), None, doc_id="docC")
    ledger = json.loads(sess.node["_prop_sources"])
    assert ledger["description"] == [["__legacy__", "legacy value"], ["docC", "fresh"]]
    # Clean value still reproduces the accretive append, preserving old data.
    assert sess.node["description"] == "legacy value | fresh"
    # The pre-existing physical id is preserved, not regenerated.
    assert sess.node["id"] == "phys-1"


def test_falsy_doc_id_maps_to_unknown_source_not_legacy():
    # A write with no doc_id must not masquerade as the never-purged legacy source.
    sess = _StatefulSession()
    ing._upsert_entity(sess, _ent(description="d"), None, doc_id=None)
    ledger = json.loads(sess.node["_prop_sources"])
    assert ledger["description"] == [["__unknown__", "d"]]


# ── single strip point ───────────────────────────────────────────────────────

def test_strip_internal_props_drops_ledger_and_embedding():
    record = {
        "e": {
            "id": "x",
            "labels": ["Entity"],
            "properties": {
                "name": "Bank",
                "description": "d",
                "embedding": [0.1, 0.2],
                "_prop_sources": '{"description": [["docA", "d"]]}',
            },
        }
    }
    out = strip_internal_props(record)
    props = out["e"]["properties"]
    assert props == {"name": "Bank", "description": "d"}


def test_strip_internal_props_recurses_into_lists():
    out = strip_internal_props([{"embedding": [1], "keep": 1}, {"_prop_sources": "{}", "k": 2}])
    assert out == [{"keep": 1}, {"k": 2}]


# ── entity_history exclusion (defensive) ─────────────────────────────────────

def test_entity_history_capture_excludes_prop_sources(tmp_path):
    from artmind import entity_history as eh

    (tmp_path / "entities.json").write_text(
        json.dumps([{"id": "e1", "name": "Bank", "entity_class": "ORG", "domain": "banking"}]),
        encoding="utf-8",
    )
    (tmp_path / "properties.json").write_text(
        json.dumps([{"id": "e1", "properties": {"fee": "10", "_prop_sources": "{}"}}]),
        encoding="utf-8",
    )
    rows = eh._staged_assertions(tmp_path, "banking")
    assert rows, "expected one staged identity row"
    assert rows[0]["keys"] == ["fee"]
