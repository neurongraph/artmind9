"""Budgets and reported cuts (spec 2026-11-09 §5.1): definitions are cut last."""
from artmind.orient import budget


def size(x):
    return budget.size(x)


def _block(n_classes=30, n_rel=40, n_docs=30, defn="d" * 300):
    return {
        "domain": "big", "card": {"domain": "big"},
        "classes": [{"class": f"C{i}", "count": 100 - i, "kind": "k", "definition": defn,
                     "types": {"top": [[f"t{j}", 9] for j in range(8)], "distinct": 8}, "dated": 0.0}
                    for i in range(n_classes)],
        "relations": [{"from": "A", "rel": f"R{i}", "to": "B", "count": 50 - i} for i in range(n_rel)],
        "views": [{"name": "v", "summary": "s", "params": {}, "examples": ["e1", "e2"]}],
        "topics": [f"topic {i}" for i in range(20)],
        "documents": [{"name": f"d{i}.md", "status": "retired", "valid_from": None, "successor": None}
                      for i in range(n_docs)],
    }


def test_a_small_block_is_untouched():
    b = _block(n_classes=2, n_rel=2, n_docs=1, defn="short")
    out, cuts = budget.fit_domains([b])
    assert out == [b] and cuts == []


def test_definitions_survive_cheaper_cuts():
    out, cuts = budget.fit_domains([_block()], domain_budget=16000)
    assert size(out[0]) <= 16000
    parts = [c["part"] for c in cuts]
    assert "definitions" not in parts and parts[0] == "relations"
    assert parts == sorted(parts, key=budget.CUT_ORDER.index)
    assert all(c["definition"] for c in out[0]["classes"])
    rel = next(c for c in cuts if c["part"] == "relations")
    assert (rel["shown"], rel["total"]) == (budget.RELATIONS_KEEP, 40)
    assert rel["rest"] == "artmind query graph metadata --domain big"


def test_definitions_go_last_smallest_classes_first():
    out, cuts = budget.fit_domains([_block()], domain_budget=10000)
    assert size(out[0]) <= 10000
    assert cuts[-1]["part"] == "definitions" and cuts[-1]["shown"] < cuts[-1]["total"]
    assert out[0]["classes"][0]["definition"] is not None  # the largest class keeps its definition
    assert out[0]["classes"][-1]["definition"] is None


def test_call_budget_cuts_the_largest_block_first():
    a, b = _block(), _block(n_classes=3, n_rel=3, n_docs=1)
    b["domain"] = "small"
    out, cuts = budget.fit_domains([a, b], domain_budget=50_000, call_budget=9000)
    assert size(out) <= 9000
    assert {c["domain"] for c in cuts} == {"big"}
    assert len({(c["domain"], c["part"]) for c in cuts}) == len(cuts)  # one record per part


def test_corpus_collapses_largest_family_first():
    cards = [{"domain": f"bank.c{i}", "family": "bank", "purpose": "p" * 400, "entities": 10,
              "unit": {"documents": 2}, "stores": ["graph"]} for i in range(30)]
    cards.append({"domain": "journal", "family": None, "purpose": "j", "entities": 5,
                  "unit": {"documents": 1}, "stores": ["graph"]})
    out, cuts = budget.fit_corpus(cards, corpus_budget=4000)
    assert size(out) <= 4000
    row = next(r for r in out if r.get("collapsed"))
    assert row["family"] == "bank" and len(row["domains"]) == 30 and row["documents"] == 60
    assert any(r.get("domain") == "journal" for r in out)
    assert cuts == [{"part": "cards", "domain": "bank", "shown": 0, "total": 30,
                     "rest": "artmind query orient --domain bank"}]
