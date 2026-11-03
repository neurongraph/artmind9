from _neo4j_recorder import Recorder

from artmind.topics import stale, store


def _page(slug, sources, anchor=None, domains=("contracts",)):
    meta = {"artmind_topic": 1, "topic": slug, "slug": slug, "domains": list(domains),
            "anchor": anchor or {"kind": "topic"}, "built": "2026-11-02", "sources": sources}
    return store.Page(slug, meta, "body", "", None)


def _graph(cypher, params):
    if "UNWIND $docIds" in cypher:
        live = {"d1": "v1", "d2": "v2-new"}
        return [{"doc_id": d, "live": d in live, "version": live.get(d)} for d in params["docIds"]]
    if "UNWIND $keys" in cypher:
        return [{"key": k, "hash": "h-new"} for k in params["keys"]]
    return []


def test_changed_retired_and_entity_reasons():
    pages = [
        _page("fresh", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}]),
        _page("changed", [{"doc_id": "d2", "name": "b.md", "content_sha": "v2"}]),
        _page("retired", [{"doc_id": "d3", "name": "c.md", "content_sha": "v3"}]),
        _page("entity", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}],
              anchor={"kind": "entity", "key": "k1", "name": "Acme", "observation_set_hash": "h-old"}),
    ]
    s = Recorder(_graph)
    out = stale.staleness(pages, s)
    assert out == {"fresh": [], "changed": ["source changed: b.md"], "retired": ["source retired: c.md"],
                   "entity": ["entity changed: Acme"]}
    (_, p1), (_, p2) = s.calls
    assert p1["docIds"] == ["d1", "d2", "d3"] and p2["keys"] == ["k1"]


def test_no_pages_runs_no_query():
    s = Recorder(_graph)
    assert stale.staleness([], s) == {} and s.calls == []


def test_summary_shape():
    page = _page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}])
    assert stale.page_summary(page, ["source changed: a.md"]) == {
        "slug": "t", "topic": "t", "domains": ["contracts"], "built": "2026-11-02", "anchor": "topic",
        "sources": 1, "stale": True, "stale_reasons": ["source changed: a.md"], "checks": {}}


def test_survey_topics_scans_the_files_when_the_index_is_empty(monkeypatch, tmp_path):
    import paths

    store.write_page(tmp_path, "t", {**_page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"},
                                                    {"doc_id": "d2", "name": "b.md", "content_sha": "v2-new"}]).meta}, "x")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path)
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_graph))
    assert stale.survey_topics(["d1", "d2"], ["contracts"]) == [
        {"slug": "t", "topic": "t", "built": "2026-11-02", "stale": False, "overlap": 2}]
    monkeypatch.setattr(paths, "TOPICS_DIR", None)
    assert stale.survey_topics(["d1", "d2"], ["contracts"]) == []


# ----------------------------------------------- the CITES-backed reader


def _indexed_graph(indexed, hits):
    def answer(cypher, params):
        if ":CITES]" in cypher:
            return [{"indexed": indexed, "hits": hits}]
        return _graph(cypher, params)
    return answer


def _vault(monkeypatch, tmp_path, *pages):
    import paths

    for page in pages:
        store.write_page(tmp_path, page.slug, page.meta, "x")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path)


def test_survey_topics_asks_the_index_once_and_reads_only_the_matched_files(monkeypatch, tmp_path):
    fresh = _page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}, {"doc_id": "d2", "name": "b.md", "content_sha": "v2-new"}])
    other = _page("u", [{"doc_id": "d9", "name": "z.md", "content_sha": "v9"}])
    _vault(monkeypatch, tmp_path, fresh, other)
    graph = Recorder(_indexed_graph(2, [{"slug": "t", "domains": ["contracts"], "overlap": 2}]))
    monkeypatch.setattr(stale, "read_session", lambda: graph)
    monkeypatch.setattr(store, "list_pages", lambda d: (_ for _ in ()).throw(AssertionError("scanned the folder")))

    out = stale.survey_topics(["d2", "d1"], ["contracts"])

    assert out == [{"slug": "t", "topic": "t", "built": "2026-11-02", "stale": False, "overlap": 2}]
    assert out.warnings == []
    cites = graph.queries_containing(":CITES]")
    assert len(cites) == 1 and cites[0][1]["docIds"] == ["d1", "d2"]
    assert "overlap >= 2" in cites[0][0]


def test_survey_topics_keeps_the_domain_relation_and_the_order(monkeypatch, tmp_path):
    a = _page("a", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}])
    b = _page("b", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}])
    c = _page("c", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}], domains=("elsewhere",))
    _vault(monkeypatch, tmp_path, a, b, c)
    hits = [{"slug": "b", "domains": ["contracts.eu"], "overlap": 2}, {"slug": "a", "domains": ["contracts"], "overlap": 3},
            {"slug": "c", "domains": ["elsewhere"], "overlap": 9}]
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_indexed_graph(3, hits)))
    out = stale.survey_topics(["d1", "d2", "d3"], ["contracts"])
    assert [(t["slug"], t["overlap"]) for t in out] == [("a", 3), ("b", 2)]


def test_a_node_whose_file_is_gone_is_left_out(monkeypatch, tmp_path):
    _vault(monkeypatch, tmp_path, _page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}]))
    hits = [{"slug": "ghost", "domains": ["contracts"], "overlap": 2}]
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_indexed_graph(1, hits)))
    assert stale.survey_topics(["d1", "d2"], ["contracts"]) == []


def test_an_empty_index_falls_back_to_the_files_and_names_the_repair(monkeypatch, tmp_path):
    page = _page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}, {"doc_id": "d2", "name": "b.md", "content_sha": "v2-new"}])
    _vault(monkeypatch, tmp_path, page)
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_indexed_graph(0, [])))
    out = stale.survey_topics(["d1", "d2"], ["contracts"])
    assert [t["slug"] for t in out] == ["t"]
    assert len(out.warnings) == 1 and "artmind topic index" in out.warnings[0]


def test_an_empty_index_and_no_pages_is_silent(monkeypatch, tmp_path):
    _vault(monkeypatch, tmp_path)
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_indexed_graph(0, [])))
    out = stale.survey_topics(["d1", "d2"], ["contracts"])
    assert out == [] and out.warnings == []


def test_a_graph_without_the_index_still_answers_from_the_files(monkeypatch, tmp_path):
    page = _page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"}, {"doc_id": "d2", "name": "b.md", "content_sha": "v2-new"}])
    _vault(monkeypatch, tmp_path, page)

    def down(cypher, params):
        if ":CITES]" in cypher:
            raise RuntimeError("index missing")
        return _graph(cypher, params)

    monkeypatch.setattr(stale, "read_session", lambda: Recorder(down))
    out = stale.survey_topics(["d1", "d2"], ["contracts"])
    assert [t["slug"] for t in out] == ["t"] and "artmind topic index" in out.warnings[0]
