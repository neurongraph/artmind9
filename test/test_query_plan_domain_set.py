"""The domain decision as a set (fix B1): named, chunks-only-all, margin, family parent, coverage.
Synthetic names only."""

import shlex

import pytest

from artmind.query_plan import candidates, domain_set, planner
from artmind.query_plan import config as plan_config
from artmind.query_plan.classify import Answer
from artmind.query_plan.replay import _domains_match
from test_query_plan_planner import FakeClassifier, _plan, _use, world  # noqa: F401  (fixture)

ALL = planner.ALL_DOMAINS


def _rows(*names, chunks=()):
    return [{"domain": n, "document_count": 2, "documents": [f"{n}_doc.md"], "top_classes": ["THING"],
             **({"graph_extraction": False} if n in chunks else {})} for n in names]


def _decide(question, rows, probabilities, choice=None, **kw):
    options = candidates.domain_options(rows)
    probabilities = {**{o.key: 0.0 for o in options}, ALL: 0.0, **probabilities}
    choice = choice or max(probabilities, key=probabilities.get)
    answer = Answer(choice, probabilities, probabilities[choice])
    return domain_set.decide(question, options, rows, answer, ALL, **kw)


def _domain_answer(probabilities: dict[str, float]) -> Answer:
    choice = max(probabilities, key=probabilities.get)
    return Answer(choice, probabilities, probabilities[choice])


# ── the rules, in code ────────────────────────────────────────────────────────────────────

def test_a_named_domain_wins_whatever_the_mass():
    got = _decide("what does the gardening notebook say about frost?", _rows("gardening", "travel"),
                  {"travel": 0.9, "gardening": 0.05})
    assert (got.selected, got.set_rule, got.coverage, got.named) == (["gardening"], "named", 1.0, ["gardening"])


def test_a_name_with_spaces_and_an_alias_both_name_a_domain():
    rows = _rows("field_notes", "travel")
    assert _decide("in field notes, what frost dates?", rows, {"travel": 0.9}).selected == ["field_notes"]
    got = _decide("what does my almanac say?", rows, {"travel": 0.9}, aliases={"field_notes": ("almanac",)})
    assert got.selected == ["field_notes"] and got.set_rule == "named"


def test_a_child_name_beats_the_family_name_inside_it():
    rows = _rows("shop.orders", "shop.stock", "travel")
    got = _decide("what does shop orders say about returns?", rows, {"travel": 0.9})
    assert got.selected == ["shop.orders"]
    assert _decide("what does the shop sell?", rows, {"travel": 0.9}).selected == ["shop"]


def test_a_word_inside_another_word_names_nothing():
    got = _decide("any travelling plans?", _rows("travel", "gardening"), {"travel": 0.7, "gardening": 0.1})
    assert got.set_rule == "single" and got.named == []


def test_two_chunks_only_domains_are_both_searched():
    rows = _rows("letters", "replies", chunks=("letters", "replies"))
    got = _decide("what was agreed about the deadline?", rows, {"letters": 0.95, "replies": 0.02})
    assert (got.selected, got.set_rule, got.coverage, got.source) == (["letters", "replies"], "chunks_only_all", 1.0, "set")


def test_chunks_only_all_also_covers_the_no_single_domain_answer():
    rows = _rows("letters", "replies", chunks=("letters", "replies"))
    got = _decide("anything?", rows, {ALL: 1.0}, choice=ALL)
    assert got.selected == ["letters", "replies"] and got.set_rule == "chunks_only_all"


def test_a_mixed_vault_is_not_chunks_only_all():
    rows = _rows("letters", "garden", chunks=("letters",))
    got = _decide("what frost dates?", rows, {"garden": 0.8, "letters": 0.1})
    assert got.selected == ["garden"] and got.set_rule == "single"


def test_runners_up_within_the_margin_join_the_top_domain():
    rows = _rows("garden", "travel", "kitchen")
    got = _decide("q", rows, {"garden": 0.45, "travel": 0.35, "kitchen": 0.1, ALL: 0.1})
    assert got.selected == ["garden", "travel"] and got.set_rule == "margin" and got.source == "set"
    assert got.coverage == pytest.approx(0.8)


def test_a_clear_winner_stays_alone_and_its_coverage_is_its_mass():
    rows = _rows("garden", "travel", "kitchen")
    got = _decide("q", rows, {"garden": 0.6, "travel": 0.25, "kitchen": 0.15})
    assert (got.selected, got.set_rule, got.source) == (["garden"], "single", "matched")
    assert got.coverage == pytest.approx(0.6)


def test_mass_spread_over_three_children_selects_the_family_parent():
    rows = _rows("shop.orders", "shop.stock", "shop.staff", "travel")
    got = _decide("q", rows, {"shop.orders": 0.4, "shop.stock": 0.2, "shop.staff": 0.2, "travel": 0.1, ALL: 0.1})
    assert (got.selected, got.set_rule, got.source) == (["shop"], "family_parent", "parent")
    assert got.coverage == pytest.approx(0.8)  # the parent covers all three children


def test_two_spread_children_are_a_margin_set_not_the_parent():
    rows = _rows("shop.orders", "shop.stock", "shop.staff", "travel")
    got = _decide("q", rows, {"shop.orders": 0.45, "shop.stock": 0.4, "shop.staff": 0.05, "travel": 0.1})
    assert got.selected == ["shop.orders", "shop.stock"] and got.set_rule == "margin"


def test_no_single_domain_still_searches_everything_at_zero():
    got = _decide("q", _rows("garden", "travel"), {ALL: 0.9, "garden": 0.1}, choice=ALL)
    assert (got.set_rule, got.coverage, got.source) == ("all", 0.0, "all")
    assert got.selected == ["garden", "travel"]


def test_a_set_covering_the_whole_graph_vault_earns_no_confidence_for_widening():
    rows = _rows("shop.orders", "shop.stock", "shop.staff")
    got = _decide("q", rows, {"shop.orders": 0.4, "shop.stock": 0.3, "shop.staff": 0.3})
    assert got.selected == ["shop"] and got.whole_vault is True
    assert got.coverage == pytest.approx(1.0) and got.stage == pytest.approx(0.4)  # the top choice, like `all`
    named = _decide("what does the shop sell?", rows, {"shop.orders": 0.5, "shop.stock": 0.5})
    assert named.set_rule == "named" and named.whole_vault is True and named.stage == pytest.approx(0.5)


def test_a_set_covering_part_of_the_vault_takes_its_coverage_as_the_stage():
    got = _decide("q", _rows("garden", "travel", "kitchen"), {"garden": 0.45, "travel": 0.35, "kitchen": 0.05})
    assert got.whole_vault is False and got.stage == got.coverage == pytest.approx(0.8)


def test_a_chunks_only_vault_searched_whole_keeps_full_confidence():
    rows = _rows("letters", "replies", chunks=("letters", "replies"))
    got = _decide("q", rows, {"letters": 0.6, "replies": 0.1})
    assert got.whole_vault is False and got.stage == 1.0


def test_given_domains_are_kept_as_given():
    got = domain_set.decide("q", [], [], None, ALL, given=["garden"])
    assert (got.selected, got.set_rule, got.coverage) == (["garden"], "given", None)


def test_domain_aliases_come_from_the_domains_own_file_only(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    (tmp_path / "shop.yaml").write_text("domain_aliases: [the store]\n")
    assert plan_config.domain_aliases("shop") == ("the store",)
    assert plan_config.domain_aliases("shop.orders") == ()  # a parent's file names the parent
    with pytest.raises(plan_config.PlanConfigError):
        plan_config.parse_config({"domain_aliases": "the store"}, "x.yaml")


# ── the planner ───────────────────────────────────────────────────────────────────────────

def test_the_planner_searches_both_chunks_only_domains_in_one_command(world, monkeypatch):
    world["overview"][:] = _rows("letters", "replies", chunks=("letters", "replies"))
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"domain": _domain_answer({"letters": 0.9, "replies": 0.05, ALL: 0.05})}))
    result = _plan("what was agreed about the deadline?", domains=None)
    assert result["domains"]["selected"] == ["letters", "replies"]
    assert result["domains"]["set_rule"] == "chunks_only_all" and result["domains"]["coverage"] == 1.0
    cmds = [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert cmds and all("--domain letters,replies" in c for c in cmds)
    assert result["scope"]["domains"] == ["letters", "replies"]


def test_one_domain_with_mass_elsewhere_is_a_low_domain_stage(world, monkeypatch):
    world["overview"][:] = _rows("garden", "travel", "kitchen")
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"domain": _domain_answer({"garden": 0.6, "travel": 0.2, "kitchen": 0.2})}))
    result = _plan("How often did the team meet?", domains=None)
    assert result["domains"]["selected"] == ["garden"] and result["domains"]["coverage"] == pytest.approx(0.6)
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "domain"


def test_a_margin_set_covering_the_mass_keeps_the_domain_stage_high(world, monkeypatch):
    world["overview"][:] = _rows("garden", "travel", "kitchen")
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"domain": _domain_answer(
        {"garden": 0.5, "travel": 0.45, "kitchen": 0.0, ALL: 0.05})}))
    result = _plan("How often did the team meet?", domains=None)
    assert result["domains"]["selected"] == ["garden", "travel"] and result["domains"]["set_rule"] == "margin"
    assert result["domains"]["coverage"] == pytest.approx(0.95) and result["domains"]["whole_vault"] is False
    assert result["confidence"]["weakest"] != "domain"
    assert all("--domain garden,travel" in s["cmd"] for s in result["steps"] + result["suggested_steps"])


def test_a_view_that_does_not_cover_every_selected_domain_is_not_used(world, monkeypatch):
    world["overview"][:] = _rows("journal", "letters")
    _use(monkeypatch, FakeClassifier(override={"domain": _domain_answer({"journal": 0.5, "letters": 0.45, ALL: 0.05})}))
    result = _plan("How many visits did Ada make to Lakeport in 2025?", domains=None)
    assert result["domains"]["selected"] == ["journal", "letters"]
    assert result["route"]["id"] != "view"
    assert any("visits_by_year matched but does not cover" in w for w in result["warnings"])


def test_a_one_domain_command_renders_once_per_selected_domain(world, monkeypatch):
    world["overview"][:] = _rows("garden", "travel")
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={
        "domain": _domain_answer({"garden": 0.5, "travel": 0.45, ALL: 0.05}),
        "route": Answer("insights", {"insights": 0.95}, 0.95)}))
    result = _plan("what changed recently?", domains=None)
    assert result["route"]["id"] == "insights"
    cmds = [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert cmds[:2] == ["artmind query insights list --domain garden --compact",
                        "artmind query insights list --domain travel --compact"]
    assert cmds[2].startswith("artmind query survey --shape brief --domain garden,travel ")
    node = result["program"][0]["children"][0]  # the par's first child: the per-domain par
    assert node["kind"] == "par" and node["per_domain"] is True
    assert [c["run"] for c in node["children"]] == cmds[:2]


def test_a_one_domain_command_over_one_domain_stays_one_run(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": Answer("insights", {"insights": 0.95}, 0.95)}))
    result = _plan("what changed recently?")
    first = result["program"][0]["children"][0]
    assert first["kind"] == "run"
    assert first["run"] == "artmind query insights list --domain journal --compact"


def test_every_route_template_takes_several_domains():
    """`{domains}` goes to a repeatable, comma-split `--domain`; `{domain}` only to a command that
    takes one, rendered per domain."""
    from artmind.cli import cli
    from artmind.query_plan import routes as routes_mod

    context = {p: "x" for p in routes_mod.ALL_PLACEHOLDERS}
    context.update(domains="a.b,c", store_domains="a.b,c", domain="a.b", domain_list=("a.b", "c"))
    seen = 0
    for route in routes_mod.load_routes():
        templates = [n.run for c in route.programs for n, _ in planner._expand_commands(c.program, context, {})
                     if n.run] + list(route.steps_for(None))
        for template in templates:
            for ctx in planner._per_domain(template, context):
                argv = shlex.split(routes_mod.render(template, ctx))[1:]
                if "--domain" not in argv:
                    continue
                node, words = cli, list(argv)
                while hasattr(node, "commands") and words and words[0] in node.commands:
                    node = node.commands[words.pop(0)]
                option = next(p for p in node.params if "--domain" in getattr(p, "opts", ()))
                assert option.multiple, f"{template}: --domain is not repeatable"
                value = argv[argv.index("--domain") + 1]
                assert value == ("a.b,c" if "{domains}" in template or "{store_domains}" in template else ctx["domain"])
                seen += 1
    assert seen > 20


# ── replay grades sets ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("selected, expected, ok", [
    (["a", "b"], ["b", "a"], True),
    (["shop"], ["shop.orders", "shop.stock"], True),          # a parent covers its children
    (["shop", "travel"], ["shop.orders", "travel"], True),
    (["shop.orders"], ["shop.orders", "shop.stock"], False),  # part of the expected set
    (["a", "b"], ["a"], False),                               # a domain beside the expected ones
    (["a"], ["b"], False),
])
def test_replay_matches_domain_sets(selected, expected, ok):
    assert _domains_match(selected, expected) is ok
