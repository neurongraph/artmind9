"""The packaged program variants of existence, enumeration, aggregate, entity_profile and document
(planner v2 P2b, slice P3). Synthetic names only; fixtures from test_query_plan_planner."""

import pytest

from artmind import graph_query
from artmind.query_plan import candidates, routes, slots
from artmind.query_plan.program import run_nodes
from test_query_plan_planner import _cat, _plan, catalogue_world, world  # noqa: F401  (fixtures and helpers)

TWINS = [{"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
         {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]}]


def _cmds(result):
    return [s["cmd"] for s in result["steps"] + result["suggested_steps"]]


@pytest.fixture
def common_words(monkeypatch):
    """The hermetic catalogue lacks the question's everyday words, so `rare_term` would hold for every
    question; a real catalogue knows them. Tests of ordinary questions pin it off."""
    monkeypatch.setattr(slots, "rare_term", lambda *a, **k: False)


def _route(route_id):
    return next(r for r in routes.load_routes() if r.id == route_id)


@pytest.mark.parametrize("route_id, kinds", [
    ("existence", ["existence_concept", "absence", "one_step"]),
    ("enumeration", ["draw_connected", "enumeration"]),
    ("aggregate", ["kinship_aggregate", "one_step"]),
    ("entity_profile", ["entity_visits", "one_step"]),
    ("document", ["latest", "one_step"]),
])
def test_each_route_loads_with_its_choices(route_id, kinds):
    route = _route(route_id)
    assert route.chooses and [c.kind for c in route.programs] == kinds
    assert route.programs[-1].when is None and all(c.when for c in route.programs[:-1])


def test_the_defaults_keep_todays_first_command():
    expected = {
        "existence": "artmind query graph pattern7 --domain {domains} --searchTerm {entity_name} --shape brief --compact",
        "enumeration": "artmind query graph pattern7 --domain {domains} --searchTerm {keywords} --limit 50 --shape brief --compact",
        "aggregate": "artmind query graph text2cypher --domain {domains} {question} --compact",
        "entity_profile": "artmind query entity-context --domain {domains} --entityId {entity_id} --shape brief --compact",
        "document": "artmind query graph pattern10 --domain {domains} --documentName {document} --compact",
    }
    for route_id, template in expected.items():
        assert run_nodes(_route(route_id).program)[0].run == template, route_id


# ── existence ────────────────────────────────────────────────────────────────

def test_an_ordinary_existence_question_keeps_the_default(world, common_words):
    world["views"] = []
    result = _plan("Did I visit Lakeport again?")
    assert result["route"]["id"] == "existence"
    assert result["program_kind"] == "one_step"
    assert result["plan_facts"] == {"entities": 1, "negative": False, "rare_term": False}
    assert _cmds(result)[0] == "artmind query graph pattern7 --domain journal --searchTerm Lakeport --shape brief --compact"
    assert [g["then"] for g in result["guards"]] == [{"kind": "replace", "route": "search"}]


def test_a_negative_existence_question_sweeps_the_exact_term_once(world, common_words):
    world["views"] = []
    result = _plan("Did I ever visit Lakeport?")
    assert result["route"]["id"] == "existence" and result["program_kind"] == "absence"
    assert result["plan_facts"]["negative"] is True
    cmds = _cmds(result)
    assert len(cmds) == 2 and "pattern7" in cmds[0] and "--limit 500" in cmds[0] and "--shape brief" in cmds[0]
    assert "vector-text --shape brief" in cmds[1] and "--topK 25" in cmds[1]
    assert [n["kind"] for n in result["program"]] == ["par", "pick"]
    assert result["program"][1]["pick"] == {"from": "sweep", "how": "union", "score": []}


def test_a_rare_word_beside_a_known_person_probes_phrasings_before_the_absence_sweep(catalogue_world, tmp_path):
    catalogue_world["views"] = []
    (tmp_path / "journal.yaml").write_text("synonyms:\n  sauna: [steam room, bathhouse]\n")
    result = _plan("Did I ever have a sauna with Ada?")
    assert result["route"]["id"] == "existence"
    assert result["plan_facts"]["rare_term"] is True and result["plan_facts"]["entities"] >= 1
    assert result["program_kind"] == "existence_concept"  # before `absence`, though `ever` is a negative cue
    node = result["program"][0]["map"]
    assert node["max"] == 5 and node["as"] == "phrase" and "steam room" in node["items"]
    assert 1 < len(node["items"]) <= 5
    assert all("entity-resolve" in e["run"] and "--topK 25" in e["run"] for e in node["expanded"])
    assert not any("ada" in item.casefold() for item in node["items"][1:])


# ── enumeration ──────────────────────────────────────────────────────────────

def test_an_enumeration_without_an_entity_keeps_the_default_and_pages_when_cut(world):
    world["views"] = []
    result = _plan("List all the recorded hobbies")
    assert result["route"]["id"] == "enumeration" and result["program_kind"] == "enumeration"
    assert result["plan_facts"] == {"entities": 0}
    cmds = _cmds(result)
    assert cmds[0].startswith("artmind query graph pattern7 --domain journal --searchTerm ")
    assert "--limit 50" in cmds[0] and "--shape brief" in cmds[0]
    assert "vector-text --shape brief" in cmds[1]
    assert [n["kind"] for n in result["program"]] == ["par", "page", "pick"]
    page = result["program"][1]
    assert page["page"]["until"] == "rows_total" and page["when"] == "s1.truncated"
    assert [(g["on"], g["if"], g["then"]["kind"]) for g in result["guards"]] == [("more", "truncated", "escalate")]


def test_everyone_linked_to_an_entity_draws_the_connected_rows(world):
    world["views"] = []
    result = _plan("List all the visits to Lakeport")
    assert result["route"]["id"] == "enumeration" and result["program_kind"] == "draw_connected"
    cmds = _cmds(result)
    assert "entity-context" in cmds[0] and "--entityId l1" in cmds[0] and "--shape brief" in cmds[0]
    assert "pattern7" in cmds[1] and "--searchTerm Lakeport" in cmds[1] and "--limit 50" in cmds[1]
    page = next(n for n in result["program"] if n["kind"] == "page")
    assert page["page"]["from"] == "linked" and page["page"]["until"] == "rows_total"


# ── aggregate ────────────────────────────────────────────────────────────────

def test_an_ordinary_count_keeps_text2cypher_with_a_search_fallback(world):
    world["views"] = []
    result = _plan("How often did the team meet?")
    assert result["route"]["id"] == "aggregate" and result["program_kind"] == "one_step"
    assert _cmds(result) == ["artmind query graph text2cypher --domain journal 'How often did the team meet?' --compact"]
    assert [g["then"] for g in result["guards"]] == [{"kind": "replace", "route": "search"}]


def test_a_count_over_twins_maps_entity_context_beside_text2cypher(world):
    world["views"] = []
    world["entities"]["Lakeport"] = TWINS
    result = _plan("How many times was Lakeport visited?")
    assert result["route"]["id"] == "aggregate" and result["program_kind"] == "kinship_aggregate"
    assert result["plan_facts"]["twins"] > 0
    par = result["program"][0]
    assert [c["kind"] for c in par["children"]] == ["run", "map"]
    node = par["children"][1]["map"]
    assert node["items"] == ["l1", "l2"] and node["as"] == "twin" and node["max"] == 5
    cmds = _cmds(result)
    assert "text2cypher" in cmds[0]
    assert len(cmds) == 3 and all("entity-context" in c and "--shape brief" in c for c in cmds[1:])


# ── entity profile ───────────────────────────────────────────────────────────

def test_a_plain_profile_keeps_the_default(world):
    world["views"] = []
    result = _plan("Tell me about Lakeport")
    assert result["route"]["id"] == "entity_profile" and result["program_kind"] == "one_step"
    assert result["plan_facts"] == {"occurrence": False}
    assert _cmds(result) == ["artmind query entity-context --domain journal --entityId l1 --shape brief --compact"]


def test_a_visits_question_reads_the_context_and_checks_the_text(world):
    world["views"] = []
    result = _plan("Tell me about the visits to Lakeport")
    assert result["route"]["id"] == "entity_profile" and result["program_kind"] == "entity_visits"
    assert result["plan_facts"] == {"occurrence": True}
    cmds = _cmds(result)
    assert "entity-context" in cmds[0] and "vector-text --shape brief" in cmds[1] and "--topK 25" in cmds[1]
    assert [n["kind"] for n in result["program"]] == ["run", "page", "run"]
    guard = result["guards"][0]
    assert guard["on"] == "check" and guard["if"] == "docs_cited == 0 and ctx.entity_docs > 0"
    assert guard["then"]["kind"] == "escalate"


# ── document: latest and the family read ─────────────────────────────────────

class _Reads:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, cypher, params):
        self.calls.append((cypher, params))
        return self.rows


def test_the_family_read_is_one_bounded_query_with_its_params(monkeypatch):
    reads = _Reads([{"name": "CN012_harbour_v2.md", "valid_from": "2026-03-01"},
                    {"name": "CN12_harbour.md", "valid_from": "2026-01-01"},
                    {"name": "CN012_harbour_v2.md", "valid_from": "2026-03-01"}])
    monkeypatch.setattr(graph_query, "_run_read_query", reads)
    pool = ["CN12_harbour.md", "CN012_harbour_v2.md", "CN13_other.md", "policy_alpha.md"]
    family = candidates.family_documents(["journal"], "CN12_harbour.md", pool)
    assert family == ["CN012_harbour_v2.md", "CN12_harbour.md"]
    ((cypher, params),) = reads.calls
    assert params == {"domains": ["journal"], "name": "CN12_harbour.md",
                      "names": ["CN12_harbour.md", "CN012_harbour_v2.md"], "limit": 20}
    assert "SUPERSEDES*1..5" in cypher and "LIMIT $limit" in cypher and "ORDER BY" in cypher
    assert not any(w in cypher.upper() for w in ("CREATE", "MERGE", "DELETE", "SET "))


def test_the_family_read_without_a_reference_uses_only_the_chain_and_falls_back_to_the_target(monkeypatch):
    reads = _Reads([])
    monkeypatch.setattr(graph_query, "_run_read_query", reads)
    assert candidates.family_documents(["journal"], "policy_alpha.md", ["policy_alpha.md", "CN12_x.md"]) == ["policy_alpha.md"]
    assert reads.calls[0][1]["names"] == ["policy_alpha.md"]

    def boom(cypher, params):
        raise RuntimeError("no graph")

    monkeypatch.setattr(graph_query, "_run_read_query", boom)
    assert candidates.family_documents(["journal"], "policy_alpha.md", []) == ["policy_alpha.md"]


def test_the_family_names_are_capped(monkeypatch):
    reads = _Reads([])
    monkeypatch.setattr(graph_query, "_run_read_query", reads)
    pool = [f"CN12_v{i}.md" for i in range(60)]
    candidates.family_documents(["journal"], "CN12_harbour.md", pool)
    assert len(reads.calls[0][1]["names"]) == 21  # the target plus at most 20 same-reference names


def test_a_latest_question_maps_pattern10_over_the_family_newest_first(world, monkeypatch):
    world["views"] = []
    monkeypatch.setattr(candidates, "document_names", lambda domains: ["CN12_harbour.md", "CN012_harbour_v2.md"])
    reads = _Reads([{"name": "CN012_harbour_v2.md", "valid_from": "2026-03-01"},
                    {"name": "CN12_harbour.md", "valid_from": "2026-01-01"}])
    monkeypatch.setattr(graph_query, "_run_read_query", reads)
    result = _plan("Summarise the latest CN12_harbour.md")
    assert result["route"]["id"] == "document" and result["program_kind"] == "latest"
    assert result["plan_facts"] == {"document": True, "latest": True}
    node = result["program"][0]["map"]
    assert node["items"] == ["CN012_harbour_v2.md", "CN12_harbour.md"] and node["max"] == 10
    assert node["expanded"][0]["run"].endswith("--documentName CN012_harbour_v2.md --compact")
    assert result["slots"]["family_documents"]["value"] == node["items"]
    assert len(reads.calls) == 1 and reads.calls[0][1]["domains"] == ["journal"]


def test_an_ordinary_document_question_keeps_the_default_and_never_reads_the_family(world, monkeypatch):
    world["views"] = []
    monkeypatch.setattr(candidates, "document_names", lambda domains: ["policy_alpha.md"])
    reads = _Reads([])
    monkeypatch.setattr(graph_query, "_run_read_query", reads)
    result = _plan("What does policy_alpha.md say?")
    assert result["route"]["id"] == "document" and result["program_kind"] == "one_step"
    assert reads.calls == [] and "family_documents" not in result["slots"]
    cmds = _cmds(result)
    assert cmds[0] == "artmind query graph pattern10 --domain journal --documentName policy_alpha.md --compact"
    assert "--includeHistory" in cmds[1]  # the v1 fallback, now a conditional node
    assert result["program"][1]["when"] == "s1.rows == 0"
