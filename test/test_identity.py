from artmind import identity


def row(i, name, cls="Person"):
    return {"entity": {"_id": i, "name": name, "entity_class": cls}, "score": 1, "matched_by": ["exact"]}


def test_normalise():
    assert identity.normalise("  Dr.  O'Brien-Smith ") == "dr o brien smith"
    assert identity.normalise(None) == ""


def test_first_person():
    assert identity.first_person("Me") == "whole"
    assert identity.first_person("myself!") == "whole"
    assert identity.first_person("my wife") == "leading"
    assert identity.first_person("Mike") == ""
    assert identity.first_person("the wife of me") == ""
    assert identity.first_person("") == ""


def test_twin_groups_same_class_and_name():
    groups = identity.twin_groups([row("a", "Acme Corp."), row("b", "acme corp", ), row("c", "Acme Corp", "Org"),
                                   row("d", "Other")])
    assert groups == [{"name": "Acme Corp.", "entity_class": "Person", "ids": ["a", "b"]}]


def test_twin_groups_same_id_not_twin():
    assert identity.twin_groups([row("a", "X"), row("a", "X")]) == []


def test_twin_warning_text():
    g = {"name": "Acme", "entity_class": "Org", "ids": ["a", "b"]}
    assert identity.twin_warning(g) == ("2 entities share the name 'Acme' (Org): "
                                        "treat them as one unless the text distinguishes them")


def test_narrator_for_agreement(monkeypatch):
    n = {"entity_class": "Person", "name": "Ann", "aliases": []}
    monkeypatch.setattr(identity.domain_config, "narrator", lambda d: n if d != "x" else None)
    assert identity.narrator_for(["a", "b"])[0] == n
    assert identity.narrator_for(["a", "x"])[0] is None
    assert identity.narrator_for([])[0] is None


def test_narrator_for_disagreement(monkeypatch):
    monkeypatch.setattr(identity.domain_config, "narrator",
                        lambda d: {"entity_class": "Person", "name": d, "aliases": []})
    assert identity.narrator_for(["a", "b"])[0] is None


def test_narrator_for_error(monkeypatch):
    def boom(d):
        raise identity.domain_config.DomainConfigError("bad")
    monkeypatch.setattr(identity.domain_config, "narrator", boom)
    n, w = identity.narrator_for(["a"])
    assert n is None and "bad" in w[0]


def arow(i, name, aliases, cls="Person"):
    return {"entity": {"_id": i, "name": name, "entity_class": cls, "aliases": aliases}}


def test_twin_groups_name_vs_alias():
    groups = identity.twin_groups([row("a", "Satish"), arow("b", "Satish Pillai", ["Satish"])])
    assert [g["ids"] for g in groups] == [["a", "b"]]
    assert identity.twin_warning(groups[0]) == (
        '"Satish" and "Satish Pillai" (Person) share the name "Satish": '
        "treat them as one unless the text distinguishes them")


def test_twin_groups_alias_across_classes_is_not_a_twin():
    assert identity.twin_groups([row("a", "Satish", "Org"), arow("b", "Satish Pillai", ["Satish"])]) == []
