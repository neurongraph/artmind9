"""B2 S3: scope by evidence in families of domains (spec 2026-11-07 §6). Invented domains
`X`, `X.a`, `X.b`, `Y`; no vault data."""

import pytest

from artmind.catalogue.model import DocRecord, DomainCatalogue, NameEntry
from artmind.query_plan import candidates, domain_set, planner
from artmind.query_plan.classify import Answer
from test_query_plan_planner import FakeClassifier, _plan, _use, world  # noqa: F401  (fixture)

ALL = planner.ALL_DOMAINS
DOMAINS = ["X.a", "X.b", "Y"]


def _rows(*names):
    return [{"domain": n, "document_count": 2, "documents": [f"{n}_doc.md"], "top_classes": ["THING"]} for n in names]


# ── domain_set: the rules ─────────────────────────────────────────────────────────────────

def _dset(selected, rule="single", decided=True):
    return domain_set.DomainSet(selected, "set", 0.9, 0.9, rule, decided=decided)


def _rescope(dset, route, probabilities=None, resolve=None, stores=None, domains=None):
    domains = domains or DOMAINS
    options = candidates.domain_options(_rows(*domains))
    return domain_set.rescope(dset, route, options=options, all_domains=domains, resolve=resolve, stores=stores,
                              probabilities=probabilities or {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})


def _entity(*subjects):
    return lambda kind, root: [list(s) for s in subjects] if kind == "entity" else []


def _document(*groups):
    return lambda kind, root: [list(groups)] if kind == "document" and groups else []


def test_family_root_and_families():
    assert domain_set.family_root("X.a.b") == "X" and domain_set.family_root("Y") == "Y"
    assert domain_set.families(DOMAINS) == {"X": ["X.a", "X.b"]}
    assert domain_set.families(["X", "X.a", "Y"]) == {"X": ["X", "X.a"]}  # a root that is a domain counts


def test_decide_marks_what_the_classifier_chose_as_decided():
    def decide(question, rows, probs, **kw):
        options = candidates.domain_options(rows)
        probs = {**{o.key: 0.0 for o in options}, ALL: 0.0, **probs}
        choice = max(probs, key=probs.get)
        return domain_set.decide(question, options, rows, Answer(choice, probs, probs[choice]), ALL, **kw)

    rows = _rows(*DOMAINS)
    assert decide("q", rows, {"X.a": 0.9, "Y": 0.1}).decided is True
    assert decide("q", rows, {"X.a": 0.5, "Y": 0.45}).decided is True  # margin
    assert decide("q", rows, {"X.a": 0.9}, given=["X.a"]).decided is False
    assert decide("what does X.a say?", rows, {"Y": 0.9}).decided is False  # named
    chunks = [{**r, "graph_extraction": False} for r in _rows("c1", "c2")]
    assert decide("q", chunks, {"c1": 0.9}).decided is False
    assert decide("q", rows, {ALL: 1.0}).decided is False
    assert decide("q", _rows("only"), {"only": 1.0}).decided is False  # one option


@pytest.mark.parametrize("route", ["search", "survey"])
def test_search_and_survey_lift_the_child_to_its_family(route):
    got = _rescope(_dset(["X.a"]), route)
    assert (got.selected, got.set_rule, got.family) == (["X"], "family", "X")
    assert got.coverage == pytest.approx(0.9)  # X covers X.a and X.b
    assert got.stage == pytest.approx(0.9)


def test_a_margin_set_lifts_only_the_family_member():
    got = _rescope(_dset(["X.a", "Y"], "margin"), "search", {"X.a": 0.5, "Y": 0.45, "X.b": 0.05})
    assert got.selected == ["X", "Y"] and got.set_rule == "family"


def test_a_domain_outside_any_family_is_left_alone():
    got = _rescope(_dset(["Y"]), "search")
    assert got.selected == ["Y"] and got.set_rule == "single"


@pytest.mark.parametrize("dset", [
    _dset(["X.a"], "given", decided=False), _dset(["X.a"], "named", decided=False),
    _dset(["X.a", "X.b", "Y"], "chunks_only_all", decided=False),
    _dset(["X.a", "X.b", "Y"], "all", decided=False), _dset(["X.a"], "single", decided=False),
])
def test_a_set_the_classifier_did_not_decide_is_never_rescoped(dset):
    assert _rescope(dset, "search") is dset


@pytest.mark.parametrize("route", ["existence", "enumeration", "aggregate", "conflicts", "insights", "filing",
                                   "inventory", "superlative"])
def test_other_routes_keep_b1s_set(route):
    got = _rescope(_dset(["X.a"]), route)
    assert got.selected == ["X.a"] and got.set_rule == "single"


@pytest.mark.parametrize("route", ["entity_profile", "entity_history", "hierarchy"])
def test_an_entity_selects_the_domain_it_lives_in(route):
    got = _rescope(_dset(["X.a"]), route, resolve=_entity([("Ann Lee", ("X.b",))]))
    assert (got.selected, got.set_rule) == (["X.b"], "resolved")
    assert got.resolution == {"entity": "Ann Lee", "domains": ["X.b"]} and got.tie_break is None
    assert got.coverage == pytest.approx(0.9)  # the family root's mass, not X.b's own 0.1


def test_twins_select_every_child_they_live_in():
    got = _rescope(_dset(["X.a"]), "entity_history", resolve=_entity([("Ann Lee", ("X.a", "X.b"))]))
    assert got.selected == ["X.a", "X.b"] and got.set_rule == "resolved"


def test_path_resolves_both_entities():
    got = _rescope(_dset(["X.a"]), "path", resolve=_entity([("Ann", ("X.a",))], [("Bo", ("X.b",))]))
    assert got.selected == ["X.a", "X.b"]
    assert got.resolution["entity"] == "Ann / Bo"


def test_the_child_with_the_highest_classifier_share_wins_a_tie():
    groups = [("Ann Lee", ("X.a",)), ("Ann Lee", ("X.b",))]
    got = _rescope(_dset(["X.a"]), "entity_profile", {"X.a": 0.3, "X.b": 0.6, "Y": 0.1}, resolve=_entity(groups))
    assert got.selected == ["X.b"] and got.tie_break == "classifier"


def test_equal_shares_pick_the_first_without_claiming_a_tie_break():
    groups = [("Ann Lee", ("X.a",)), ("Ann Lee", ("X.b",))]
    got = _rescope(_dset(["X.a"]), "entity_profile", {"X.a": 0.5, "X.b": 0.5}, resolve=_entity(groups))
    assert got.selected == ["X.a"] and got.tie_break is None


def test_the_same_name_in_two_children_is_twins_and_selects_both(family, monkeypatch):
    _classify(monkeypatch, "entity_profile", {"X.a": 0.2, "X.b": 0.7, "Y": 0.1})
    result = _plan("Who is Ann Lee?", domains=None)
    assert result["domains"]["selected"] == ["X.a", "X.b"] and result["domains"]["tie_break"] is None


def test_an_unresolved_entity_searches_the_family_root():
    got = _rescope(_dset(["X.a"]), "entity_profile", resolve=_entity())
    assert (got.selected, got.set_rule, got.resolution) == (["X"], "family", None)


@pytest.mark.parametrize("route", ["document", "time_history"])
def test_a_document_selects_the_domains_holding_it(route):
    got = _rescope(_dset(["X.a"]), route, resolve=_document(("notice.md", ("X.b",))))
    assert (got.selected, got.set_rule) == (["X.b"], "resolved")
    assert got.resolution == {"document": "notice.md", "domains": ["X.b"]}


def test_an_unmatched_document_searches_the_family_root():
    got = _rescope(_dset(["X.a"]), "document", resolve=_document())
    assert (got.selected, got.set_rule) == (["X"], "family")


def test_structured_selects_the_family_domains_holding_tables():
    got = _rescope(_dset(["X.a"]), "structured", stores=lambda root: ["X.b"])
    assert (got.selected, got.set_rule, got.store_domains) == (["X.b"], "stores", ("X.b",))


def test_structured_without_any_table_keeps_b1s_set():
    got = _rescope(_dset(["X.a"]), "structured", stores=lambda root: [])
    assert got.selected == ["X.a"] and got.set_rule == "single"


def test_the_family_root_that_holds_the_tables_is_the_store_domain():
    domains = ["X", "X.a", "X.b", "Y"]
    got = _rescope(_dset(["X.a"]), "structured", stores=lambda root: ["X"], domains=domains,
                   probabilities={"X": 0.0, "X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    assert got.selected == ["X"] and got.set_rule == "stores"


def test_guidance_adds_the_family_root_beside_the_store_domains():
    domains = ["X.a", "X.b", "Y"]
    got = _rescope(_dset(["X.a"]), "structured", stores=lambda root: ["X.b"], domains=domains)
    options = candidates.domain_options(_rows(*domains))
    guided = domain_set.with_guidance(got, options)
    assert guided.selected == ["X"] and guided.store_domains == ("X.b",)


def test_the_whole_vault_rule_is_kept_when_the_family_covers_every_option():
    domains = ["X.a", "X.b"]
    options = candidates.domain_options(_rows(*domains))
    got = domain_set.rescope(_dset(["X.a"]), "search", options=options, all_domains=domains,
                             probabilities={"X": 0.0, "X.a": 0.9, "X.b": 0.1})
    assert got.whole_vault is True and got.stage == pytest.approx(got.confidence)


# ── the planner, over a mocked family catalogue ───────────────────────────────────────────

def _cat(domain, names=(), docs=(), tables=0, aliases=()):
    entries = tuple(NameEntry(i, n, tuple(aliases), c, 5, domain) for i, n, c in names)
    records = tuple(DocRecord(f"{domain}:{d}", d, domain) for d in docs)
    return DomainCatalogue(domain, (1, 1, 0, ""), "2026-10-23T00:00:00+00:00", (("PERSON", 2, ""),), (),
                           tuple(docs), entries, False, records,
                           {"graph_extraction": True, "kind": "graph", "structured_tables": tables},
                           part_keys={"stores": "k", "names": "k", "documents": "k"})


@pytest.fixture
def family(world, monkeypatch):
    state = {"cats": {"X.a": _cat("X.a", [("n1", "Ann Lee", "PERSON")], ["alpha_notice.md"]),
                      "X.b": _cat("X.b", [("n2", "Ann Lee", "PERSON"), ("n3", "Bo Ray", "PERSON")],
                                  ["beta_notice.md"], tables=2),
                      "Y": _cat("Y", [("n4", "Cy Dunn", "PERSON")], ["gamma_notice.md"])}}
    world["overview"][:] = _rows(*DOMAINS)
    world["views"] = []
    monkeypatch.setattr(planner, "_get_catalogue", lambda cover: ({d: state["cats"][d] for d in cover if d in state["cats"]},
                                                                  {d: "live" for d in cover}))
    world.update(state)
    return world


def _classify(monkeypatch, route, shares):
    _use(monkeypatch, FakeClassifier(override={
        "domain": Answer(max(shares, key=shares.get), {**shares, ALL: 0.0}, max(shares.values())),
        "route": Answer(route, {route: 0.95}, 0.95)}))


def _cmds(result):
    return [s["cmd"] for s in result["steps"] + result["suggested_steps"]]


def test_a_text_route_searches_the_family_root(family, monkeypatch):
    _classify(monkeypatch, "search", {"X.a": 0.9, "X.b": 0.05, "Y": 0.05})
    result = _plan("what steps apply when a card is lost?", domains=None)
    domains = result["domains"]
    assert domains["selected"] == ["X"] and domains["set_rule"] == "family" and domains["family"] == "X"
    assert domains["classifier"] == {"selected": ["X.a"], "set_rule": "single", "coverage": 0.9}
    assert domains["resolution"] is None and domains["tie_break"] is None
    assert all("--domain X " in c for c in _cmds(result))
    assert result["confidence"]["score"] == 0.95  # the stage is X's coverage, 0.9 + 0.05, not X.a's 0.9


def test_an_entity_resolved_in_a_child_the_classifier_did_not_pick(family, monkeypatch):
    _classify(monkeypatch, "entity_profile", {"X.a": 0.1, "X.b": 0.1, "Y": 0.8})
    family["entities"]["Bo Ray"] = [{"_id": "n3", "name": "Bo Ray", "entity_class": "PERSON", "matched_by": ["exact"],
                                     "domain": "X.b"}]
    _classify(monkeypatch, "entity_profile", {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    result = _plan("Who is Bo Ray?", domains=None)
    domains = result["domains"]
    assert domains["selected"] == ["X.b"] and domains["set_rule"] == "resolved"
    assert domains["classifier"]["selected"] == ["X.a"]
    assert domains["resolution"] == {"entity": "Bo Ray", "domains": ["X.b"]}
    assert domains["coverage"] == pytest.approx(0.9)  # the family root's coverage, whatever X.a's mass
    assert any("--domain X.b" in c for c in _cmds(result))


def test_a_name_in_two_children_goes_to_the_one_the_classifier_prefers(family, monkeypatch):
    family["cats"]["X.a"] = _cat("X.a", [("n1", "Ann Lee", "PERSON")], ["alpha_notice.md"], aliases=("Ann",))
    family["cats"]["X.b"] = _cat("X.b", [("n2", "Ann Ray", "PERSON")], ["beta_notice.md"], aliases=("Ann",))
    _classify(monkeypatch, "entity_profile", {"X.a": 0.4, "X.b": 0.5, "Y": 0.1})  # a margin set: both children
    result = _plan("Who is Ann?", domains=None)
    assert result["domains"]["selected"] == ["X.b"]
    assert result["domains"]["tie_break"] == "classifier"
    assert result["domains"]["set_rule"] == "resolved"


def test_the_same_name_in_two_children_is_twins_and_selects_both(family, monkeypatch):
    _classify(monkeypatch, "entity_profile", {"X.a": 0.2, "X.b": 0.7, "Y": 0.1})
    result = _plan("Who is Ann Lee?", domains=None)
    assert result["domains"]["selected"] == ["X.a", "X.b"] and result["domains"]["tie_break"] is None


def test_an_unresolved_entity_searches_the_family(family, monkeypatch):
    _classify(monkeypatch, "entity_profile", {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    result = _plan("Who is Zed Quill?", domains=None)
    assert result["domains"]["selected"] == ["X"] and result["domains"]["set_rule"] == "family"
    assert result["domains"]["resolution"] is None


def test_a_document_is_scoped_to_the_domain_holding_it(family, monkeypatch):
    _classify(monkeypatch, "document", {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    result = _plan("summarise beta_notice.md", domains=None)
    assert result["domains"]["selected"] == ["X.b"] and result["domains"]["set_rule"] == "resolved"
    assert result["domains"]["resolution"] == {"document": "beta_notice.md", "domains": ["X.b"]}


def test_structured_renders_store_domains_into_sql_and_domains_into_text(family, monkeypatch):
    monkeypatch.setattr(candidates, "store_facts", lambda domains, question, classes: {
        "kind": "records_plus_guidance", "tables": ["t"], "bridge_classes": [], "matched_values": []})
    _classify(monkeypatch, "structured", {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    result = _plan("How many accounts are overdue per region?", domains=None)
    domains = result["domains"]
    assert domains["set_rule"] == "stores" and domains["family"] == "X"
    cmds = _cmds(result)
    assert any(c.startswith("artmind db bridge --domain X.b ") for c in cmds)
    assert any(c.startswith("artmind query text2sql --domain X.b ") for c in cmds)
    assert any(c.startswith("artmind query vector-text") and "--domain X " in c for c in cmds)


def test_structured_with_plain_analytical_kind_uses_the_table_domain_only(family, monkeypatch):
    monkeypatch.setattr(candidates, "store_facts", lambda domains, question, classes: {
        "kind": "analytical", "tables": ["t"], "bridge_classes": [], "matched_values": []})
    _classify(monkeypatch, "structured", {"X.a": 0.8, "X.b": 0.1, "Y": 0.1})
    result = _plan("How many accounts are overdue per region?", domains=None)
    assert result["domains"]["selected"] == ["X.b"]
    assert all("--domain X.b " in c for c in _cmds(result))


def test_given_domains_are_not_rescoped(family, monkeypatch):
    _classify(monkeypatch, "search", {"X.a": 0.9, "X.b": 0.05, "Y": 0.05})
    result = _plan("what steps apply when a card is lost?", domains=("X.a",))
    assert result["domains"]["selected"] == ["X.a"] and result["domains"]["set_rule"] == "given"
    assert result["domains"]["family"] is None


def test_a_view_matching_b1s_set_keeps_it(family, monkeypatch):
    from test_query_plan_planner import VIEW, LoadedView, Path, parse_view

    spec = parse_view({**VIEW, "domains": ["X.a"]}, VIEW["name"])
    family["views"] = [LoadedView(spec, "", Path("."))]
    _use(monkeypatch, FakeClassifier(override={
        "domain": Answer("X.a", {"X.a": 0.9, "X.b": 0.05, "Y": 0.05, ALL: 0.0}, 0.9),
        "route": Answer("search", {"search": 0.95}, 0.95),
        "view": Answer(VIEW["name"], {VIEW["name"]: 0.95}, 0.95)}))
    result = _plan("How many visits did Ada make to Lakeport in 2025?", domains=None)
    assert result["domains"]["selected"] == ["X.a"] and result["domains"]["set_rule"] == "single"
