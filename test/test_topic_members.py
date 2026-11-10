"""Members of a set (spec 2026-11-10 §6.4): discovery in the dry run, one subject per kept
member (name + aliases, no period slice) plus the anchors' cell, the plan's members /
discover / dropped, and new candidates in the freshness check. Synthetic names only."""
import pytest

from _neo4j_recorder import Recorder

import _topic_world as w
from artmind.topics import build, freshness, members, plan_store


def _resolve(domains, name, k):
    rows = {"Northland": [{"entity": {"_id": "e-org", "name": "Northland", "entity_class": "ORG"}, "matched_by": ["exact"]},
                          {"entity": {"_id": "e-n", "name": "Northland", "entity_class": "LOCATION"}, "matched_by": ["exact"]}],
            "Capital": [{"entity": {"_id": "e-c", "name": "Capital", "entity_class": "LOCATION"}, "matched_by": ["vector"]}]}
    return {"rows": rows.get(name, [])}


CANDS = [{"id": "x1", "key": "k1", "name": "Harbourtown", "aliases": ["Harbour Town"], "domain": "j", "mentions": 4,
          "first": "2023-01-02", "last": "2024-05-06"},
         {"id": "x2", "key": "k2", "name": "Fogmoor", "aliases": [], "domain": "j", "mentions": 1,
          "first": "2023-03-04", "last": "2023-03-04"}]


def _respond(cypher, params):
    if "e.entity_class = $entityClass" in cypher:
        return CANDS
    if "UNWIND $names AS n" in cypher:
        return [{"name": n, "key": "k1" if n == "Harbourtown" else None,
                 "aliases": ["Harbour Town"] if n == "Harbourtown" else []} for n in params["names"]]
    return []


def test_discovery_resolves_anchors_of_the_class_and_lists_co_mentions():
    s = Recorder(_respond)
    out = members.discover(["Northland", "Capital"], "LOCATION", ["j"], s, kept=["harbour town"], dropped=["Fogmoor"],
                           resolve=_resolve)
    assert [a["_id"] for a in out["anchors"]] == ["e-n", "e-c"]  # the LOCATION exact match beats the ORG one
    cypher, params = s.queries_containing("e.entity_class = $entityClass")[0]
    assert params == {"anchorIds": ["e-n", "e-c"], "entityClass": "LOCATION", "domains": ["j"],
                      "limit": members.CANDIDATES_LIMIT}
    assert "NOT e IN anchors" in cypher and "EXTRACTED_FROM" in cypher
    assert [(c["name"], c["mentions"], c["kept"], c["dropped"]) for c in out["candidates"]] == [
        ("Harbourtown", 4, True, False), ("Fogmoor", 1, False, True)]
    assert members.new_candidates(out) == []


def test_member_records_carry_graph_aliases_and_an_added_member_needs_no_entity():
    s = Recorder(_respond)
    recs = members.member_records(["Harbourtown", "Hill Farm"], ["j"], s)
    assert recs == [{"name": "Harbourtown", "aliases": ["Harbour Town"], "entity_key": "k1"},
                    {"name": "Hill Farm", "aliases": []}]
    assert members.query_text(recs[0]) == "Harbourtown Harbour Town"


@pytest.fixture
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))
    monkeypatch.setenv("ARTMIND_TOPIC_MAP_CONCURRENCY", "1")
    monkeypatch.setattr(w, "MATCHES", {"Harbourtown Harbour Town": ["D1", "D2"], "Hill Farm": ["D3"],
                                       "Northland  Capital": ["D2"]})
    return tmp_path


def _session_factory():
    base, _ = w.session_factory()

    def respond(cypher, params):
        rows = _respond(cypher, params)
        return rows or base._respond(cypher, params)

    s = Recorder(respond)
    return s, (lambda: s)


def _build(tmp_path, **kw):
    s, factory = _session_factory()
    llms = w.LLMs()
    out = build.build_topic("Places in Northland", ["j"], members=["Harbourtown", "Hill Farm"],
                            anchors=["Northland", "Capital"], entity_class="LOCATION", dropped=("Fogmoor",),
                            out=tmp_path / "_Topics", model="m", session_factory=factory, search=w.search,
                            map_llm=llms.map, reduce_llm=llms.reduce, generations={"j": 1}, resolve=_resolve,
                            cards=w.CARDS, **kw)
    return out, llms, s


def test_members_are_subjects_without_a_period_slice_plus_the_anchors_cell(world):
    out, _, s = _build(world, dry_run=True)
    assert out["grid"]["slices"] == {"by": "none", "split": None}  # even though the corpus would slice by year
    assert [x["text"] for x in out["subjects"]] == ["Harbourtown", "Hill Farm", "Northland; Capital"]
    counts = [p["ft_query"] for c, p in s.calls if "passage_text_ft" in c]
    assert counts == [w.ft("Harbourtown Harbour Town"), w.ft("Hill Farm"), w.ft("Northland  Capital")]
    assert [(c["label"], c["matching"]) for c in out["grid"]["cells"]] == [
        ("subject 1", 2), ("subject 2", 1), ("subject 3", 1)]
    assert out["discover"]["candidates_total"] == 2 and out["members"][0]["aliases"] == ["Harbour Town"]


def test_the_plan_records_members_discovery_and_dropped_and_the_page_has_a_timeline(world):
    _build(world)
    spec = plan_store.load_plan("places-in-northland").spec
    assert spec.members == ({"name": "Harbourtown", "aliases": ["Harbour Town"], "entity_key": "k1"},
                            {"name": "Hill Farm", "aliases": []})
    assert spec.discover == {"anchors": ["Northland", "Capital"], "entity_class": "LOCATION"}
    assert spec.dropped == ("Fogmoor",) and spec.queries()[0] == "Harbourtown Harbour Town"
    body = (world / "_Topics" / "places-in-northland.md").read_text()
    assert "## Harbourtown" in body and "## Timeline" in body
    assert build.args_from_plan(spec)["members"] == ["Harbourtown", "Hill Farm"]
    assert build.args_from_plan(spec)["facets"] is None


def test_discovery_without_kept_members_builds_nothing(world):
    s, factory = _session_factory()
    out = build.build_topic("Places in Northland", ["j"], anchors=["Northland"], entity_class="LOCATION",
                            out=world / "_Topics", model="m", session_factory=factory, search=w.search,
                            generations={}, resolve=_resolve, cards=w.CARDS)
    assert out["error"].startswith("discovery lists candidates") and not (world / "plans").exists()


def test_freshness_reports_new_candidates_without_adding_them(world, monkeypatch):
    _build(world)
    monkeypatch.setattr("artmind.vector_query.entity_resolve", _resolve)
    global CANDS
    extra = CANDS + [{"id": "x3", "key": "k3", "name": "Saltbridge", "aliases": [], "domain": "j", "mentions": 2,
                      "first": "2026-01-01", "last": "2026-01-01"}]
    monkeypatch.setattr(__import__(__name__), "CANDS", extra)
    s, _ = _session_factory()
    out = freshness.check(plan_store.load_plan("places-in-northland"), s, keep=False)
    assert out["new_candidates"] == 1 and out["new_candidate_names"] == ["Saltbridge"]
    assert plan_store.load_plan("places-in-northland").spec.members[-1]["name"] == "Hill Farm"
