"""Deterministic candidates for `query plan` (spec 2026-10-22 §3.1, §4.2 entities)."""

from artmind import graph_query, vector_query
from artmind.query_plan import candidates
from artmind.query_plan.config import Narrator


def _texts(question):
    return [(m.text, m.via) for m in candidates.find_mentions(question)]


def test_mentions_are_names_quotes_and_first_person():
    assert _texts("How many visits did I make to Port Ellen in May 2025?") == [
        ("I", "first_person"), ("Port Ellen", "capitalised"),
    ]
    assert _texts('Did Ada read "The Long Harbour" again?') == [
        ("Ada", "capitalised"), ("The Long Harbour", "quoted"),
    ]
    assert _texts("Bo's notes mention Lakeport.") == [("Bo", "capitalised"), ("Lakeport", "capitalised")]


def test_keywords_skip_mentions_and_stopwords():
    question = "recorded deaths in my family since Ada left"
    assert candidates.keywords(question, candidates.find_mentions(question)) == [
        "recorded", "deaths", "family", "left",  # "since" is a question word
    ]


def test_domain_options_add_parents_with_two_children():
    rows = [
        {"domain": "bank.policy", "document_count": 3, "documents": ["policy_alpha.md"], "top_classes": ["POLICY"]},
        {"domain": "bank.cases", "document_count": 2, "documents": ["case_one.md"], "top_classes": ["CASE"]},
        {"domain": "journal", "document_count": 9, "documents": ["2025-05-01.md"], "top_classes": ["PERSON"]},
    ]
    options = candidates.domain_options(rows)
    assert [(o.key, o.is_parent) for o in options] == [
        ("bank", True), ("bank.cases", False), ("bank.policy", False), ("journal", False),
    ]
    assert options[0].document_count == 5
    assert "All bank.* domains" in options[0].text()


def test_resolution_keeps_only_name_evidence(monkeypatch):
    def fake_resolve(domains, reference, top_k):
        assert (domains, reference, top_k) == (["journal"], "Ada", candidates.RESOLVE_TOP_K)
        return {"rows": [
            {"entity": {"_id": "p1", "name": "Ada", "entity_class": "PERSON", "_domain": "journal"},
             "matched_by": ["exact", "vector"]},
            {"entity": {"_id": "p2", "name": "Adam", "entity_class": "PERSON"}, "matched_by": ["vector"]},
        ]}

    monkeypatch.setattr(vector_query, "entity_resolve", fake_resolve)
    mention = candidates.Mention("Ada", 0, 3, "capitalised")
    found = candidates.resolve_mention(mention, ["journal"], None)
    assert [c["_id"] for c in found] == ["p1"]
    assert found[0]["matched_by"] == ["exact", "vector"]


def test_first_person_resolves_to_the_narrator(monkeypatch):
    sent = {}

    def fake_run(cypher, params):
        sent["cypher"], sent["params"] = cypher, params
        return [{"entity": {"_id": "n1", "name": "Diarist", "entity_class": "PERSON"}}]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    mention = candidates.Mention("I", 0, 1, "first_person")
    found = candidates.resolve_mention(mention, ["journal"], Narrator("PERSON", "Diarist"))
    assert found[0]["_id"] == "n1" and found[0]["matched_by"] == ["narrator"]
    assert sent["params"]["cls"] == "PERSON" and sent["params"]["name"] == "Diarist"
    assert candidates.resolve_mention(mention, ["journal"], None) == []


def test_twins_group_by_class_and_casefolded_name():
    refs = [{"_id": "a", "name": "Lakeport", "entity_class": "LOCATION"},
            {"_id": "b", "name": "lakeport", "entity_class": "LOCATION"},
            {"_id": "c", "name": "Lakeport", "entity_class": "ORGANIZATION"}]
    assert [[r["_id"] for r in g] for g in candidates.twin_groups(refs)] == [["a", "b"], ["c"]]


def test_a_document_named_in_the_question_is_found():
    names = ["policy_alpha.md", "policy_alpha_v2.md", "notes.md"]
    assert candidates.match_document("What did policy alpha v2 say in January?", names) == "policy_alpha_v2.md"
    assert candidates.match_document("Summarise policy_alpha.md.", names) == "policy_alpha.md"
    assert candidates.match_document("What did I do?", names) is None


def test_store_facts_pick_a_kind(monkeypatch):
    from artmind.structured import registry

    monkeypatch.setattr(registry, "routing_surface", lambda domains: [
        {"table": "accounts", "entity_classes": [{"entity_class": "CUSTOMER"}]},
    ])
    hybrid = candidates.store_facts(["bank"], "What is the balance of 3 accounts?", {"CUSTOMER"})
    assert hybrid["kind"] == "hybrid" and hybrid["tables"] == ["accounts"]
    assert candidates.store_facts(["bank"], "average balance", set())["kind"] == "analytical"
    monkeypatch.setattr(registry, "routing_surface", lambda domains: [])
    assert candidates.store_facts(["bank"], "anything", set()) is None
