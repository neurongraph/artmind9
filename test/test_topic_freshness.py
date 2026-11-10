"""Topic plan freshness (spec 2026-11-10 §8.1): the generation short-circuit, the grid
re-count, changed and retired sources, and the stored result. No LLM; synthetic text."""
import json
from dataclasses import replace

import pytest

from _neo4j_recorder import Recorder

import _topic_world as w
from artmind.topics import freshness, plan_store
from artmind.topics import plan as model
from artmind.topics import store as pages


@pytest.fixture
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))
    spec = model.PlanSpec(slug="garden", topic="Garden", version=1, domains=("j",),
                          subjects=("tomato planting",), by="year")
    skill = model.default_skill_md(spec)
    built = {"at": "2026-11-01T00:00:00Z", "generations": {"j": 4},
             "grid": [{"subject": 1, "period": "2023", "domain": None, "matching": 2, "taken": 2}],
             "documents": ["D1", "D2"], "version": 1, "fingerprint": model.fingerprint(spec, skill)}
    plan_store.write_plan(replace(spec, built=built), skill)
    pages.write_page(tmp_path / "_Topics", "garden", {"artmind_topic": 1, "topic": "Garden", "slug": "garden",
                                                      "sources": [{"doc_id": "D1", "content_sha": "v-D1"},
                                                                  {"doc_id": "D2", "content_sha": "v-D2"}]}, "x")
    return tmp_path


def _session(generation, matches, live=("D1", "D2"), versions=None):
    versions = versions or {}

    def respond(cypher, params):
        if "DomainState" in cypher:
            return [{"domain": "j", "generation": generation}]
        if "passage_text_ft" in cypher:
            return [{"scanned": len(matches), "docs": [{"doc_id": d, "date": t, "domain": "j"} for d, t in matches]}]
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "live": d in live, "version": versions.get(d, f"v-{d}")} for d in params["docIds"]]
        return []
    return Recorder(respond)


def test_an_unmoved_generation_is_fresh_without_a_recount(world):
    s = _session(4, [])
    out = freshness.check(plan_store.load_plan("garden"), s, now="t")
    assert out["status"] == "fresh" and out["reasons"] == []
    assert not s.queries_containing("passage_text_ft")  # the short-circuit: one generation read
    assert json.loads((world / "logs" / "topic-freshness" / "garden.json").read_text())["status"] == "fresh"
    assert freshness.stored("garden")["checked_at"] == "t"


def test_a_new_matching_day_makes_the_plan_stale_naming_the_cell(world):
    s = _session(5, [("D1", "2023-03-01"), ("D2", "2023-07-12"), ("D9", "2026-11-09")])
    out = freshness.check(plan_store.load_plan("garden"), s)
    assert out["status"] == "stale" and out["new_documents"] == 1 and out["cells_grown"] == ["subject 1 / 2026"]
    assert out["changed"] == 0 and out["retired"] == 0
    assert s.queries_containing("UNWIND $docIds")[0][1]["docIds"] == ["D1", "D2"]


def test_changed_and_retired_sources_are_counted(world):
    s = _session(5, [("D1", "2023-03-01"), ("D2", "2023-07-12")], live=("D1",), versions={"D1": "v-new"})
    out = freshness.check(plan_store.load_plan("garden"), s)
    assert (out["status"], out["changed"], out["retired"], out["new_documents"]) == ("stale", 1, 1, 0)


def test_a_moved_generation_with_nothing_new_is_fresh(world):
    s = _session(9, [("D1", "2023-03-01"), ("D2", "2023-07-12")])
    assert freshness.check(plan_store.load_plan("garden"), s)["status"] == "fresh"


def test_an_unreachable_graph_is_unknown(world):
    class Down:
        def run(self, *a, **k):
            raise RuntimeError("connection refused")

    out = freshness.check(plan_store.load_plan("garden"), Down())
    assert out["status"] == "unknown" and "graph unreachable" in out["reasons"][0]


def test_plan_payload_and_list_carry_freshness(world, monkeypatch):
    from artmind.topics import run

    monkeypatch.setattr("artmind.graph_query.read_session", lambda: _session(4, []))
    loaded = plan_store.load_plan("garden")
    assert run.plan_payload(loaded)["freshness"]["status"] == "fresh"
    assert run.list_payload([loaded], [])["plans"][0]["freshness"]["status"] == "fresh"
