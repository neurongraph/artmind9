"""Aliases reach the entity fulltext index; entities get a name-only vector (plan 2026-11-15)."""
from _neo4j_recorder import Recorder

from artmind import projection, setup


def _setup(existing_props):
    def respond(cypher, params):
        if "SHOW FULLTEXT INDEXES" in cypher:
            return [{"properties": existing_props}] if existing_props is not None else []
        return []
    s = Recorder(respond)
    setup._setup_neo4j(s, 768)
    return [c for c, _ in s.calls]


def test_old_entity_name_ft_is_dropped_and_created_with_aliases():
    calls = _setup(["name", "description"])
    drop = calls.index("DROP INDEX entity_name_ft IF EXISTS")
    create = calls.index(setup._ENTITY_NAME_FT_CREATE)
    assert drop < create
    assert "ON EACH [e.name, e.description, e.aliases]" in setup._ENTITY_NAME_FT_CREATE


def test_current_entity_name_ft_is_left_alone():
    calls = _setup(["name", "description", "aliases"])
    assert "DROP INDEX entity_name_ft IF EXISTS" not in calls
    assert setup._ENTITY_NAME_FT_CREATE in calls


def test_fresh_graph_creates_entity_name_ft_without_a_drop():
    calls = _setup(None)
    assert "DROP INDEX entity_name_ft IF EXISTS" not in calls
    assert setup._ENTITY_NAME_FT_CREATE in calls


def test_name_vector_index_is_created():
    text = "\n".join(_setup(None))
    assert "CREATE VECTOR INDEX entity_name_embedding IF NOT EXISTS FOR (e:Entity) ON (e.name_embedding)" in text


def test_name_embedding_text_keeps_short_distinct_aliases():
    long = "Tier 2 Rate - 4.70% AER (£10,001-£50,000), effective 2026-01-15 for new savers"
    assert projection.name_embedding_text("Acme Ltd", ["acme ltd", "ACME", long, "", "Acme"]) == "Acme Ltd; ACME"
    assert projection.name_embedding_text("Acme Ltd", None) == "Acme Ltd"
    many = [f"A{i}" for i in range(20)]
    assert projection.name_embedding_text("X", many).count(";") == projection.NAME_EMBED_MAX_ALIASES


def test_merge_sets_name_src_from_name_and_aliases():
    obs = [
        {"id": "o1", "name": "Acme", "canonical_name": "Acme Ltd", "entity_class": "ORG", "_domain": "d"},
        {"id": "o2", "name": "Acme Ltd", "canonical_name": "Acme Ltd", "entity_class": "ORG", "_domain": "d",
         "aliases": ["ACME"]},
    ]
    props = projection.merge_observations(obs)["props"]
    assert props["_name_src"] == projection.name_source("Acme Ltd", props["aliases"])
    assert "name_embedding" in projection._PRESERVED_ENTITY_KEYS
    assert "_name_embed_key" in projection._PRESERVED_ENTITY_KEYS
