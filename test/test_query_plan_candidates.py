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


def _hit(entity_id, name, legs, cls="PERSON"):
    return {"entity": {"_id": entity_id, "name": name, "entity_class": cls, "_domain": "journal"},
            "matched_by": legs, "score": 1.0}


def test_resolution_keeps_only_name_evidence(monkeypatch):
    def fake_names(domains, reference, top_k):
        assert (domains, reference, top_k) == (["journal"], "Ada", candidates.RESOLVE_TOP_K)
        return {"rows": [
            _hit("p1", "Ada", ["exact", "fulltext"]),
            _hit("p2", "Ada Lovelace", ["fulltext"]),
            _hit("p3", "Adam Bede", ["fulltext"]),
            _hit("p4", "Ada", ["vector"]),
        ]}

    def no_resolve(*args, **kwargs):
        raise AssertionError("resolve_mention must not call the embedding-backed entity_resolve")

    monkeypatch.setattr(vector_query, "entity_name_candidates", fake_names)
    monkeypatch.setattr(vector_query, "entity_resolve", no_resolve)
    mention = candidates.Mention("Ada", 0, 3, "capitalised")
    found = candidates.resolve_mention(mention, ["journal"], None)
    assert [c["_id"] for c in found] == ["p1", "p2"]
    assert found[0]["matched_by"] == ["exact", "fulltext"]


def test_fulltext_needs_every_token_of_one_side_in_the_other(monkeypatch):
    rows = [
        _hit("a", "Lakeport Harbour Office", ["fulltext"]),   # mention tokens all in the name
        _hit("b", "Office", ["fulltext"]),                    # name tokens all in the mention
        _hit("c", "Port Office", ["fulltext"]),               # neither
        _hit("d", "Harbour Office Lakeport", ["fulltext"]),   # order does not matter
        _hit("e", "lakeport office", ["exact"]),              # exact is always kept
    ]
    monkeypatch.setattr(vector_query, "entity_name_candidates", lambda d, r, k: {"rows": rows})
    mention = candidates.Mention("Lakeport  Office", 0, 16, "capitalised")
    found = candidates.resolve_mention(mention, ["journal"], None)
    assert [c["_id"] for c in found] == ["a", "b", "d", "e"]


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


# --- mentions ---------------------------------------------------------------


def test_contractions_with_i_are_first_person():
    for apostrophe in ("'", "\u2019"):
        question = f"Where I{apostrophe}m going and what I{apostrophe}ve seen near Lakeport"
        assert _texts(question) == [("I", "first_person"), ("Lakeport", "capitalised")]
    mention = candidates.find_mentions("Why I'd go there")[0]
    assert (mention.text, mention.end - mention.start) == ("I", 1)


def test_a_possessive_ends_a_run():
    assert _texts("Ada's Lakeport trip") == [("Ada", "capitalised"), ("Lakeport", "capitalised")]
    assert _texts("Ada\u2019s notes") == [("Ada", "capitalised")]
    mention = candidates.find_mentions("Port Ellen's harbour")[0]
    assert mention.text == "Port Ellen"


def test_trailing_punctuation_is_stripped_from_tokens():
    question = "Did Ada- or Bo& visit Lakeport'?"
    assert _texts(question) == [("Ada", "capitalised"), ("Bo", "capitalised"), ("Lakeport", "capitalised")]
    ada = candidates.find_mentions(question)[0]
    assert question[ada.start:ada.end] == "Ada"


def test_an_all_upper_case_question_skips_capitalisation():
    assert _texts("HOW MANY VISITS DID ADA MAKE TO LAKEPORT?") == []
    assert _texts('HOW MANY VISITS DID I MAKE TO "LAKEPORT"?') == [("I", "first_person"), ("LAKEPORT", "quoted")]


def test_weekdays_are_not_names():
    assert _texts("What happened on Monday near Lakeport?") == [("Lakeport", "capitalised")]


def test_a_run_continues_across_lowercase_joiners_before_a_capital():
    assert _texts("Please visit Bank of England today") == [("Bank of England", "capitalised")]
    assert _texts("Any letters from Ludwig van Beethoven") == [("Ludwig van Beethoven", "capitalised")]
    assert _texts("Ada and Bo met") == [("Ada", "capitalised"), ("Bo", "capitalised")]
    assert _texts("Ada of the north") == [("Ada", "capitalised")]


def test_imperative_verbs_at_a_sentence_start_are_not_names():
    for verb in ("Compare", "Calculate", "Identify", "Name", "Count", "Check", "Look", "Get", "Make",
                 "Remind", "Summarise", "Summarize"):
        assert _texts(f"{verb} Ada and Bo") == [("Ada", "capitalised"), ("Bo", "capitalised")], verb


def test_sentences_also_start_after_a_newline_or_colon():
    assert _texts("Ada left.\nCompare Lakeport") == [("Ada", "capitalised"), ("Lakeport", "capitalised")]
    assert _texts("Task:\nIdentify Bo") == [("Task", "capitalised"), ("Bo", "capitalised")]
    assert _texts("Question: Show Bo") == [("Question", "capitalised"), ("Bo", "capitalised")]
    # a newline does not glue two capitalised lines into one run
    assert _texts("Ada\nLakeport") == [("Ada", "capitalised"), ("Lakeport", "capitalised")]


def test_honorifics_join_the_following_name():
    assert _texts("Did Dr. Smith call?") == [("Dr. Smith", "capitalised")]
    assert _texts("Did Mrs Jones call?") == [("Mrs Jones", "capitalised")]
    assert _texts("ask prof. Hale") == [("prof. Hale", "capitalised")]
    assert _texts("Does Ms. Ng know Mr. Quill") == [("Ms. Ng", "capitalised"), ("Mr. Quill", "capitalised")]


def test_me_after_a_giving_verb_is_not_first_person():
    for lead in ("tell", "show", "give", "list", "send", "remind", "let", "help"):
        assert _texts(f"Please {lead} me about Lakeport") == [("Lakeport", "capitalised")], lead
    assert _texts("Did Bo visit me in Lakeport?") == [("Bo", "capitalised"), ("me", "first_person"),
                                                      ("Lakeport", "capitalised")]


def test_planner_contract_examples_hold():
    assert _texts("How many visits did Ada make to Lakeport in 2025?") == [
        ("Ada", "capitalised"), ("Lakeport", "capitalised")]
    assert _texts("How many visits did I make to Lakeport in 2025?") == [
        ("I", "first_person"), ("Lakeport", "capitalised")]
    assert _texts("Something about the weather?") == [("Something", "capitalised")]
    assert _texts("How often did the team meet?") == []


def test_too_many_mentions_keep_the_most_specific_by_priority():
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India"]
    question = 'I met "Quoted Thing" with Port Ellen and ' + " ".join(f"{n} saw x" for n in names)
    found = candidates.find_mentions(question)
    assert len(found) == candidates.MAX_MENTIONS
    texts = [(m.text, m.via) for m in found]
    assert ("Quoted Thing", "quoted") in texts and ("Port Ellen", "capitalised") in texts
    assert [m.start for m in found] == sorted(m.start for m in found)
    # quoted and multi-token names outrank single names, which outrank first person
    assert ("I", "first_person") not in texts
    assert ("Foxtrot", "capitalised") in texts and ("Golf", "capitalised") not in texts


def test_priority_drops_first_person_before_capitalised_singles():
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    question = "I met " + " ".join(f"{n} saw x" for n in names)
    texts = [(m.text, m.via) for m in candidates.find_mentions(question)]
    assert len(texts) == 8 and ("I", "first_person") not in texts


# --- keywords ---------------------------------------------------------------


def test_keywords_keep_alphanumerics_and_drop_dates_and_filler():
    question = "How much did we get in q4 2025 and in March on v2 of the budget on Friday?"
    assert candidates.keywords(question, []) == ["q4", "v2", "budget"]


def test_keywords_tokenise_apostrophes_and_hyphens():
    question = "list the well-known company's cross-team rules"
    assert candidates.keywords(question, []) == ["well-known", "company", "cross-team", "rules"]


# --- documents --------------------------------------------------------------


def test_a_bare_short_stem_is_not_a_document_but_the_spelled_suffix_is():
    names = ["notes.md", "summary.md"]
    assert candidates.match_document("Any notes on the summary?", names) is None
    assert candidates.match_document("Open notes.md please", names) == "notes.md"


def test_long_multi_token_or_marked_stems_are_accepted_without_suffix():
    assert candidates.match_document("what is in quarterly handbook", ["quarterly_handbook.md"]) == "quarterly_handbook.md"
    assert candidates.match_document("what about the 2025 plan", ["plan2025.md"]) is None  # different token
    assert candidates.match_document("see report2025 now", ["report2025.md"]) == "report2025.md"  # has a digit
    assert candidates.match_document("see handbooks overview", ["handbooks.md"]) == "handbooks.md"  # >= 8 chars


def test_document_ties_are_broken_by_sorted_name():
    names = ["b_report.md", "a_report.md"]
    assert candidates.match_document("the a report or b report?", names) == "a_report.md"
    assert candidates.match_document("the a report or b report?", list(reversed(names))) == "a_report.md"


def test_document_matching_keeps_unicode_letters():
    assert candidates.match_document("Was steht in caf\u00e9 \u00fcbersicht heute?", ["caf\u00e9_\u00fcbersicht.md"]) \
        == "caf\u00e9_\u00fcbersicht.md"


def test_document_names_use_a_label_expression(monkeypatch):
    sent = {}

    def fake_run(cypher, params):
        sent["cypher"], sent["params"] = cypher, params
        return [{"name": "b.md"}, {"name": "a.md"}, {"name": "a.md"}, {"name": None}]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    assert candidates.document_names(["journal"]) == ["a.md", "b.md"]
    assert "MATCH (d:Document|DocumentHistory)" in sent["cypher"]
    assert sent["params"] == {"domains": ["journal"]}


# --- store facts ------------------------------------------------------------


def _surface(monkeypatch):
    from artmind.structured import registry

    monkeypatch.setattr(registry, "routing_surface", lambda domains: [
        {"table": "accounts", "entity_classes": [{"entity_class": "CUSTOMER"}]},
    ])


def test_store_fact_words_match_on_word_boundaries(monkeypatch):
    _surface(monkeypatch)
    # "per " inside "super idea", "rule" inside "ruler": substrings are not words
    assert candidates.store_facts(["bank"], "a super idea", set())["kind"] == "graph"
    assert candidates.store_facts(["bank"], "check the ruler", {"CUSTOMER"})["kind"] == "graph"
    assert candidates.store_facts(["bank"], "a perfect staff", {"CUSTOMER"})["kind"] == "graph"
    assert candidates.store_facts(["bank"], "visits per month", set())["kind"] == "analytical"
    assert candidates.store_facts(["bank"], "the total", set())["kind"] == "analytical"
    assert candidates.store_facts(["bank"], "what policies apply", {"CUSTOMER"})["kind"] == "records_plus_guidance"
    assert candidates.store_facts(["bank"], "which procedures apply", {"CUSTOMER"})["kind"] == "records_plus_guidance"
    assert candidates.store_facts(["bank"], "the proceduralist", {"CUSTOMER"})["kind"] == "graph"


def test_store_facts_log_the_swallowed_registry_error(monkeypatch):
    from loguru import logger
    from artmind.structured import registry

    def boom(domains):
        raise RuntimeError("no registry here")

    monkeypatch.setattr(registry, "routing_surface", boom)
    messages = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    try:
        assert candidates.store_facts(["bank"], "average", set()) is None
    finally:
        logger.remove(sink)
    assert any("no registry here" in m for m in messages)


# --- domain options ---------------------------------------------------------


def test_nested_parents_with_the_same_descendants_keep_the_deepest():
    rows = [
        {"domain": "org.team.alpha", "document_count": 1, "documents": ["a.md"], "top_classes": ["X"]},
        {"domain": "org.team.beta", "document_count": 1, "documents": ["b.md"], "top_classes": ["Y"]},
    ]
    assert [(o.key, o.is_parent) for o in candidates.domain_options(rows)] == [
        ("org.team", True), ("org.team.alpha", False), ("org.team.beta", False),
    ]
    rows.append({"domain": "org.other", "document_count": 1, "documents": ["c.md"], "top_classes": ["Z"]})
    keys = [o.key for o in candidates.domain_options(rows)]
    assert "org" in keys and "org.team" in keys  # now their descendant sets differ


def test_a_parent_that_is_itself_a_domain_is_kept():
    rows = [
        {"domain": "org", "document_count": 1, "documents": ["root.md"], "top_classes": ["R"]},
        {"domain": "org.team.alpha", "document_count": 1, "documents": ["a.md"], "top_classes": []},
        {"domain": "org.team.beta", "document_count": 1, "documents": ["b.md"], "top_classes": []},
    ]
    keys = [o.key for o in candidates.domain_options(rows)]
    assert "org" in keys and "org.team" in keys


def test_parent_examples_interleave_across_children():
    rows = [
        {"domain": "bank.a", "document_count": 5, "documents": [f"a{i}.md" for i in range(6)],
         "top_classes": [f"A{i}" for i in range(6)]},
        {"domain": "bank.b", "document_count": 5, "documents": ["b0.md", "b1.md"], "top_classes": ["B0", "B1"]},
    ]
    parent = candidates.domain_options(rows)[0]
    assert parent.key == "bank"
    assert parent.documents[:4] == ("a0.md", "b0.md", "a1.md", "b1.md")
    assert parent.top_classes[:4] == ("A0", "B0", "A1", "B1")
    assert len(parent.documents) == 8 and len(parent.top_classes) == 8


# ── gazetteer and catalogue-aware options (spec §11) ────────────────────────

def _entry(i, name, obs=3, aliases=()):
    from artmind.query_plan.catalogue import NameEntry

    return NameEntry(i, name, tuple(aliases), "THING", obs)


def _gaz(question, entries):
    index = candidates.name_index(entries)
    return [(m.text, m.via, [e._id for e in m.entries])
            for m in candidates.gazetteer_mentions(question, candidates.find_mentions(question), index)]


def test_gazetteer_longest_match_wins():
    entries = [_entry("a", "harbour"), _entry("b", "harbour cafe"), _entry("c", "cafe")]
    assert _gaz("we went to the harbour cafe again", entries) == [("harbour cafe", "catalogue", ["b"])]


def test_gazetteer_matches_aliases_and_orders_entries_by_observations():
    entries = [_entry("a", "Greenhouse", 2), _entry("b", "Glasshouse", 9, aliases=["greenhouse"])]
    assert _gaz("time in the greenhouse", entries) == [("greenhouse", "catalogue", ["b", "a"])]


def test_gazetteer_skips_spans_overlapping_an_existing_mention():
    assert _gaz("Did Ada Lovelace visit", [_entry("a", "ada lovelace"), _entry("b", "lovelace")]) == []


def test_gazetteer_single_token_rules():
    entries = [_entry("a", "gym", 9), _entry("b", "dinner", 1), _entry("c", "what", 9), _entry("d", "march", 9),
               _entry("e", "tennis", 2), _entry("f", "today", 9)]
    assert _gaz("what gym dinner march tennis today", entries) == [("tennis", "catalogue", ["e"])]
    # a multi-word name needs no observation minimum and no length floor per token
    assert _gaz("saw the old gym", [_entry("g", "old gym", 1)]) == [("old gym", "catalogue", ["g"])]


def test_gazetteer_needs_adjacent_tokens():
    assert _gaz("old, gym", [_entry("g", "old gym", 1)]) == []


def test_gazetteer_respects_the_mention_cap():
    entries = [_entry(str(i), f"place{i}x") for i in range(12)]
    question = " ".join(f"place{i}x" for i in range(12))
    assert len(_gaz(question, entries)) == candidates.MAX_MENTIONS


def test_option_text_carries_catalogue_classes_and_relations():
    from artmind.query_plan.catalogue import DomainCatalogue

    classes = tuple((f"C{i:02d}", 100 - i, "") for i in range(20))
    relations = tuple((f"R{i}", 50 - i) for i in range(8))
    cat = DomainCatalogue("journal", (), "t", classes, relations, (), (), False)
    rows = [{"domain": "journal", "document_count": 3, "documents": ["a.md"], "top_classes": ["OLD"]}]
    text = candidates.domain_options(rows, {"journal": cat})[0].text()
    assert "classes " + ", ".join(f"C{i:02d}" for i in range(15)) in text
    assert "C15" not in text and "OLD" not in text
    assert text.endswith("relations R0, R1, R2, R3, R4")
    plain = candidates.domain_options(rows)[0].text()
    assert "classes OLD" in plain and "relations" not in plain


def test_a_broader_catalogue_is_not_used_for_an_option():
    from artmind.query_plan.catalogue import DomainCatalogue

    cat = DomainCatalogue("fam", (), "t", (("C", 1, ""),), (("R", 1),), (), (), False)
    rows = [{"domain": "fam.a", "document_count": 1, "top_classes": ["OLD"]}]
    assert "classes OLD" in candidates.domain_options(rows, {"fam": cat})[0].text()
