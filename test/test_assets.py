"""Answer assets (spec 2026-11-02 §5): one record shape over views, topics, insights, summary sets."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import paths
from _neo4j_recorder import Recorder
from artmind import assets, graph_query
from artmind.catalogue import cache, parts
from artmind.catalogue.model import DocRecord
from artmind.cli import cli
from artmind.insights import store as insights_store
from artmind.insights.model import Finding, Run
from artmind.topics import store as topics_store

RECORD_KEYS = {"kind", "id", "domains", "title", "answers", "anchor", "built_at", "stale", "stale_reasons",
               "evidence_kind", "open", "status", "detail"}


def _write_view(root: Path, name, domains=("journal",), summary="Trips by year", examples=("which trips last year?",),
                broken=False):
    folder = root / name
    folder.mkdir(parents=True)
    spec = {"name": name, "version": 2, "domains": list(domains), "summary": summary, "examples": list(examples),
            "params": {"person": {"type": "entity", "entity_class": "PERSON"}},
            "cypher": "MATCH (n:Entity) WHERE n._domain IN $domains AND n._id = $person RETURN n.name AS name",
            "presentation": {"format": "list", "list": {"label": "name"}},
            "provenance": {"created": "2026-10-09", "origin": "authored"}}
    (folder / "view.yaml").write_text("name: x\n" if broken else yaml.safe_dump(spec))
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: when\n---\n\n# body\n")


@pytest.fixture
def world(tmp_path, monkeypatch):
    views = tmp_path / "views"
    topics = tmp_path / "_Topics"
    insights = tmp_path / "insights"
    for attr, value in (("DOMAIN_VIEWS_DIR", views), ("TOPICS_DIR", topics), ("INSIGHTS_DIR", insights),
                        ("LOGS_DIR", tmp_path / "logs")):
        monkeypatch.setattr(paths, attr, value)
    state = {"docs": [("d1", "a.md", True), ("d2", "b.md", False)], "entities": 5, "documents": 2}

    def read(cypher, params):
        if "DomainState" in cypher:
            return [{"domain": d, "generation": 3, "entities": state["entities"], "documents": state["documents"],
                     "history": 0} for d in params["domains"]]
        if "has_summary" in cypher:
            return [{"id": i, "name": n, "domain": "journal", "valid_from": None, "has_summary": s, "live": True}
                    for i, n, s in state["docs"]]
        return []

    def graph_rows(cypher, params):
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "live": True, "version": "v1"} for d in params["docIds"]]
        if "UNWIND $keys" in cypher:
            return [{"key": k, "hash": "h"} for k in params["keys"]]
        return []

    session = Recorder(graph_rows)
    monkeypatch.setattr(graph_query, "_run_read_query", read)
    monkeypatch.setattr(graph_query, "read_session", lambda: session)
    monkeypatch.setattr(parts, "BUILDERS", {"assets": assets.build_assets})
    monkeypatch.setenv("ARTMIND_PLAN_CATALOG", "1")
    cache.reset()
    yield {"views": views, "topics": topics, "insights": insights, "state": state, "session": session}
    cache.reset()


def _topic(root, slug="termination", domains=("journal",), sha="v1"):
    topics_store.write_page(root, slug, {
        "artmind_topic": 1, "topic": "Termination rights", "slug": slug, "domains": list(domains),
        "anchor": {"kind": "entity", "key": "k1", "name": "Ada", "_id": "e1", "observation_set_hash": "h"},
        "built": "2026-11-02", "sources": [{"doc_id": "d1", "name": "a.md", "content_sha": sha}], "checks": {"ok": True},
    }, "# Termination rights\n\nEither side may end the agreement with notice.\n")


def _snapshot(domain="journal", entities=5, documents=2):
    insights_store.write_run(Run(
        domain=domain, created_at="2026-11-01T00:00:00+00:00", as_of="2026-10-30",
        fingerprint={"entities": entities, "documents": documents}, anchors=[], profile={}, plan=[],
        findings=[Finding("k1", "outcome_lift", "corpus", 0.9, "Calls lift renewals"),
                  Finding("k2", "outcome_lift", "corpus", 0.4, "Visits lift renewals"),
                  Finding("k3", "dormancy", "corpus", 0.5, "Ada went quiet", diff="gone")],
        circles=[], diff={}))


def test_each_kind_has_the_one_record_shape(world):
    _write_view(world["views"], "trips_by_year")
    _topic(world["topics"])
    _snapshot()
    rows, total = assets.list_assets(["journal"])
    assert total == 4 and [r["kind"] for r in rows] == ["view", "topic", "insight", "summary_set"]
    assert all(set(r) == RECORD_KEYS for r in rows)
    view, topic, insight, summary = rows
    assert view["id"] == "trips_by_year" and view["open"] == "artmind query views show trips_by_year --compact"
    assert view["answers"] == "Trips by year which trips last year?" and view["evidence_kind"] == "rows"
    assert view["detail"] == {"version": 2, "params": {"person": {"type": "entity", "entity_class": "PERSON"}}}
    assert topic["id"] == "termination" and topic["answers"].startswith("Termination rights. Either side may end")
    assert topic["anchor"] == {"kind": "entity", "_id": "e1", "key": "k1", "name": "Ada"}
    assert topic["detail"] == {"sources": 1, "checks": {"ok": True}} and topic["stale"] is False
    assert insight["id"] == "outcome_lift" and insight["detail"]["findings"] == 2  # the gone finding is not counted
    assert "Calls lift renewals" in insight["answers"] and insight["stale"] is False
    assert summary["id"] == "journal" and summary["detail"] == {"documents": 2, "with_summary": 1}
    assert summary["stale"] is True and summary["stale_reasons"] == ["1 document(s) without a summary"]


def test_an_invalid_view_is_listed_not_hidden(world):
    _write_view(world["views"], "good")
    _write_view(world["views"], "bad", broken=True)
    rows, _ = assets.list_assets(["journal"], ["view"])
    assert [(r["id"], r["status"]) for r in rows] == [("bad", "invalid"), ("good", "ok")]
    assert rows[0]["detail"]["error"] and rows[0]["answers"] == ""


def test_a_view_of_another_domain_family_is_not_listed(world):
    _write_view(world["views"], "other", domains=("finance",))
    assert assets.list_assets(["journal"], ["view"]) == ([], 0)


def test_a_topic_whose_source_changed_is_stale(world):
    _topic(world["topics"], sha="v0")  # the graph says version v1
    row = assets.list_assets(["journal"], ["topic"])[0][0]
    assert row["stale"] and row["stale_reasons"] == ["source changed: a.md"]
    assert world["session"].queries_containing("UNWIND $docIds")[0][1]["docIds"] == ["d1"]


def test_without_a_topics_folder_there_are_no_topic_assets(world, monkeypatch):
    monkeypatch.setattr(paths, "TOPICS_DIR", None)
    assert assets.list_assets(["journal"], ["topic"]) == ([], 0)


def test_an_insight_snapshot_is_stale_once_the_graph_moved(world):
    _snapshot(entities=4)  # the graph now has 5 entities
    row = assets.list_assets(["journal"], ["insight"])[0][0]
    assert row["stale"] and row["stale_reasons"] == ["entities changed since the snapshot: 4 -> 5"]
    assert assets.list_assets(["journal"], ["insight"], stale_only=True)[1] == 1


def test_stale_only_limit_and_unknown_kind(world):
    _write_view(world["views"], "a")
    _write_view(world["views"], "b")
    rows, total = assets.list_assets(["journal"], ["view"], limit=1)
    assert len(rows) == 1 and total == 2
    assert assets.list_assets(["journal"], stale_only=True)[0][0]["kind"] == "summary_set"
    with pytest.raises(ValueError, match="unknown asset kind"):
        assets.list_assets(["journal"], ["nope"])


def test_several_domains_merge_one_asset_with_the_union_of_its_domains(world):
    _write_view(world["views"], "shared", domains=("journal", "finance"))
    rows, total = assets.list_assets(["journal", "finance"], ["view"])
    assert total == 1 and rows[0]["domains"] == ["finance", "journal"]


def test_assets_follow_a_disk_change_without_a_graph_change(world):
    clock = [1000.0]
    cache.set_clock(lambda: clock[0])
    assert assets.list_assets(["journal"], ["view"])[1] == 0
    _write_view(world["views"], "late")
    clock[0] += cache.FINGERPRINT_TTL_S + 1  # past the fingerprint's memory window; the graph is unchanged
    assert assets.list_assets(["journal"], ["view"])[1] == 1


# ------------------------------------------------------------------ like

class _Embedder:
    """Deterministic vectors: a text is closer to another the more words they share."""

    def __init__(self):
        self.documents = 0
        self.queries = 0

    @staticmethod
    def vec(text):
        words = ["trips", "year", "renewals", "termination", "notice"]
        return [float(w in text.lower()) for w in words] + [0.01]

    def document(self, text):
        self.documents += 1
        return self.vec(text)

    def query(self, text):
        self.queries += 1
        return self.vec(text)


@pytest.fixture
def embedder(monkeypatch, tmp_path):
    from artmind.embedding_cache import EmbeddingCache

    fake = _Embedder()
    monkeypatch.setattr(assets, "_embed_document", fake.document)
    monkeypatch.setattr(assets, "_embed_query", fake.query)
    monkeypatch.setattr(assets, "_embed_key", lambda: "k")
    fake.cache = EmbeddingCache(tmp_path / "emb.sqlite")
    return fake


def test_like_ranks_by_meaning_and_the_second_call_hits_the_cache(world, embedder):
    _write_view(world["views"], "trips_by_year")
    _write_view(world["views"], "renewals", summary="Renewals", examples=("which renewals lapsed?",))
    _topic(world["topics"])
    first = assets.like("notice before termination", ["journal"], ["view", "topic"], k=3, cache=embedder.cache)
    assert [a["id"] for a, _ in first][0] == "termination"
    assert first[0][1] > first[1][1] >= first[2][1]
    docs = embedder.documents
    assert docs == 3  # views and the topic, each embedded once
    again = assets.like("which trips happen each year", ["journal"], ["view", "topic"], k=2, cache=embedder.cache)
    assert embedder.documents == docs and embedder.queries == 2  # only the question was embedded
    assert again[0][0]["id"] == "trips_by_year" and len(again) == 2


def test_like_never_matches_an_invalid_asset(world, embedder):
    _write_view(world["views"], "bad", broken=True)
    assert assets.like("anything", ["journal"], ["view"], cache=embedder.cache) == []


# ------------------------------------------------------------------ CLI

def _run(*args):
    result = CliRunner().invoke(cli, ["query", "assets", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_cli_payload_and_truncation(world):
    _write_view(world["views"], "a")
    _write_view(world["views"], "b")
    out = _run("--domain", "journal", "--kind", "view", "--limit", "1")
    assert out["rows_total"] == 2 and out["truncated"] is True and [r["id"] for r in out["assets"]] == ["a"]


def test_cli_stale_filter_and_bad_kind(world):
    _write_view(world["views"], "a")
    assert [r["kind"] for r in _run("--domain", "journal", "--stale")["assets"]] == ["summary_set"]
    result = CliRunner().invoke(cli, ["query", "assets", "--domain", "journal", "--kind", "nope"])
    assert result.exit_code != 0 and "unknown asset kind" in result.output


def test_cli_like_carries_scores(world, embedder, monkeypatch):
    _write_view(world["views"], "trips_by_year")
    real = assets.like
    monkeypatch.setattr(assets, "like", lambda *a, **kw: real(*a, **{**kw, "cache": embedder.cache}))
    out = _run("--domain", "journal", "--like", "trips each year")
    assert out["assets"][0]["id"] == "trips_by_year" and isinstance(out["assets"][0]["score"], float)


def test_the_command_is_routed():
    import rich_click

    names = [c for g in rich_click.rich_click.COMMAND_GROUPS["artmind query"] for c in g["commands"]]
    assert "assets" in names
