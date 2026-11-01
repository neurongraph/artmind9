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


def test_survey_topics_reads_matching_pages(monkeypatch, tmp_path):
    import paths

    store.write_page(tmp_path, "t", {**_page("t", [{"doc_id": "d1", "name": "a.md", "content_sha": "v1"},
                                                    {"doc_id": "d2", "name": "b.md", "content_sha": "v2-new"}]).meta}, "x")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path)
    monkeypatch.setattr(stale, "read_session", lambda: Recorder(_graph))
    assert stale.survey_topics(["d1", "d2"], ["contracts"]) == [
        {"slug": "t", "topic": "t", "built": "2026-11-02", "stale": False, "overlap": 2}]
    monkeypatch.setattr(paths, "TOPICS_DIR", None)
    assert stale.survey_topics(["d1", "d2"], ["contracts"]) == []
