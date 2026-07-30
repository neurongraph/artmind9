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
