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
