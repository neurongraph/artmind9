"""`:TopicPage` is an index of generated pages, never evidence (spec 2026-11-02 §6).

Static checks over the retrieval modules and the label lists a graph copy travels with.
"""
import inspect
import re

from artmind import graph_query, graph_snapshot, survey, sync_state, text2cypher, vector_query
from artmind.topics import index


def _source(module) -> str:
    return inspect.getsource(module)


def test_retrieval_modules_never_name_the_label():
    for module in (vector_query, survey, graph_query):
        assert "TopicPage" not in _source(module), f"{module.__name__} names :TopicPage"


def test_the_text2cypher_schema_text_never_names_it():
    assert "TopicPage" not in text2cypher.STRUCTURAL_SCHEMA
    assert "TopicPage" not in _source(text2cypher)


def test_the_label_is_not_exported_in_a_snapshot_or_the_sync_fingerprint():
    labels = set(graph_snapshot.BASE_LABELS) | set(graph_snapshot.PROJECTED_LABELS) | set(graph_snapshot.SYNC_STATE_LABELS)
    assert "TopicPage" not in labels
    assert "TopicPage" not in _source(graph_snapshot)
    assert "TopicPage" not in _source(sync_state)


def test_a_page_node_carries_only_its_own_label_and_no_part_of_edge():
    cyphers = [v for k, v in vars(index).items() if k.endswith("_CYPHER") and isinstance(v, str)]
    assert cyphers
    for cypher in cyphers:
        assert "PART_OF" not in cypher
        for label in re.findall(r"(?:MERGE|CREATE)\s*\(\w*:([A-Za-z]+)", cypher):
            assert label == "TopicPage", f"the index writes a :{label} node"
        assert not re.search(r"SET\s+\w+:[A-Za-z]+", cypher), "the index adds a label to a node"


def test_the_indexes_setup_creates_for_it_are_its_own():
    from _neo4j_recorder import Recorder

    from artmind import setup

    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    assert "CREATE CONSTRAINT topic_page_slug IF NOT EXISTS FOR (n:TopicPage) REQUIRE n.slug IS UNIQUE" in text
    assert "CREATE VECTOR INDEX topic_embedding IF NOT EXISTS" in text
    assert "FOR (t:TopicPage) ON t.embedding WITH [t._domain]" in text
    assert "CREATE FULLTEXT INDEX topic_text_ft IF NOT EXISTS FOR (t:TopicPage) ON EACH [t.topic, t.text]" in text
    # no existing retrieval index gained the label
    for line in text.splitlines():
        if "TopicPage" in line:
            assert any(name in line for name in ("topic_page_slug", "topic_embedding", "topic_text_ft"))
