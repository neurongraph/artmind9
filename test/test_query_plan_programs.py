"""A route that chooses among several authored programs at plan time (planner v2 P2b, slice P1).
Synthetic routes and data only; fixtures come from test_query_plan_planner."""

import copy

import pytest
import yaml

from artmind.query_plan import planner, routes
from test_query_plan_planner import _cat, _plan, catalogue_world, world  # noqa: F401  (fixtures and helpers)

ONE = "artmind query vector-text --shape brief --domain {domains} {search_term} --compact"
PHRASE = "artmind query entity-resolve --domain {domains} --topK 25 {phrase} --compact"
TWIN = "artmind query entity-context --domain {domains} --entityId {twin} --shape brief --compact"
YEAR = "artmind query graph pattern7 --domain {domains} --searchTerm {search_term} --limit 50 --compact"


def _run(i, template):
    return {"id": f"r{i}", "run": template}


def _map(over, as_, template, max_=10):
    return [{"id": "m", "map": {"over": over, "as": as_, "max": max_, "do": _run(1, template)}}]


def _choice(kind, when, program, guards=None):
    entry = {"kind": kind, "program": program}
    if when is not None:
        entry["when"] = when
    if guards is not None:
        entry["guards"] = guards
    return entry


def _install(monkeypatch, *choices, route_id="probe"):
    """The packaged routes plus one `probe` route (trigger: the word `probe`) carrying `programs`."""
    data = yaml.safe_load(routes.ROUTES_FILE.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data["routes"].append({"id": route_id, "description": "A probe shape.", "triggers": ["probe"], "needs": [],
                           "read": [], "evidence": ["S1"], "programs": list(choices)})
    data["routes"] = [r for r in data["routes"] if r["id"] in ("search", route_id)]  # no rival triggers
    parsed = routes.parse_routes(data)
    monkeypatch.setattr(routes, "load_routes", lambda: parsed)
    return parsed


def _two(monkeypatch, when, true_kind="absence"):
    _install(monkeypatch,
             _choice(true_kind, when, [_run(1, ONE.replace("vector-text", "graph pattern7"))]),
             _choice("one_step", None, [_run(1, ONE)]))


# ── choosing by plan fact ────────────────────────────────────────────────────────────────

TWINS = [{"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
         {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]}]


@pytest.mark.parametrize("when, question, setup, expected", [
    ("twins > 0", "probe Lakeport visits", "twins", True),
    ("twins > 0", "probe Lakeport visits", None, False),
    ("entities >= 1", "probe Lakeport visits", None, True),
    ("entities >= 1", "probe harbour visits", None, False),
    ("negative", "probe was the sauna ever recorded", None, True),
    ("negative", "probe the sauna visits", None, False),
    ("latest", "probe the latest sauna notes", None, True),
    ("latest", "probe the first sauna notes", None, False),
    ("category", "probe which places", "category", True),
    ("category", "probe harbour visits", None, False),
    ("date_window", "probe sauna visits in 2024", None, True),
    ("date_window", "probe sauna visits", None, False),
    ("document", "probe the policy_alpha notes", "document", True),
    ("document", "probe sauna visits", "document", False),
    ("rare_term", "probe sauna visits", "catalogue", True),
    ("rare_term", "probe greenhouse visits", "catalogue", False),
])
def test_each_plan_fact_selects_its_program(world, monkeypatch, tmp_path, when, question, setup, expected):
    world["views"] = []
    _two(monkeypatch, when)
    if setup == "twins":
        world["entities"]["Lakeport"] = TWINS
    if setup == "category":
        (tmp_path / "journal.yaml").write_text("categories:\n  place: [LOCATION, harbour]\n")
    if setup == "document":
        monkeypatch.setattr(planner.candidates, "document_names", lambda domains: ["policy_alpha.md"])
    if setup == "catalogue":
        cat = _cat(names=[("g1", "Greenhouse", [], "LOCATION", 3), ("q1", "Probe visits", [], "EVENT", 1)],
                   classes=[("LOCATION", 1, "")])
        monkeypatch.setattr(planner, "_get_catalogue", lambda cover: ({"journal": cat}, {"journal": "live"}))
    result = _plan(question)
    assert result["route"]["id"] == "probe", result["route"]
    assert result["program_kind"] == ("absence" if expected else "one_step")
    fact = when.split()[0]
    assert list(result["plan_facts"]) == [fact] and bool(result["plan_facts"][fact]) is expected
    assert ("pattern7" in result["program"][0]["run"]) is expected


def test_chunks_only_and_view_count_facts(world, monkeypatch):
    world["views"] = []
    world["overview"] = [{"domain": "journal", "document_count": 3, "documents": [], "top_classes": [],
                          "graph_extraction": False}]
    _two(monkeypatch, "chunks_only")
    monkeypatch.setattr(planner, "_chunks_only", lambda selected, overview: True)
    result = _plan("probe sauna visits")
    # a chunks-only scope plans survey/search, never a graph route; search chooses by chunks_only
    assert result["route"]["id"] in ("search", "survey")
    assert result["plan_facts"] in ({}, {"chunks_only": True})


def test_the_first_matching_entry_wins_and_the_last_is_the_default(world, monkeypatch):
    world["views"] = []
    _install(monkeypatch,
             _choice("absence", "negative", [_run(1, ONE.replace("vector-text", "graph pattern7"))]),
             _choice("latest", "negative and date_window", [_run(1, ONE.replace("vector-text", "survey"))]),
             _choice("one_step", None, [_run(1, ONE)]))
    both = _plan("probe was the sauna ever recorded in 2024")
    assert both["program_kind"] == "absence"  # the second also holds; the first wins
    assert both["plan_facts"] == {"date_window": True, "negative": True}
    neither = _plan("probe sauna visits")
    assert neither["program_kind"] == "one_step" and neither["plan_facts"] == {"date_window": False, "negative": False}
    assert "vector-text" in neither["steps"][0]["cmd"] or "vector-text" in neither["suggested_steps"][0]["cmd"]


def test_a_when_on_the_last_entry_does_not_remove_the_default(world, monkeypatch):
    world["views"] = []
    _install(monkeypatch,
             _choice("absence", "negative", [_run(1, ONE.replace("vector-text", "graph pattern7"))]),
             _choice("one_step", "date_window", [_run(1, ONE)]))
    assert _plan("probe sauna visits")["program_kind"] == "one_step"


def test_the_payload_carries_the_chosen_program_its_guards_and_steps(world, monkeypatch):
    world["views"] = []
    _install(monkeypatch,
             _choice("absence", "negative", [_run(1, ONE.replace("vector-text", "graph pattern7"))],
                     guards=[{"on": "r1", "if": "rows == 0", "then": "escalate"}]),
             _choice("one_step", None, [_run(1, ONE)]))
    result = _plan("probe was the sauna ever recorded")
    assert result["program_kind"] == "absence" and result["plan_facts"] == {"negative": True}
    assert [n["run"] for n in result["program"]] == [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert "pattern7" in result["program"][0]["run"]
    assert [g["then"]["kind"] for g in result["guards"]] == ["escalate"]
    other = _plan("probe sauna visits")
    assert other["guards"] == [] and "vector-text" in other["program"][0]["run"]


def test_a_view_plan_is_one_step_with_no_facts(world):
    result = _plan("When did Ada travel to Lakeport in 2024?")
    assert result["route"]["id"] == "view"
    assert result["program_kind"] == "one_step" and result["plan_facts"] == {}


def test_a_view_count_question_plans_the_view_beside_a_text_check(world):
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["route"]["id"] == "view"
    assert result["program_kind"] == "view_count" and result["plan_facts"] == {"view_count": True}
    assert [n["kind"] for n in result["program"]] == ["par", "pick"]
    assert result["program"][1]["pick"]["how"] == "union"
    assert [c["run"] for c in result["program"][0]["children"]] == [s["cmd"] for s in result["steps"]]
    assert result["steps"][0]["cmd"].startswith("artmind query views run visits_by_year")
    assert result["steps"][-1]["cmd"].startswith("artmind query vector-text --shape brief --domain journal")


def test_routes_without_programs_keep_their_steps_and_report_their_kind(world):
    world["views"] = []
    result = _plan("What is the favourite place of the team?")
    assert result["route"]["id"] == "superlative"
    assert result["program_kind"] == "one_step" and result["plan_facts"] == {}  # a single authored program
    assert [s["cmd"] for s in result["steps"] + result["suggested_steps"]][1] == (
        "artmind query graph text2cypher --domain journal 'What is the favourite place of the team?' --compact")


def test_a_non_retrieval_intent_has_no_program_kind(world):
    result = _plan("Please update my notes about the boat")
    if result["intent"]["id"] != "retrieve":
        assert result["program_kind"] is None and result["plan_facts"] == {}


# ── map items ────────────────────────────────────────────────────────────────────────────

def test_a_twin_map_runs_the_body_once_per_twin_id(world, monkeypatch):
    world["views"] = []
    world["entities"]["Lakeport"] = TWINS
    _install(monkeypatch, _choice("kinship_aggregate", "twins > 0", _map("twin_ids", "twin", TWIN)),
             _choice("one_step", None, [_run(1, ONE)]))
    result = _plan("probe Lakeport visits")
    assert result["program_kind"] == "kinship_aggregate"
    node = result["program"][0]["map"]
    assert node["items"] == ["l1", "l2"] and node["items_total"] == 2 and node["as"] == "twin"
    assert [e["run"] for e in node["expanded"]] == [TWIN.replace("{domains}", "journal").replace("{twin}", "l1"),
                                                   TWIN.replace("{domains}", "journal").replace("{twin}", "l2")]
    assert all(e["filled"] for e in node["expanded"])
    assert node["do"]["run"] == TWIN and node["do"]["filled"] is False
    assert result["slots"]["twin_ids"]["value"] == ["l1", "l2"] and result["slots"]["twin_ids"]["source"] == "twins"
    cmds = [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert cmds == [e["run"].replace("'", "") for e in node["expanded"]] or len(cmds) == 2
    assert not any("{twin}" in c for c in cmds)


def test_a_years_map_follows_the_date_window(world, monkeypatch):
    world["views"] = []
    _install(monkeypatch, _choice("one_step", None, _map("years", "year", YEAR.replace("{search_term}", "{year}"))))
    result = _plan("probe sauna in 2024")
    node = result["program"][0]["map"]
    assert node["items"] == [2024] and node["items_total"] == 1
    assert "--searchTerm 2024" in node["expanded"][0]["run"]
    assert result["slots"]["years"]["value"] == [2024]


def test_a_map_is_capped_at_max_and_items_total_keeps_the_count(world, monkeypatch, tmp_path):
    world["views"] = []
    (tmp_path / "journal.yaml").write_text("synonyms:\n  sauna: [steam room, bathhouse, hot room]\n")
    _install(monkeypatch, _choice("existence_concept", None, _map("phrasings", "phrase", PHRASE, max_=2)))
    result = _plan("probe was there a sauna")
    node = result["program"][0]["map"]
    assert result["slots"]["phrasings"]["value"][:4] == ["probe sauna", "steam room", "bathhouse", "hot room"]
    assert node["items"] == ["probe sauna", "steam room"] and node["items_total"] == len(result["slots"]["phrasings"]["value"])
    assert len(node["expanded"]) == 2
    cmds = [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert len(cmds) == 2 and "'steam room'" in cmds[1]


def test_phrasings_use_the_catalogue_descriptions_and_drop_the_person(catalogue_world, monkeypatch):
    catalogue_world["views"] = []
    cat = _cat(names=[("p1", "Ada", [], "PERSON", 5)],
               classes=[("PERSON", 3, ""), ("HOT_ROOM", 1, "A sauna, steam or hot room")])
    monkeypatch.setattr(planner, "_get_catalogue", lambda cover: ({"journal": cat}, {"journal": "live"}))
    _install(monkeypatch, _choice("existence_concept", None, _map("phrasings", "phrase", PHRASE)))
    result = _plan("probe was there a sauna with Ada")
    phrasings = result["slots"]["phrasings"]["value"]
    assert "hot room" in phrasings and not any("ada" in p.casefold() for p in phrasings[1:])
    assert len(phrasings) <= 5


def test_family_documents_is_empty_in_this_slice(world, monkeypatch):
    world["views"] = []
    _install(monkeypatch, _choice("latest", None,
                                  _map("family_documents", "doc", "artmind query chunks --domain {domains} {doc} --compact")))
    result = _plan("probe the notes")
    node = result["program"][0]["map"]
    assert node["items"] == [] and node["items_total"] == 0 and node["expanded"] == []
    assert result["slots"]["family_documents"]["value"] == []
    assert result["steps"] == [] and not any("{doc}" in s["cmd"] for s in result["suggested_steps"])


def test_a_programs_route_without_a_map_does_not_add_list_slots(world, monkeypatch):
    world["views"] = []
    _two(monkeypatch, "negative")
    result = _plan("probe sauna visits")
    assert not {"twin_ids", "phrasings", "years", "family_documents"} & set(result["slots"])
