"""The topic index (spec 2026-11-02 §6): node, edges, bump and embedding per page.

Synthetic text only. Every assertion is on the Cypher and parameters sent.
"""
from _dry_run import WritableRecorder
from _neo4j_recorder import Recorder

from artmind.topics import index, store

META = {
    "artmind_topic": 1, "topic": "Quarterly notices", "slug": "notices", "domains": ["alpha", "alpha.sub"],
    "anchor": {"kind": "entity", "key": "widget|THING|alpha", "name": "Widget"},
    "built": "2026-11-02T10:00:00Z",
    "sources": [{"doc_id": "d2", "name": "b.md", "passages": ["p2", "p1"]}, {"doc_id": "d1", "name": "a.md", "passages": ["p1"]}],
    "checks": {},
}
BODY = "## Overview\nNotices went out [a](x.md#1) each quarter.\n\n## Detail\nNot part of the index text."


def _page(tmp_path, meta=None, body=BODY, slug="notices"):
    store.write_page(tmp_path, slug, meta or META, body)
    return store.read_page(tmp_path, slug)


def _bumps(calls):
    return [p for c, p in calls if "MERGE (s:DomainState" in c]


def test_text_is_topic_plus_overview_without_links_capped(tmp_path):
    record = index.page_record(_page(tmp_path))
    assert record["text"] == "Quarterly notices\nNotices went out a each quarter."
    long = index.page_text("T", "## Overview\n" + "word " * 1000)
    assert len(long) == index.TEXT_MAX


def test_text_falls_back_to_the_opening_when_there_is_no_overview():
    assert index.page_text("T", "Plain opening.\n\n## Other\nlater") == "T\nPlain opening."


def test_record_holds_the_first_domain_sorted_citations_and_the_anchor_key(tmp_path):
    record = index.page_record(_page(tmp_path))
    assert (record["domain"], record["domains"]) == ("alpha", ["alpha", "alpha.sub"])
    assert record["doc_ids"] == ["d1", "d2"] and record["passage_ids"] == ["p1", "p2"]
    assert (record["anchor_kind"], record["anchor_key"]) == ("entity", "widget|THING|alpha")


def test_a_topic_anchor_has_no_key(tmp_path):
    record = index.page_record(_page(tmp_path, {**META, "anchor": {"kind": "topic"}}))
    assert record["anchor_kind"] == "topic" and record["anchor_key"] is None


def test_sources_version_ignores_nothing_in_sources_and_everything_else():
    assert index.sources_version(META) == index.sources_version({**META, "topic": "other"})
    assert index.sources_version(META) != index.sources_version({**META, "sources": META["sources"][:1]})


def test_a_new_page_writes_node_edges_and_bumps_in_the_same_transaction(tmp_path):
    record = index.page_record(_page(tmp_path))
    tx = Recorder()
    assert index.upsert_page(tx, record) == "created"
    texts = [c for c, _ in tx.calls]
    assert "MERGE (p:TopicPage {slug: $slug})" in texts[1]
    node = tx.queries_containing("MERGE (p:TopicPage {slug: $slug})")[0][1]
    assert node["domain"] == "alpha" and node["domains"] == ["alpha", "alpha.sub"] and node["text"] == record["text"]
    assert tx.queries_containing("MERGE (p)-[:CITES]->(d)")[0][1]["doc_ids"] == ["d1", "d2"]
    assert tx.queries_containing("MERGE (p)-[:CITES_PASSAGE]->(q)")[0][1]["passage_ids"] == ["p1", "p2"]
    assert tx.queries_containing("MERGE (p)-[:ABOUT]->(e)")[0][1]["key"] == "widget|THING|alpha"
    assert [b["domains"] for b in _bumps(tx.calls)] == [["alpha", "alpha.sub"]]
    assert "MERGE (s:DomainState" in texts[-1]  # after the writes, inside the same transaction


def _stored(record):
    return {"topic": record["topic"], "domains": record["domains"], "built": record["built"],
            "anchor_kind": record["anchor_kind"], "text": record["text"], "sources_version": record["sources_version"]}


def test_an_unchanged_page_sends_no_write_and_no_bump(tmp_path):
    record = index.page_record(_page(tmp_path))
    tx = Recorder(lambda c, p: [_stored(record)] if "RETURN p.topic" in c else [])
    assert index.upsert_page(tx, record) == "unchanged"
    assert len(tx.calls) == 1
    assert _bumps(tx.calls) == []


def test_changed_text_drops_the_stale_vector_but_keeps_edges(tmp_path):
    record = index.page_record(_page(tmp_path))
    old = {**_stored(record), "text": "something older"}
    tx = Recorder(lambda c, p: [old] if "RETURN p.topic" in c else [])
    assert index.upsert_page(tx, record) == "updated"
    assert tx.queries_containing("REMOVE p.embedding, p._embed_key")
    assert not tx.queries_containing("DELETE r")
    assert not tx.queries_containing("MERGE (p)-[:CITES]->(d)")
    assert len(_bumps(tx.calls)) == 1


def test_changed_sources_rewrite_the_edges(tmp_path):
    record = index.page_record(_page(tmp_path))
    old = {**_stored(record), "sources_version": "old"}
    tx = Recorder(lambda c, p: [old] if "RETURN p.topic" in c else [])
    index.upsert_page(tx, record)
    assert tx.queries_containing("DELETE r")[0][1] == {"slug": "notices"}
    assert tx.queries_containing("MERGE (p)-[:CITES]->(d)")
    assert not tx.queries_containing("REMOVE p.embedding")


def test_a_moved_domain_bumps_both_the_old_and_the_new(tmp_path):
    record = index.page_record(_page(tmp_path))
    old = {**_stored(record), "domains": ["beta"], "text": "x"}
    tx = Recorder(lambda c, p: [old] if "RETURN p.topic" in c else [])
    index.upsert_page(tx, record)
    assert [b["domains"] for b in _bumps(tx.calls)] == [["alpha", "alpha.sub", "beta"]]


def test_delete_detaches_the_node_and_bumps_its_domains():
    tx = Recorder(lambda c, p: [{"domains": ["alpha"]}] if "DETACH DELETE" in c else [])
    assert index.delete_page(tx, "notices") is True
    assert tx.queries_containing("DETACH DELETE p")[0][1] == {"slug": "notices"}
    assert [b["domains"] for b in _bumps(tx.calls)] == [["alpha"]]


def test_deleting_an_absent_page_bumps_nothing():
    tx = Recorder()
    assert index.delete_page(tx, "gone") is False
    assert _bumps(tx.calls) == []


# ----------------------------------------------------------------- the sweep


class _Graph(WritableRecorder):
    """Answers the sweep's reads from `nodes`; applies nothing."""

    def __init__(self, nodes=(), stored=None):
        self.nodes = list(nodes)
        self.stored = stored or {}
        super().__init__(self._answer)

    def _answer(self, cypher, params):
        if "RETURN p.slug AS slug" in cypher:
            return self.nodes
        if "RETURN p.topic" in cypher:
            row = self.stored.get(params["slug"])
            return [row] if row else []
        if "DETACH DELETE" in cypher:
            return [{"domains": ["alpha"]}]
        return []


def _two_pages(tmp_path):
    store.write_page(tmp_path, "notices", META, BODY)
    store.write_page(tmp_path, "other", {**META, "slug": "other", "topic": "Other", "domains": ["beta"],
                                         "anchor": {"kind": "topic"}}, "## Overview\nOther.")


def test_sweep_indexes_new_pages_and_embeds_them(tmp_path):
    _two_pages(tmp_path)
    graph = _Graph(nodes=[])
    embedded = []
    # after the writes the nodes exist but have no vector
    graph.nodes = [{"slug": "notices", "domains": ["alpha"], "has_embedding": False, "embed_key": None},
                   {"slug": "other", "domains": ["beta"], "has_embedding": False, "embed_key": None}]
    out = index.sweep(directory=tmp_path, session=graph, cache=None, embed_key="k1",
                      embed=lambda t: embedded.append(t) or [0.5])
    assert out == {"indexed": 2, "updated": 2, "deleted": 0, "embedded": 2}
    assert len(embedded) == 2 and embedded[0].startswith("Quarterly notices")
    vectors = graph.queries_containing("SET p.embedding = $embedding")
    assert [p["slug"] for _, p in vectors] == ["notices", "other"]
    assert all(p["embed_key"] == "k1" and p["embedding"] == [0.5] for _, p in vectors)


def test_sweep_deletes_nodes_whose_file_is_gone(tmp_path):
    _two_pages(tmp_path)
    graph = _Graph(nodes=[{"slug": "stale", "domains": ["alpha"], "has_embedding": True, "embed_key": "k1"},
                          {"slug": "notices", "domains": ["alpha"], "has_embedding": True, "embed_key": "k1"},
                          {"slug": "other", "domains": ["beta"], "has_embedding": True, "embed_key": "k1"}])
    out = index.sweep(directory=tmp_path, session=graph, cache=None, embed_key="k1", embed=lambda t: [1.0])
    assert out["deleted"] == 1
    assert [p["slug"] for _, p in graph.queries_containing("DETACH DELETE")] == ["stale"]


def test_sweep_scoped_to_a_domain_leaves_other_domains_alone(tmp_path):
    _two_pages(tmp_path)
    graph = _Graph(nodes=[{"slug": "elsewhere", "domains": ["gamma"], "has_embedding": True, "embed_key": "k1"},
                          {"slug": "notices", "domains": ["alpha"], "has_embedding": True, "embed_key": "k1"}])
    out = index.sweep(["alpha"], directory=tmp_path, session=graph, cache=None, embed_key="k1", embed=lambda t: [1.0])
    assert out["indexed"] == 1 and out["deleted"] == 0
    assert not graph.queries_containing("DETACH DELETE")
    assert [p["slug"] for _, p in graph.queries_containing("MERGE (p:TopicPage {slug: $slug})")] == ["notices"]


def test_a_second_sweep_changes_nothing_and_embeds_nothing(tmp_path):
    store.write_page(tmp_path, "notices", META, BODY)
    record = index.page_record(store.read_page(tmp_path, "notices"))
    graph = _Graph(nodes=[{"slug": "notices", "domains": ["alpha"], "has_embedding": True, "embed_key": "k1"}],
                   stored={"notices": _stored(record)})
    out = index.sweep(directory=tmp_path, session=graph, cache=None, embed_key="k1", embed=lambda t: 1 / 0)
    assert out == {"indexed": 1, "updated": 0, "deleted": 0, "embedded": 0}
    assert not [c for c, _ in graph.calls if "MERGE" in c or "DELETE" in c or "SET" in c]


def test_a_vector_cut_under_another_key_is_embedded_again(tmp_path):
    store.write_page(tmp_path, "notices", META, BODY)
    record = index.page_record(store.read_page(tmp_path, "notices"))
    graph = _Graph(nodes=[{"slug": "notices", "domains": ["alpha"], "has_embedding": True, "embed_key": "old"}],
                   stored={"notices": _stored(record)})
    out = index.sweep(directory=tmp_path, session=graph, cache=None, embed_key="new", embed=lambda t: [2.0])
    assert out["embedded"] == 1 and out["updated"] == 0


def test_dry_run_sends_no_write_and_reports_the_same_counts(tmp_path):
    _two_pages(tmp_path)
    graph = _Graph(nodes=[{"slug": "stale", "domains": ["alpha"], "has_embedding": True, "embed_key": "k1"}])
    out = index.sweep(dry_run=True, directory=tmp_path, session=graph, cache=None, embed_key="k1",
                      embed=lambda t: 1 / 0)
    assert out == {"indexed": 2, "updated": 2, "deleted": 1, "embedded": 2}
    assert not [c for c, _ in graph.calls if any(w in c for w in ("MERGE", "DELETE", "SET", "REMOVE"))]


def test_an_embedding_failure_is_counted_not_raised(tmp_path):
    store.write_page(tmp_path, "notices", META, BODY)
    graph = _Graph(nodes=[{"slug": "notices", "domains": ["alpha"], "has_embedding": False, "embed_key": None}])

    def boom(text):
        raise RuntimeError("down")

    out = index.sweep(directory=tmp_path, session=graph, cache=None, embed_key="k", embed=boom)
    assert out["embedded"] == 0 and out["failed"] == 1
    assert not graph.queries_containing("SET p.embedding = $embedding")


def test_embedding_goes_through_the_embedding_cache(tmp_path):
    from artmind.embedding_cache import EmbeddingCache

    store.write_page(tmp_path, "notices", META, BODY)
    cache = EmbeddingCache(tmp_path / "cache.sqlite")
    calls = []
    for _ in range(2):
        graph = _Graph(nodes=[{"slug": "notices", "domains": ["alpha"], "has_embedding": False, "embed_key": None}])
        index.sweep(directory=tmp_path, session=graph, cache=cache, embed_key="k",
                    embed=lambda t: calls.append(t) or [0.25])
    assert len(calls) == 1  # the second sweep read the vector from the cache


# ------------------------------------------------------------ writer hooks


def test_best_effort_sweep_without_a_topics_folder_does_nothing(monkeypatch):
    import paths

    monkeypatch.setattr(paths, "TOPICS_DIR", None, raising=False)
    assert index.best_effort_sweep() is None


def test_best_effort_sweep_reports_a_failure_instead_of_raising(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(index, "sweep", lambda domains=None, **k: 1 / 0)
    out = index.best_effort_sweep(["alpha"])
    assert out["next"] == "artmind topic index" and "error" in out


def test_index_page_never_raises_when_the_graph_is_down(tmp_path, monkeypatch):
    import paths

    from artmind import graph_query

    store.write_page(tmp_path, "notices", META, BODY)
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path, raising=False)

    def down():
        raise ConnectionError("no graph")

    monkeypatch.setattr(graph_query, "neo4j_session", down)
    out = index.index_page("notices")
    assert out["status"] == "not indexed" and out["next"] == "artmind topic index"


def test_topic_build_indexes_the_page_it_wrote(tmp_path, monkeypatch):
    import json

    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.topics import build

    seen = []
    monkeypatch.setattr(build, "build_page", lambda *a, **k: {"slug": "notices", "path": "p.md", "dry_run": False})
    monkeypatch.setattr(index, "index_page", lambda slug: seen.append(slug) or {"status": "created", "embedded": 1})
    out = CliRunner().invoke(cli, ["topic", "build", "Notices", "--domain", "alpha", "--compact"])
    assert out.exit_code == 0, out.output
    assert seen == ["notices"] and json.loads(out.stdout)["indexed"]["status"] == "created"


def test_topic_build_dry_run_and_out_folder_do_not_touch_the_index(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.topics import build

    monkeypatch.setattr(build, "build_page", lambda *a, **k: {"slug": "notices", "path": "p.md", "dry_run": False})
    monkeypatch.setattr(index, "index_page", lambda slug: (_ for _ in ()).throw(AssertionError("indexed")))
    base = ["topic", "build", "Notices", "--domain", "alpha", "--compact"]
    assert CliRunner().invoke(cli, [*base, "--out", str(tmp_path)]).exit_code == 0
    monkeypatch.setattr(build, "build_page", lambda *a, **k: {"slug": "notices", "path": None, "dry_run": True})
    assert CliRunner().invoke(cli, [*base, "--dryRun"]).exit_code == 0


def test_topic_delete_drops_the_node_for_the_vault_folder(tmp_path, monkeypatch):
    import paths
    from click.testing import CliRunner

    from artmind.cli import cli

    store.write_page(tmp_path, "notices", META, BODY)
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path, raising=False)
    gone = []
    monkeypatch.setattr(index, "unindex_page", lambda slug: gone.append(slug) or {"status": "deleted"})
    out = CliRunner().invoke(cli, ["topic", "delete", "notices", "--compact"])
    assert out.exit_code == 0, out.output
    assert gone == ["notices"] and not (tmp_path / "notices.md").exists()


def test_rebuild_sweep_runs_the_topic_sweep_and_reports_it(monkeypatch):
    from artmind import ingest, projection

    monkeypatch.setattr(projection, "full_rebuild_batched", lambda keys: {"rebuilt": 0})
    monkeypatch.setattr(projection, "all_keys", lambda tx, d: set())
    monkeypatch.setattr(projection, "passage_domains", lambda tx: [])
    monkeypatch.setattr("artmind.jobs._resolve_failed_finalizes", lambda: [])
    session = WritableRecorder()
    monkeypatch.setattr("artmind.graph_query.neo4j_session", lambda: session)
    monkeypatch.setattr(index, "best_effort_sweep", lambda domains=None: {"indexed": 3, "updated": 0, "deleted": 0, "embedded": 0})
    assert ingest.rebuild_and_sweep_all()["topic_index"]["indexed"] == 3
