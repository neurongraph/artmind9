"""Topic plans (spec 2026-11-10 §5): the plan.yaml contract, the store's version guard, the
grid's keyword counts and fair cut, the per-cell gather, and `query topics plan|run`.
Synthetic text only."""
import json

import pytest
import yaml
from click.testing import CliRunner

from _neo4j_recorder import Recorder

from artmind.topics import grid
from artmind.topics import plan as model
from artmind.topics import plan_store
from artmind.topics import run as topic_run
from artmind.topics.plan import PlanError, PlanSpec


def _spec(**kw):
    base = dict(slug="garden-years", topic="Garden work over the years", version=1, domains=("journal",),
                subjects=("tomato planting", "compost heap"), by="year", split="none")
    base.update(kw)
    return PlanSpec(**base)


@pytest.fixture
def plans(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "topic_plans")
    return tmp_path / "topic_plans"


def _save(spec, skill=None, built=True):
    skill = skill if skill is not None else model.default_skill_md(spec)
    if built:
        from dataclasses import replace

        spec = replace(spec, built={"at": "2026-11-10T00:00:00Z", "version": spec.version,
                                    "fingerprint": model.fingerprint(spec, skill)})
    return plan_store.write_plan(spec, skill)


# ── the contract ───────────────────────────────────────────────────────────────


def test_parse_round_trips_the_yaml_the_store_writes(plans):
    folder = _save(_spec())
    data = yaml.safe_load((folder / "plan.yaml").read_text())
    assert list(data)[:9] == ["format", "slug", "topic", "version", "domains", "subjects", "slices", "per_cell",
                              "max_docs"]
    assert data["slices"] == {"by": "year", "split": "none"} and data["refresh"] == "manual"
    loaded = plan_store.load_plan("garden-years")
    assert loaded.spec.subjects == ("tomato planting", "compost heap") and loaded.spec.per_cell == 15


@pytest.mark.parametrize("change, message", [
    ({"extra": 1}, "unknown key"),
    ({"slug": "Bad Slug"}, "slug must be"),
    ({"slices": {"by": "auto"}}, "slices.by must be one of"),
    ({"slices": {"split": "year"}}, "slices.split must be one of"),
    ({"subjects": []}, "subjects must be a non-empty list"),
    ({"subjects": ["a", "a"]}, "duplicate"),
    ({"max_docs": 301}, "max_docs must be an integer between 1 and 300"),
    ({"per_cell": 0}, "per_cell must be"),
    ({"refresh": "daily"}, "refresh must be one of"),
    ({"format": 2}, "format must be 1"),
])
def test_the_contract_names_the_broken_rule(change, message):
    data = {**_spec().to_yaml_dict(), **change}
    with pytest.raises(PlanError, match=message):
        model.parse_plan(data, "garden-years" if "slug" not in change else None)


def test_the_folder_name_must_be_the_slug():
    with pytest.raises(PlanError, match="must match the folder name"):
        model.parse_plan(_spec().to_yaml_dict(), "other")


def test_skill_md_needs_frontmatter_naming_the_plan():
    with pytest.raises(PlanError, match="frontmatter name"):
        model.check_skill_md("---\nname: other\ndescription: d\n---\n", "garden-years")
    model.check_skill_md(model.default_skill_md(_spec()), "garden-years")


def test_default_skill_md_names_every_subject_and_the_run_command():
    text = model.default_skill_md(_spec())
    assert "artmind query topics run garden-years --shape brief --compact" in text
    assert "1. tomato planting (`--subject 1`)" in text and "2. compost heap (`--subject 2`)" in text
    assert "one cell per year" in text


# ── the version guard (the view rule) ──────────────────────────────────────────


def test_a_hand_edit_without_a_bump_is_refused(plans):
    folder = _save(_spec())
    data = yaml.safe_load((folder / "plan.yaml").read_text())
    data["subjects"].append("seed swaps")
    (folder / "plan.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(PlanError, match="was not bumped; set `version: 2`"):
        plan_store.load_plan("garden-years")
    data["version"] = 2
    (folder / "plan.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    assert plan_store.load_plan("garden-years").spec.version == 2


def test_an_edited_skill_md_counts_as_a_change(plans):
    folder = _save(_spec())
    (folder / "SKILL.md").write_text((folder / "SKILL.md").read_text() + "\nMore guidance.\n")
    with pytest.raises(PlanError, match="was not bumped"):
        plan_store.load_plan("garden-years")


def test_a_downgrade_is_refused(plans):
    folder = _save(_spec(version=3))
    data = yaml.safe_load((folder / "plan.yaml").read_text())
    data["version"] = 2
    (folder / "plan.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(PlanError, match="downgrade"):
        plan_store.load_plan("garden-years")


def test_a_plan_without_a_built_record_has_nothing_to_compare(plans):
    _save(_spec(), built=False)
    assert plan_store.load_plan("garden-years").spec.version == 1


def test_next_version_bumps_only_on_a_change(plans):
    spec = _spec()
    _save(spec)
    old = plan_store.existing("garden-years")
    assert plan_store.next_version(spec, old, old.skill_md) == 1
    assert plan_store.next_version(_spec(subjects=("tomato planting",)), old, old.skill_md) == 2
    assert plan_store.next_version(spec, old, old.skill_md + "x") == 2
    assert plan_store.next_version(spec, None, "") == 1


def test_the_save_is_atomic_and_leaves_no_scratch(plans):
    _save(_spec())
    _save(_spec(version=2))
    assert sorted(p.name for p in plans.iterdir()) == ["garden-years"]
    assert sorted(p.name for p in (plans / "garden-years").iterdir()) == ["SKILL.md", "plan.yaml"]


def test_list_filters_by_domain_and_lists_invalid_folders(plans):
    _save(_spec())
    _save(_spec(slug="bank-rules", domains=("banking",)))
    (plans / "broken").mkdir()
    (plans / "broken" / "plan.yaml").write_text("format: 1\n")
    good, invalid = plan_store.list_plans(["journal"])
    assert [p.spec.slug for p in good] == ["garden-years"]
    assert [i["slug"] for i in invalid] == ["broken"]
    good, _ = plan_store.list_plans(["banking.policies"])  # a child of a plan domain is in scope
    assert [p.spec.slug for p in good] == ["bank-rules"]


# ── the grid ───────────────────────────────────────────────────────────────────


def _matching(by_subject):
    """A responder: per ft_query, the matching documents [(doc_id, date, domain)]."""
    def respond(cypher, params):
        if "passage_text_ft" in cypher and "hit_limit" in params:
            docs = by_subject.get(params["ft_query"], [])
            return [{"scanned": len(docs), "docs": [{"doc_id": d, "date": t, "domain": dom} for d, t, dom in docs]}]
        return []
    return respond


def _ft(text):
    from artmind.graph_query import sanitize_lucene_query

    return sanitize_lucene_query(text, drop_stopwords=True)


def test_one_count_query_per_subject_with_the_subject_words_and_scope():
    s = Recorder(_matching({_ft("tomato planting"): [("a", "2023-04-01", "journal"), ("b", "2024-05-01", "journal")],
                            _ft("compost heap"): [("c", "2024-06-01", "journal")]}))
    cells, report = grid.plan_cells(["tomato planting", "compost heap"], ["journal"], "year", "none", 15, 150,
                                    session=s, expand=lambda d: list(d))
    calls = s.queries_containing("passage_text_ft")
    assert [p["ft_query"] for _, p in calls] == [_ft("tomato planting"), _ft("compost heap")]
    assert all(p["domains"] == ["journal"] and p["hit_limit"] == grid.HIT_LIMIT for _, p in calls)
    assert "MATCH (d:Document) WHERE d.id IN ids" in calls[0][0]
    # every subject gets every period seen, zeros included
    assert [(c.subject, c.period, c.matching, c.take) for c in cells] == [
        (1, "2023", 1, 1), (1, "2024", 1, 1), (2, "2023", 0, 0), (2, "2024", 1, 1)]
    assert report == {"cut": False, "slots": 3, "wanted": 3, "max_docs": 150}


def test_domain_split_groups_by_child_of_the_plan_domain():
    s = Recorder(_matching({_ft("fees"): [("a", None, "banking.policies.retail"), ("b", None, "banking.forms"),
                                          ("c", None, "banking")]}))
    cells, _ = grid.plan_cells(["fees"], ["banking"], "none", "domain", 15, 150, session=s,
                               expand=lambda d: ["banking", "banking.forms", "banking.policies.retail"])
    assert [(c.domain, c.period, c.matching) for c in cells] == [
        ("banking", None, 1), ("banking.forms", None, 1), ("banking.policies", None, 1)]


def test_undated_documents_are_counted_but_never_gathered():
    s = Recorder(_matching({_ft("tomato"): [("a", None, "j"), ("b", "2023-01-02", "j")]}))
    cells, _ = grid.plan_cells(["tomato"], ["j"], "year", "none", 15, 150, session=s, expand=lambda d: d)
    undated = [c for c in cells if c.period == "undated"][0]
    assert undated.matching == 1 and undated.take == 0


def test_the_fair_cut_is_round_robin_over_cells():
    cells = [grid.Cell(1, "2023", None, 40, 15), grid.Cell(1, "2024", None, 3, 3), grid.Cell(2, "2023", None, 10, 10)]
    report = grid.fair_cut(cells, 10)
    assert [c.take for c in cells] == [4, 3, 3] and report["cut"] and report["wanted"] == 28


def test_a_full_index_scan_makes_the_count_a_floor(monkeypatch):
    monkeypatch.setattr(grid, "HIT_LIMIT", 2)
    s = Recorder(lambda c, p: [{"scanned": 2, "docs": [{"doc_id": "a", "date": None, "domain": "j"}]}])
    cells, _ = grid.plan_cells(["tomato"], ["j"], "none", "none", 15, 150, session=s, expand=lambda d: d)
    assert cells[0].at_least and cells[0].to_json(gathered=False)["at_least"] is True


def _survey(rows_by_call):
    calls = []

    def search(domains, question, **kw):
        calls.append((domains, question, kw))
        key = (question, kw.get("date_from"))
        return {"documents": [{"doc_id": d, "title": f"T{d}", "name": f"{d}.md", "domain": domains[0],
                               "date": kw.get("date_from"), "status": "current", "summary": f"sum {d}",
                               "passages": [{"id": f"{d}_p01", "chunk_id": f"{d}_c", "text": f"text {d}",
                                             "relevance": 0.5}]}
                              for d in rows_by_call.get(key, [])]}
    return search, calls


def test_gather_runs_one_survey_per_cell_with_its_window_scope_and_take():
    cells = [grid.Cell(1, "2023", None, 5, 2), grid.Cell(1, "2024", None, 0, 0), grid.Cell(2, None, "j.child", 1, 1)]
    search, calls = _survey({("tomato", "2023-01-01"): ["a", "b", "c"], ("compost", None): ["a"]})
    materials, per_cell = grid.gather(["tomato", "compost"], cells, ["j"], passages=3, search=search, concurrency=1)
    assert calls == [(["j"], "tomato", {"top_docs": 2, "passages": 3, "date_from": "2023-01-01",
                                        "date_to": "2023-12-31"}),
                     (["j.child"], "compost", {"top_docs": 1, "passages": 3})]
    assert cells[0].doc_ids == ["a", "b"] and cells[2].doc_ids == ["a"]
    a = next(m for m in materials if m.doc_id == "a")
    assert a.cells == ["subject 1 / 2023", "subject 2 / j.child"] and [s.id for s in a.sources] == ["a_p01"]
    assert ("subject 1 / 2023", "b") in per_cell


def test_month_bounds_end_on_the_last_day():
    assert grid.period_bounds("2024-02") == ("2024-02-01", "2024-02-29")
    assert grid.period_bounds("undated") == (None, None)


# ── query topics plan | run ────────────────────────────────────────────────────


def _patch_graph(monkeypatch, by_subject):
    import artmind.graph_query as gq

    monkeypatch.setattr(gq, "read_session", lambda: Recorder(_matching(by_subject)))
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))


def test_run_orders_rows_by_rank_across_cells_and_lists_a_repeat_once(plans, monkeypatch):
    _save(_spec())
    _patch_graph(monkeypatch, {_ft("tomato planting"): [("a", "2023-04-01", "journal"), ("b", "2023-05-01", "journal")],
                               _ft("compost heap"): [("a", "2023-04-01", "journal")]})
    search, _ = _survey({("tomato planting", "2023-01-01"): ["a", "b"], ("compost heap", "2023-01-01"): ["a"]})
    out = topic_run.run_plan(plan_store.load_plan("garden-years"), shape="brief", search=search)
    # by rank across cells: each cell's rank 1 first
    assert [(r["cell"], r["rank"], r["doc_id"]) for r in out["rows"]] == [
        ("subject 1 / 2023", 1, "a"), ("subject 2 / 2023", 1, "a"), ("subject 1 / 2023", 2, "b")]
    assert out["rows"][0]["passage"]["passage_id"] == "a_p01"
    assert out["rows"][1]["seen_in"] == "subject 1 / 2023" and "passage" not in out["rows"][1]
    assert out["cells"][0] == {"label": "subject 1 / 2023", "subject": 1, "period": "2023", "domain": None,
                               "matching": 2, "take": 2, "taken": 2, "partial": False}
    assert out["documents"] == 2 and out["complete"] is True


def test_run_one_subject_or_one_cell(plans, monkeypatch):
    _save(_spec())
    _patch_graph(monkeypatch, {_ft("tomato planting"): [("a", "2023-04-01", "journal"), ("b", "2024-05-01", "journal")]})
    search, calls = _survey({("tomato planting", "2024-01-01"): ["b"]})
    out = topic_run.run_plan(plan_store.load_plan("garden-years"), subject=1, cell="2024", search=search)
    assert [c["label"] for c in out["cells"]] == ["subject 1 / 2024"] and len(calls) == 1
    assert out["rows"][0]["passages"][0]["text"] == "text b"  # full shape
    with pytest.raises(PlanError, match="--subject must be between 1 and 2"):
        topic_run.run_plan(plan_store.load_plan("garden-years"), subject=3, search=search)
    with pytest.raises(PlanError, match="matches no cell"):
        topic_run.run_plan(plan_store.load_plan("garden-years"), cell="1999", search=search)


def test_a_partial_cell_marks_the_run_incomplete(plans, monkeypatch):
    _save(_spec(per_cell=1))
    _patch_graph(monkeypatch, {_ft("tomato planting"): [("a", "2023-04-01", "journal"), ("b", "2023-05-01", "journal")]})
    search, _ = _survey({("tomato planting", "2023-01-01"): ["a", "b"]})
    out = topic_run.run_plan(plan_store.load_plan("garden-years"), subject=1, search=search)
    assert out["complete"] is False and out["partial_cells"] == ["subject 1 / 2023"]


def test_cli_plan_list_show_and_run(plans, monkeypatch):
    from artmind.cli import cli

    _save(_spec())
    _patch_graph(monkeypatch, {_ft("tomato planting"): [("a", "2023-04-01", "journal")]})
    r = CliRunner().invoke(cli, ["query", "topics", "plan", "--list", "--compact"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["command"] == "query topics plan" and [p["slug"] for p in out["plans"]] == ["garden-years"]
    r = CliRunner().invoke(cli, ["query", "topics", "plan", "garden-years", "--compact"])
    out = json.loads(r.output)
    assert out["plan"]["subjects"][0] == {"n": 1, "text": "tomato planting"} and "name: garden-years" in out["skill"]
    assert out["cells"][0]["matching_now"] == 1
    assert CliRunner().invoke(cli, ["query", "topics", "plan"]).exit_code == 2
    search, _ = _survey({("tomato planting", "2023-01-01"): ["a"]})
    monkeypatch.setattr("artmind.survey.survey_search", search)
    r = CliRunner().invoke(cli, ["query", "topics", "run", "garden-years", "--shape", "brief", "--compact"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["command"] == "query topics run" and out["rows"][0]["doc_id"] == "a"
    r = CliRunner().invoke(cli, ["query", "topics", "run", "missing"])
    assert r.exit_code == 1 and "no topic plan named 'missing'" in r.output


def test_cli_validate_reports_each_plan_and_exits_1_on_failure(plans):
    from artmind.cli import cli

    _save(_spec())
    r = CliRunner().invoke(cli, ["topic", "plan", "validate", "--compact"])
    assert r.exit_code == 0 and json.loads(r.output)["results"][0]["valid"] is True
    (plans / "garden-years" / "SKILL.md").write_text("no frontmatter")
    r = CliRunner().invoke(cli, ["topic", "plan", "validate", "garden-years", "--compact"])
    assert r.exit_code == 1 and "frontmatter" in json.loads(r.output)["results"][0]["error"]


def test_a_run_is_a_survey_layer_span_and_its_rows_are_the_budgeted_list():
    from artmind.cli_conventions import LIST_KEY
    from artmind.traces.path import layer_of

    assert layer_of("query topics run") == "survey" and LIST_KEY["query topics run"] == "rows"


# ── the climb-one-level signposts (§9.1) ───────────────────────────────────────


def test_survey_points_to_a_matching_plan_only_when_coverage_is_partial():
    from artmind import hints

    plans = [("garden-years", "Garden work over the years", ["tomato planting", "compost heap"]),
             ("bank-rules", "Bank fee rules", ["overdraft fees"])]
    low = {"coverage": {"matching_documents": 40, "shown": 10}}
    assert hints.survey_plan_next(low, plans, "when did I do tomato planting in the garden") == \
        "artmind query topics run garden-years --shape brief --compact"
    assert hints.survey_plan_next({"coverage": {"matching_documents": 10, "shown": 10}}, plans,
                                  "tomato planting garden") is None
    assert hints.survey_plan_next(low, plans, "what is the weather") is None  # shares no two words
    assert hints.survey_plan_next({}, plans, "tomato planting garden") is None


def test_entity_context_evidence_cut_points_to_a_survey_on_the_name():
    from artmind import hints

    row = {"entityData": {"name": "Compost Club"}, "cut": {"chunks": {"shown": 8, "total": 30}}}
    assert hints.entity_context_evidence_cut(row, ["j"]) == \
        "artmind query survey --domain j --shape brief --compact 'Compost Club'"
    assert hints.entity_context_evidence_cut({**row, "cut": {"chunks": {"shown": 8, "total": 8}}}, ["j"]) is None
    assert hints.entity_context_evidence_cut({"entityData": {"name": "x"}}, ["j"]) is None


def test_the_tool_gate_refuses_page_reads_and_names_the_plan():
    from artmind.webui import tool_gate

    assert not tool_gate.is_allowed_bash("artmind topic show garden-years --compact")
    assert not tool_gate.is_allowed_bash("artmind topic list")
    assert tool_gate.is_allowed_bash("artmind query topics run garden-years --shape brief --compact")
    assert "artmind query topics run <slug>" in tool_gate.denial_message("artmind topic show x")
