"""The projection rebuild, against a REAL Neo4j.

Opt in with `ARTMIND_TEST_LIVE_NEO4J=1`, so the default suite stays hermetic:

    ARTMIND_TEST_LIVE_NEO4J=1 pytest test/test_projection_live.py

The connection is **artmind's own** (`graph_query.neo4j_session`), so it uses
whatever `ARTMIND_KG_NEO4J_*` in `~/.artmind/.env` points at — local Neo4j,
Docker, or AuraDB — with the right scheme and credentials already applied. An
earlier version opened a bare `GraphDatabase.driver(uri)` with no auth, which
could only ever have worked against an unauthenticated local instance.

**These tests write to that database.** Everything they create lives under the
`test.projection` domain and is deleted before and after each test; nothing
matches on a bare label or a null domain, so a real corpus in the same database
is not touched. Even so, prefer a scratch instance if you have one.

These assertions exist because a mocked session cannot make them. A bare
`MagicMock()` returns a truthy result for any Cypher, so it reports success
identically whether a query matched the right node, the wrong node, or
nothing at all — the failure mode that hid the `update confirm` defect. Every
check below reads the graph back.
"""
import os

import pytest

from artmind.observations import aggregate_key, entity_id, key_string
from artmind.projection import full_rebuild, rebuild

# `conftest._no_live_neo4j` is an autouse fixture that replaces
# `graph_query.neo4j_session` with a null session for EVERY test, so the
# hermetic suite can never accidentally reach a database. That guard is right
# and stays — this module is the one place that deliberately opts out.
#
# Binding the function here, at import time, is what opts out: the autouse
# fixture patches the attribute on the module object, per test, long after this
# import has already captured the real callable. Re-importing it inside the
# fixture instead would pick up the null session and every test would silently
# skip on "APOC missing" — which is exactly what happened the first time.
from artmind.graph_query import neo4j_session as _live_neo4j_session

pytestmark = pytest.mark.skipif(
    os.environ.get("ARTMIND_TEST_LIVE_NEO4J") != "1",
    reason="set ARTMIND_TEST_LIVE_NEO4J=1 to run against the configured Neo4j",
)

DOMAIN = "test.projection"
CLASS = "RATE_ENTRY"
# Scopes every conflict this module creates or deletes. Without it the cleanup
# would have to match conflicts by a null domain, which on a real graph means
# the pairwise adjudicator's own nodes.
CONFLICT_TAG = "phase3-live-test"

REQUIRED_APOC = (
    "apoc.create.addLabels",
    "apoc.create.removeProperties",
    "apoc.merge.relationship",
)


@pytest.fixture()
def session():
    from artmind.setup import _setup_neo4j

    with _live_neo4j_session() as s:
        missing = _missing_apoc(s)
        if missing:
            pytest.skip(
                "the projection rebuild needs these APOC procedures, and this "
                f"database does not expose them: {', '.join(missing)}"
            )
        # The REAL schema, so these tests run against the same constraints and
        # indexes production does — including the Observation.id uniqueness
        # constraint, which is what makes a duplicate write fail loudly here.
        _setup_neo4j(s, 768)
        _clean(s)
        yield s
        _clean(s)


def _missing_apoc(s) -> list[str]:
    """Which required APOC procedures this database lacks.

    AuraDB exposes a curated subset of APOC, so this is a real possibility
    rather than a theoretical one — and a missing procedure would otherwise
    surface as an opaque `ProcedureNotFound` in the middle of a rebuild.
    """
    try:
        available = {r["name"] for r in s.run("SHOW PROCEDURES YIELD name RETURN name").data()}
    except Exception:
        return []  # can't introspect — let the tests fail with the real error
    return [p for p in REQUIRED_APOC if p not in available]


def _clean(s):
    """Delete only what this module created.

    Every match is scoped to `test.projection` or to this module's own conflict
    tag. Nothing here matches a bare label or a null property, because this may
    be running against a database that holds a real corpus.
    """
    s.run("MATCH (n:Observation {domain: $d}) DETACH DELETE n", d=DOMAIN).consume()
    s.run("MATCH (n:Entity {domain: $d}) DETACH DELETE n", d=DOMAIN).consume()
    s.run("MATCH (c:Conflict {domain: $d}) DETACH DELETE c", d=DOMAIN).consume()
    s.run("MATCH (c:Conflict {_test: $tag}) DETACH DELETE c", tag=CONFLICT_TAG).consume()


def write_observation(session, **kw):
    props = {
        "entity_class": CLASS,
        "domain": DOMAIN,
        "_status": "latest",
        "_kind": "recurrent",
        "doc_version": 1,
    }
    props.update(kw)
    props["key"] = key_string(aggregate_key(props["canonical_name"], props["entity_class"], props["domain"]))
    session.run("CREATE (o:Observation) SET o = $props", props=props).consume()
    return props


TIER2 = "SmartSaver Account Tier 2 Rate"
TIER2_KEY = aggregate_key(TIER2, CLASS, DOMAIN)


def seed_three_schedules(session):
    """The vertical slice's shape: three documents, every one at version 1,
    three different effective dates, one aggregate."""
    write_observation(
        session, id="obs-jan", name=f"{TIER2} — 4.70% AER (£10,001–£50,000), effective 2026-01-15",
        canonical_name=TIER2, doc_id="doc-jan", chunk_id="doc-jan_001",
        _doc_valid_from="2026-01-15", _valid_from="2026-01-15",
        rate_value=4.70, rate_format="AER", description="January rate.",
    )
    write_observation(
        session, id="obs-feb", name="£10,001–£50,000",
        canonical_name=TIER2, doc_id="doc-feb", chunk_id="doc-feb_001",
        _doc_valid_from="2026-02-01", _valid_from="2026-02-01",
        rate_value=4.60, rate_format="AER", description="February rate.",
    )
    write_observation(
        session, id="obs-mar", name="SmartSaver Tier 2",
        canonical_name=TIER2, doc_id="doc-mar", chunk_id="doc-mar_001",
        _doc_valid_from="2026-03-01", _valid_from="2026-03-01",
        rate_value=4.50, rate_format="AER", description="March rate.",
    )


# ── the vertical slice, live ────────────────────────────────────────────────


def test_three_observations_project_to_one_entity_holding_the_march_rate(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    rows = session.run(
        "MATCH (e:Entity {domain: $d}) RETURN e.name AS name, e.rate_value AS rate, "
        "e._temporal_props AS temporal, e.id AS id", d=DOMAIN,
    ).data()
    assert len(rows) == 1, "exactly one Entity for the aggregate key"
    assert rows[0]["name"] == TIER2
    assert rows[0]["rate"] == 4.50, "the winner is the latest document valid_from"
    assert rows[0]["temporal"] == ["rate_value"]
    assert rows[0]["id"] == entity_id(TIER2_KEY)

    behind = session.run(
        "MATCH (:Entity {id: $id})-[:AGGREGATES]->(o:Observation) RETURN count(o) AS c",
        id=entity_id(TIER2_KEY),
    ).single()["c"]
    assert behind == 3

    conflicts = session.run(
        "MATCH (c:Conflict)-[:CONFLICT_OF]->(:Entity {id: $id}) RETURN count(c) AS c",
        id=entity_id(TIER2_KEY),
    ).single()["c"]
    assert conflicts == 0, "three disjoint windows are history, not a conflict"


def test_observations_carry_no_entity_label_and_no_class_label(session):
    """Trap 3, checked the way `graph_metadata` and `entity_listing` see it —
    a `:RATE_ENTRY` label on an observation would make a text2cypher-generated
    `MATCH (p:RATE_ENTRY)` return superseded facts."""
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    labels = session.run(
        "MATCH (o:Observation {domain: $d}) UNWIND labels(o) AS l RETURN collect(DISTINCT l) AS ls", d=DOMAIN,
    ).single()["ls"]
    assert set(labels) == {"Observation"}

    # and the Entity DOES carry its class label, derived from entity_class
    entity_labels = session.run(
        "MATCH (e:Entity {domain: $d}) UNWIND labels(e) AS l RETURN collect(DISTINCT l) AS ls", d=DOMAIN,
    ).single()["ls"]
    assert set(entity_labels) == {"Entity", CLASS}

    # the class-labelled match returns the projection only
    by_class = session.run(f"MATCH (n:{CLASS}) WHERE n.domain = $d RETURN count(n) AS c", d=DOMAIN).single()["c"]
    assert by_class == 1


def test_rebuild_is_idempotent_and_never_churns_the_element_id(session):
    """Trap 5: MERGE on the deterministic id, never delete-and-recreate."""
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    first = session.run(
        "MATCH (e:Entity {id: $id}) RETURN elementId(e) AS eid", id=entity_id(TIER2_KEY)
    ).single()["eid"]

    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    second = session.run(
        "MATCH (e:Entity {id: $id}) RETURN elementId(e) AS eid, e.rate_value AS rate",
        id=entity_id(TIER2_KEY),
    ).single()
    assert second["eid"] == first, "a rebuild must not recreate the node"
    assert second["rate"] == 4.50


def test_a_full_rebuild_from_scratch_reproduces_the_same_entity_id(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    before = session.run(
        "MATCH (e:Entity {id: $id}) RETURN properties(e) AS p", id=entity_id(TIER2_KEY)
    ).single()["p"]

    session.run("MATCH (e:Entity {domain: $d}) DETACH DELETE e", d=DOMAIN).consume()
    session.execute_write(lambda tx: full_rebuild(tx, [DOMAIN]))
    after = session.run(
        "MATCH (e:Entity {id: $id}) RETURN properties(e) AS p", id=entity_id(TIER2_KEY)
    ).single()["p"]

    assert after["id"] == before["id"]
    assert after["rate_value"] == before["rate_value"]
    assert after["name"] == before["name"]


# ── the GC rule ─────────────────────────────────────────────────────────────


def test_a_key_with_zero_latest_observations_has_its_entity_deleted(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run("MATCH (e:Entity {domain: $d}) RETURN count(e) AS c", d=DOMAIN).single()["c"] == 1

    session.run(
        "MATCH (o:Observation {domain: $d}) SET o._status = 'history'", d=DOMAIN
    ).consume()
    summary = session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    assert summary["deleted"] == 1
    assert session.run("MATCH (e:Entity {domain: $d}) RETURN count(e) AS c", d=DOMAIN).single()["c"] == 0


def test_a_renamed_entity_leaves_no_orphan_when_the_prior_key_is_swept(session):
    """Trap 6, set 2. Version 1 called it one thing, version 2 calls it
    another; rebuilding only the incoming key would strand the old Entity."""
    old_key = aggregate_key("Legacy Tier 2 Rate", CLASS, DOMAIN)
    write_observation(
        session, id="obs-v1", name="Legacy Tier 2 Rate", canonical_name="Legacy Tier 2 Rate",
        doc_id="doc-x", chunk_id="doc-x_001", _doc_valid_from="2026-01-15",
        _valid_from="2026-01-15", rate_value=4.70,
    )
    session.execute_write(lambda tx: rebuild(tx, [old_key]))
    assert session.run("MATCH (e:Entity {domain: $d}) RETURN count(e) AS c", d=DOMAIN).single()["c"] == 1

    # version 2: the prior version's observations go to history, a new name arrives
    session.run("MATCH (o:Observation {doc_id: 'doc-x'}) SET o._status = 'history'").consume()
    write_observation(
        session, id="obs-v2", name=TIER2, canonical_name=TIER2,
        doc_id="doc-x", chunk_id="doc-x_001", doc_version=2,
        _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70,
    )

    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY, old_key]))
    names = [r["n"] for r in session.run(
        "MATCH (e:Entity {domain: $d}) RETURN e.name AS n", d=DOMAIN).data()]
    assert names == [TIER2], "the abandoned key's Entity must be gone"


# ── trap 7: never null an embedding ─────────────────────────────────────────


def test_a_rebuild_leaves_the_embedding_in_place_and_only_flags_it(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    eid = entity_id(TIER2_KEY)
    session.run(
        "MATCH (e:Entity {id: $id}) SET e.embedding = $v, e.embedding_stale = false",
        id=eid, v=[0.25] * 8,
    ).consume()

    # a new observation changes the winning description
    write_observation(
        session, id="obs-apr", name="SmartSaver Tier 2", canonical_name=TIER2,
        doc_id="doc-apr", chunk_id="doc-apr_001", _doc_valid_from="2026-04-01",
        _valid_from="2026-04-01", rate_value=4.40, description="April rate, revised.",
    )
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    row = session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.embedding AS emb, e.embedding_stale AS stale, "
        "e.description AS desc", id=eid,
    ).single()
    assert row["emb"] is not None, "NEVER null an embedding — null removes it from entity_embedding"
    assert list(row["emb"]) == [0.25] * 8
    assert row["stale"] is True
    assert row["desc"] == "April rate, revised."


def test_an_unchanged_description_does_not_re_flag_a_fresh_embedding(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    eid = entity_id(TIER2_KEY)
    session.run(
        "MATCH (e:Entity {id: $id}) SET e.embedding = $v, e.embedding_stale = false",
        id=eid, v=[0.25] * 8,
    ).consume()

    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    stale = session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.embedding_stale AS s", id=eid
    ).single()["s"]
    assert stale is False


def test_an_entity_created_by_a_rebuild_starts_stale_not_null_embedded(session):
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    row = session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.embedding_stale AS s, e.embedding AS emb",
        id=entity_id(TIER2_KEY),
    ).single()
    assert row["s"] is True
    assert row["emb"] is None  # never embedded yet — the sweep will fill it


# ── stale properties are cleared, not left behind ───────────────────────────


def test_a_property_no_longer_asserted_disappears_from_the_entity(session):
    """`SET e += $props` alone would leave it behind forever, and the
    projection would stop being a projection."""
    write_observation(
        session, id="obs-1", name=TIER2, canonical_name=TIER2, doc_id="doc-1",
        chunk_id="doc-1_001", _doc_valid_from="2026-01-15", _valid_from="2026-01-15",
        rate_value=4.70, withdrawn_property="should not survive",
    )
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.withdrawn_property AS p", id=entity_id(TIER2_KEY)
    ).single()["p"] == "should not survive"

    session.run("MATCH (o:Observation {id: 'obs-1'}) REMOVE o.withdrawn_property").consume()
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.withdrawn_property AS p", id=entity_id(TIER2_KEY)
    ).single()["p"] is None


# ── trap 2: a failing rebuild fails the whole commit ────────────────────────


def test_a_failure_inside_the_transaction_rolls_back_the_observation_write(session):
    """The projection rebuild is not a best-effort hook. If it raises, the
    commit that dirtied the projection fails and NOTHING lands."""
    def write_then_fail(tx):
        tx.run(
            "CREATE (o:Observation {id: 'obs-doomed', domain: $d, key: $k, _status: 'latest'})",
            d=DOMAIN, k=key_string(TIER2_KEY),
        )
        rebuild(tx, [TIER2_KEY])
        raise RuntimeError("projection rebuild failed")

    with pytest.raises(RuntimeError):
        session.execute_write(write_then_fail)

    assert session.run(
        "MATCH (o:Observation {id: 'obs-doomed'}) RETURN count(o) AS c"
    ).single()["c"] == 0
    assert session.run(
        "MATCH (e:Entity {domain: $d}) RETURN count(e) AS c", d=DOMAIN
    ).single()["c"] == 0


# ── conflicts, live ─────────────────────────────────────────────────────────


def test_a_same_instant_disagreement_materializes_a_conflict_with_evidence(session):
    write_observation(
        session, id="obs-a", name=TIER2, canonical_name=TIER2, doc_id="doc-a",
        chunk_id="doc-a_001", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70,
    )
    write_observation(
        session, id="obs-b", name=TIER2, canonical_name=TIER2, doc_id="doc-b",
        chunk_id="doc-b_001", _doc_valid_from="2026-02-01", _valid_from="2026-01-15", rate_value=4.60,
    )
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))

    row = session.run(
        "MATCH (c:Conflict)-[:CONFLICT_OF]->(:Entity {id: $id}) "
        "RETURN c.property AS p, c._source AS src, c.values AS vals",
        id=entity_id(TIER2_KEY),
    ).single()
    assert row["p"] == "rate_value"
    assert row["src"] == "projection"
    assert set(row["vals"]) == {"4.7", "4.6"}

    evidence = session.run(
        "MATCH (:Conflict {_source:'projection'})-[:EVIDENCE]->(o:Observation) "
        "RETURN collect(o.id) AS ids"
    ).single()["ids"]
    assert set(evidence) == {"obs-a", "obs-b"}


def test_a_resolved_conflict_disappears_once_the_observations_agree(session):
    write_observation(
        session, id="obs-a", name=TIER2, canonical_name=TIER2, doc_id="doc-a",
        chunk_id="doc-a_001", _doc_valid_from="2026-01-15", _valid_from="2026-01-15", rate_value=4.70,
    )
    write_observation(
        session, id="obs-b", name=TIER2, canonical_name=TIER2, doc_id="doc-b",
        chunk_id="doc-b_001", _doc_valid_from="2026-02-01", _valid_from="2026-01-15", rate_value=4.60,
    )
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run("MATCH (c:Conflict {_source:'projection'}) RETURN count(c) AS c").single()["c"] == 1

    session.run("MATCH (o:Observation {id:'obs-b'}) SET o.rate_value = 4.70").consume()
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run("MATCH (c:Conflict {_source:'projection'}) RETURN count(c) AS c").single()["c"] == 0


def test_the_pairwise_adjudicators_conflicts_are_not_deleted_by_a_rebuild(session):
    """`artmind.conflicts` authors `:Conflict` nodes by a different mechanism.
    The projection owns only those it marked `_source: 'projection'`."""
    seed_three_schedules(session)
    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    session.run(
        "MATCH (e:Entity {id: $id}) CREATE (c:Conflict {id: 'pairwise-1', status: 'open', "
        "_test: $tag})-[:CONFLICT_OF]->(e)",
        id=entity_id(TIER2_KEY), tag=CONFLICT_TAG,
    ).consume()

    session.execute_write(lambda tx: rebuild(tx, [TIER2_KEY]))
    assert session.run(
        "MATCH (c:Conflict {id: 'pairwise-1'}) RETURN count(c) AS c"
    ).single()["c"] == 1
