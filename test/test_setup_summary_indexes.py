# test/test_setup_summary_indexes.py
from _neo4j_recorder import Recorder

from artmind import setup


def test_summary_schema_is_created():
    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    assert "CREATE CONSTRAINT summary_id IF NOT EXISTS FOR (n:DocSummary) REQUIRE n.id IS UNIQUE" in text
    assert "CREATE CONSTRAINT summary_history_id IF NOT EXISTS FOR (n:DocSummaryHistory) REQUIRE n.id IS UNIQUE" in text
    for idx in ("summary_doc_id", "summary_history_doc_id"):
        assert f"CREATE INDEX {idx} IF NOT EXISTS" in text
    assert "CREATE VECTOR INDEX summary_embedding IF NOT EXISTS" in text
    assert "ON s.embedding WITH [s._domain, s.level]" in text
    assert "CREATE FULLTEXT INDEX summary_text_ft IF NOT EXISTS FOR (s:DocSummary) ON EACH [s.text]" in text
