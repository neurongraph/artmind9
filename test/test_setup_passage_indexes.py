# test/test_setup_passage_indexes.py
from _neo4j_recorder import Recorder

from artmind import setup


def test_passage_schema_created_and_legacy_chunk_indexes_not():
    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    assert "CREATE CONSTRAINT passage_id IF NOT EXISTS FOR (n:DocPassage) REQUIRE n.id IS UNIQUE" in text
    assert "CREATE CONSTRAINT passage_history_id IF NOT EXISTS FOR (n:DocPassageHistory) REQUIRE n.id IS UNIQUE" in text
    for idx in ("passage_chunk_id", "passage_doc_id", "passage_domain", "passage_history_doc_id"):
        assert f"CREATE INDEX {idx} IF NOT EXISTS" in text
    assert "CREATE VECTOR INDEX passage_embedding IF NOT EXISTS" in text
    assert "ON p.embedding WITH [p._domain]" in text
    assert "`vector.dimensions`: 768" in text
    assert "CREATE FULLTEXT INDEX passage_text_ft IF NOT EXISTS FOR (p:DocPassage) ON EACH [p.text]" in text
    assert "chunk_embedding" not in text and "chunk_text_ft" not in text


def test_valid_time_indexes_are_on_the_prefixed_names_under_new_names():
    """Spec 2026-11-15 §7.1: valid time lives only in `_valid_from` / `_valid_to`. The old
    unprefixed indexes are the migration pack's to drop, never core's."""
    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    for label, stem in (("Entity", "entity"), ("DocChunk", "chunk"), ("Document", "document")):
        for prop in ("_valid_from", "_valid_to"):
            assert f"CREATE INDEX {stem}_prefixed{prop} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})" in text
    assert "ON (n.valid_from)" not in text and "ON (n.valid_to)" not in text
    assert "DROP INDEX" not in text or "valid_" not in "".join(
        c for c, _ in s.calls if c.startswith("DROP INDEX"))
