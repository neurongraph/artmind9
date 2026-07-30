"""Entity history zone: setup, capture gate, snapshots, and version queries."""


def test_setup_creates_entity_version_constraint_and_indexes():
    """The history zone needs its own uniqueness constraint and lookup indexes.

    entity_id backs the anchor join, valid_to backs point-in-time filtering,
    and domain backs the same scoping every other label already has.
    """
    import inspect
    import artmind.setup as s

    src = inspect.getsource(s)
    assert "entity_version_id" in src
    assert "FOR (n:EntityVersion) REQUIRE n.id IS UNIQUE" in src
    assert "entity_version_entity" in src
    assert "ON (n.entity_id)" in src
    assert "entity_version_valid_to" in src
    assert "entity_version_domain" in src


import artmind.entity_history as eh


def test_gate_closed_when_document_declares_no_supersession(monkeypatch):
    """The common case: no notice, no metadata row, no family flag — no read."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: "# A policy\n\nSome prose.\n")
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is False


def test_gate_open_for_prose_notice(monkeypatch):
    monkeypatch.setattr(
        eh, "_read_doc_body",
        lambda name: "## Supersession Notice\n\nThis supersedes Version 2.0.\n",
    )
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is True


def test_gate_open_for_metadata_table_row(monkeypatch):
    monkeypatch.setattr(
        eh, "_read_doc_body",
        lambda name: "| Supersedes | [[older_doc]] |\n",
    )
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.reference") is True


def test_gate_open_when_schema_enables_title_family(monkeypatch):
    """Title-family inference needs no in-document signal, so it can't be gated."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: "# Nothing special\n")
    monkeypatch.setattr(
        eh, "load_schema",
        lambda domain: {"temporal": {"defaults": {"supersede_on_title_family": True}}},
    )

    assert eh.supersession_possible("doc.md", "banking.reference") is True


def test_gate_closed_when_markdown_is_missing(monkeypatch):
    """A document with no markdown on disk can declare nothing."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: None)
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is False


import json


class _CaptureResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class CaptureSession:
    def __init__(self, rows=None):
        self.runs = []
        self._rows = rows or []

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _CaptureResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _write_staged(tmp_path):
    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
        {"id": "c1_e2", "name": "No Props", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"approval_limit": "£2,000"}},
    ]), encoding="utf-8")


def test_capture_projects_only_asserted_keys_never_all_properties(tmp_path, monkeypatch):
    """properties(n) would return the 768-float embedding for every entity.

    That is ~1.2MB for a 200-entity document on every gated commit, for data
    the snapshot never uses. Project the asserted keys instead.
    """
    _write_staged(tmp_path)
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    eh.capture_prior_values(tmp_path, "banking.policy")

    assert len(session.runs) == 1, "capture must be a single batched round trip"
    cypher, kwargs = session.runs[0]
    assert "properties(n)" not in cypher
    assert "embedding" not in cypher
    assert "UNWIND $rows AS r" in cypher
    assert "[k IN r.keys | [k, n[k]]]" in cypher
    # Only the entity that asserts properties is looked up.
    assert len(kwargs["rows"]) == 1
    assert kwargs["rows"][0]["name"] == "Fee Policy"
    assert kwargs["rows"][0]["keys"] == ["approval_limit"]


def test_capture_returns_prior_values_keyed_by_entity_identity(tmp_path, monkeypatch):
    _write_staged(tmp_path)
    session = CaptureSession(rows=[{
        "idx": 0,
        "id": "live-uuid-1",
        "vf": "2026-01-15",
        "prior": [["approval_limit", "£500"]],
    }])
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    out = eh.capture_prior_values(tmp_path, "banking.policy")

    key = ("Fee Policy", "POLICY", "banking.policy")
    assert out[key]["entity_id"] == "live-uuid-1"
    assert out[key]["valid_from"] == "2026-01-15"
    assert out[key]["values"] == {"approval_limit": "£500"}


def test_capture_returns_empty_for_brand_new_entity(tmp_path, monkeypatch):
    """An entity this document introduces has no pre-write node, so no snapshot.

    Correct by construction: there is no prior state to preserve.
    """
    _write_staged(tmp_path)
    session = CaptureSession(rows=[])
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    assert eh.capture_prior_values(tmp_path, "banking.policy") == {}


def test_capture_merges_keys_across_chunks_for_the_same_entity(tmp_path, monkeypatch):
    """entities.json ids are chunk-scoped: one logical entity mentioned in two

    chunks appears as two rows sharing (name, entity_class, domain) but with
    different chunk-scoped ids and different asserted property subsets. The
    capture must union those keys into a single lookup, not silently drop one
    chunk's contribution.
    """
    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
        {"id": "c7_e3", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"approval_limit": "£2,000"}},
        {"id": "c7_e3", "properties": {"effective_date": "2026-01-01"}},
    ]), encoding="utf-8")
    session = CaptureSession(rows=[{
        "idx": 0,
        "id": "live-uuid-1",
        "vf": "2026-01-15",
        "prior": [["approval_limit", "£500"], ["effective_date", "2025-06-01"]],
    }])
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    eh.capture_prior_values(tmp_path, "banking.policy")

    assert len(session.runs) == 1, "one logical entity across chunks must be one row, one lookup"
    _, kwargs = session.runs[0]
    assert len(kwargs["rows"]) == 1
    assert kwargs["rows"][0]["keys"] == ["approval_limit", "effective_date"]

    out = eh.capture_prior_values(tmp_path, "banking.policy")
    key = ("Fee Policy", "POLICY", "banking.policy")
    assert out[key]["values"] == {
        "approval_limit": "£500",
        "effective_date": "2025-06-01",
    }


def test_capture_is_empty_when_no_entity_asserts_properties(tmp_path, monkeypatch):
    """No asserted properties means nothing can be overwritten — skip the query."""
    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text("[]", encoding="utf-8")
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    assert eh.capture_prior_values(tmp_path, "banking.policy") == {}
    assert session.runs == []


def test_snapshot_written_only_for_changed_values(monkeypatch):
    """Case 1 writes history; case 3 (identical values) writes nothing."""
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": "2026-01-15",
            "values": {"approval_limit": "£500", "owner": "Ops"},
        },
    }
    incoming = {
        ("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000", "owner": "Ops"},
    }

    written = eh.snapshot_changed_values(
        prior, incoming, effective="2026-06-01", newer_doc_id="doc-v3",
    )

    assert written == 1
    cypher, kwargs = session.runs[0]
    assert ":EntityVersion" in cypher
    assert "PRIOR_STATE" in cypher
    # Only the property that actually changed is preserved.
    assert kwargs["props"]["approval_limit"] == "£500"
    assert "owner" not in kwargs["props"]
    assert kwargs["validFrom"] == "2026-01-15"
    assert kwargs["validTo"] == "2026-06-01"
    assert kwargs["closedBy"] == "supersession"
    assert kwargs["supersededByDoc"] == "doc-v3"
    assert kwargs["entityId"] == "live-1"


def test_snapshot_skipped_when_nothing_changed(monkeypatch):
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": None,
            "values": {"approval_limit": "£2,000"},
        },
    }
    incoming = {("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000"}}

    assert eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3") == 0
    assert session.runs == []


def test_snapshot_preserves_the_clean_prior_value(monkeypatch):
    """Guards the Task 6 ordering contract: capture precedes the accretive write.

    If capture ever moves after write_to_graph, prior values arrive already
    merged as "old | new" and the history zone silently fills with
    concatenations instead of the values that were actually superseded. This
    asserts the clean value survives end to end.
    """
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": None,
            "values": {"approval_limit": "£500"},
        },
    }
    incoming = {("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000"}}

    eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3")

    _, kwargs = session.runs[0]
    assert kwargs["props"]["approval_limit"] == "£500"
    assert " | " not in kwargs["props"]["approval_limit"]


def test_snapshot_is_idempotent_on_rerun(monkeypatch):
    """A re-run after a partial commit_to_graph failure must not duplicate history.

    The Cypher must MERGE on (entity_id, superseded_by_doc) rather than blindly
    CREATE, so re-running snapshot_changed_values for the same entity closed out
    by the same superseding document lands on the same :EntityVersion node
    instead of minting a second one.
    """
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": "2026-01-15",
            "values": {"approval_limit": "£500"},
        },
    }
    incoming = {("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000"}}

    eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3")
    eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3")

    assert len(session.runs) == 2
    for cypher, kwargs in session.runs:
        assert "MERGE (v:EntityVersion {entity_id:" in cypher
        assert "CREATE (v:EntityVersion)" not in cypher
        assert kwargs["entityId"] == "live-1"
        assert kwargs["supersededByDoc"] == "doc-v3"
