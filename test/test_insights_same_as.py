import pytest

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors.same_as import SAME_AS_CANDIDATES, candidate_pairs, load_alias_groups, name_match, norm
from artmind.insights.detectors.split import SPLIT_SIGNAL
from artmind.insights.model import Finding


def days(month, n, year=2023):
    return [f"{year}-{month:02d}-{d:02d}" for d in range(1, n + 1)]


def test_norm_and_name_rules():
    aliases = load_alias_groups()
    assert norm("  Sam  O'Neil ") == "sam o neil"
    assert name_match("dad", "daddy", aliases, 0.25)[0] == "kinship"
    assert name_match("ab", "asha bose", aliases, 0.25)[0] == "initials"
    assert name_match("sam", "sam rivera", aliases, 0.25)[0] == "token_subset"
    assert name_match("jonathan", "jonathon", aliases, 0.25)[0] == "edit"
    assert name_match("ben", "ken", aliases, 0.25) is None          # too short for edit distance
    assert name_match("sam rivera", "sam okoro", aliases, 0.25) is None


WORDS = iter(["Oak", "Elm", "Fir", "Yew", "Bay", "Ivy", "Rue", "Cob", "Hop", "Kit", "Lux", "Mug",
              "Nib", "Orb", "Pod", "Rib", "Tux", "Urn", "Vim", "Wax", "Zip", "Gum"])


def labelled_world():
    """Synthetic labelled pairs. Duplicates share a circle of neighbours and are never mentioned on the same day.
    Namesakes have disjoint circles, meet on two separate days, and disagree on type. Neighbour names are
    distinct 3-letter words, so they never name-match anything."""
    ents, positives, negatives = [], [], []

    def circle(month):
        return [ent(f"c{month}_{i}", "PERSON", days(month, 6), name=next(WORDS)) for i in range(3)]

    def same(a, b, name_a, name_b, month):
        ents.extend(circle(month))
        ents.append(ent(a, "PERSON", days(month, 6)[:3], name=name_a, type="parent"))
        ents.append(ent(b, "PERSON", days(month, 6)[3:], name=name_b, type="parent"))
        positives.append(tuple(sorted((a, b))))

    def namesakes(a, b, name_a, name_b, month):
        ents.extend(circle(month) + circle(month + 1))
        meet = days(12, 2)
        ents.append(ent(a, "PERSON", days(month, 4) + meet, name=name_a, type="friend"))
        ents.append(ent(b, "PERSON", days(month + 1, 4) + meet, name=name_b, type="colleague"))
        negatives.append(tuple(sorted((a, b))))

    same("dad", "daddy", "Dad", "Daddy", 1)
    same("ab", "asha", "AB", "Asha Bose", 3)
    same("jon1", "jon2", "Jonathan", "Jonathon", 5)
    namesakes("sam", "samo", "Sam", "Sam Okoro", 7)
    namesakes("lee", "leep", "Lee", "Lee Park", 9)
    return ents, positives, negatives


def test_labelled_fixture_separates_duplicates_from_namesakes():
    ents, positives, negatives = labelled_world()
    ctx = make_ctx(ents, docs_per_period={"2023": 100})
    found = SAME_AS_CANDIDATES.run(ctx, "PERSON")
    scored = {tuple(sorted((f.stats["canonical"]["_id"], f.stats["alias"]["_id"]))): f.score for f in found}
    for p in positives:
        assert scored.get(p, 0) >= 0.5, (p, scored.get(p))
    for n in negatives:
        assert scored.get(n, 0) < 0.5, (n, scored.get(n))
    assert {tuple(sorted((c["canonical"], c["alias"]))) for c in ctx.memo["same_as"]} == set(positives)
    f = next(f for f in found if f.stats["rule"] == "kinship")
    assert f.kind == "health" and f.action == "curate" and f.title.startswith("Possibly the same PERSON:")


def test_config_aliases_extend_the_package_list():
    ents = [ent("n1", "PERSON", days(1, 4), name="Nonno"), ent("g1", "PERSON", days(1, 8)[4:], name="Bapu")]
    plain = make_ctx(ents)
    assert not candidate_pairs(plain.entities, load_alias_groups(plain.config), 0.25)
    ctx = make_ctx(ents, config={"detectors": {"same_as_candidates": {"aliases": [["Nonno", "Bapu"]]}}})
    assert any(p[2] == "kinship" for p in candidate_pairs(ctx.entities, load_alias_groups(ctx.config), 0.25))


def test_split_signal_flags_opposite_trends():
    a = ent("t1", "LOCATION", days(1, 5), name="Tech Park One")
    b = ent("t2", "LOCATION", days(2, 5), name="TPO")
    ctx = make_ctx([a, b])
    ctx.memo["same_as"] = [{"class": "LOCATION", "canonical": "t1", "alias": "t2", "score": 0.7}]
    def trend(e, direction):
        return Finding(key=f"salience_trend:LOCATION:{e._id}", detector="salience_trend", kind="corpus", score=0.5,
                       title="x", subjects=[{"_id": e._id, "name": e.name, "class": "LOCATION"}],
                       stats={"direction": direction})
    ctx.memo["findings"] = [trend(a, "fading"), trend(b, "rising")]
    assert SPLIT_SIGNAL.plan(ctx) == ["all"]
    (f,) = SPLIT_SIGNAL.run(ctx, "all")
    assert f.key == "split_signal:LOCATION:t1:t2:salience_trend" and f.action == "curate"
    assert f.stats["directions"] == {"t1": "fading", "t2": "rising"}


def test_split_signal_runs_and_finds_nothing_without_candidates():
    ctx = make_ctx([ent("a", "PERSON", days(1, 4))])
    assert SPLIT_SIGNAL.plan(ctx) == ["all"] and SPLIT_SIGNAL.run(ctx, "all") == []
