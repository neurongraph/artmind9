"""Peel 1: one card per domain plus the corpus block (spec 2026-11-09 §2, §3)."""
from artmind.catalogue.model import DocRecord, DomainCatalogue
from artmind.orient import card


def _cat(domain="bank.policy", **kw):
    base = dict(
        domain=domain, fingerprint=(8, 1907, 2, 1, ""), built_at="2026-11-09T00:00:00+00:00",
        classes=(("POLICY_PROVISION", 811, ""), ("CONTROL_MEASURE", 384, ""), ("RISK", 252, ""),
                 ("POLICY", 126, ""), ("ROLE", 104, ""), ("SAR", 8, "")),
        doc_records=(DocRecord("d1", "policy_a_v2.md", domain, "2026-06-01", True),
                     DocRecord("d2", "policy_b.md", domain, "2026-01-01", False)),
        stores={"graph_extraction": True, "kind": "graph", "structured_tables": 5},
        time={"documents": 2, "with_valid_from": 2, "share": 1.0, "earliest": "2026-01-01", "latest": "2026-06-01",
              "from_content": 2, "content_earliest": "2026-01-01", "content_latest": "2026-06-01"},
        versions=({"name": "policy_a.md", "status": "retired", "valid_from": "2026-01-01",
                   "successor": "policy_a_v2.md"},
                  {"name": "policy_a_v2.md", "status": "current", "valid_from": "2026-06-01", "successor": None,
                   "replaces": 1}),
        assets=({"kind": "topic", "id": "t1", "detail": {}}, {"kind": "topic", "id": "t2", "detail": {}}),
        part_keys={p: [8] for p in ("classes", "documents", "stores", "time", "versions", "assets")},
    )
    base.update(kw)
    return DomainCatalogue(**base)


FACTS = {"purpose": "Formal policy documents.", "time_rule": {"valid_from": "document field 'Effective Date'"},
         "filename_pattern": None, "classes": {}}


def test_card_fields_from_catalogue_schema_and_narrator():
    c = card.build_card(_cat(), FACTS, narrator=None, family=("bank", 1))
    assert c["domain"] == "bank.policy" and c["family"] == "bank" and c["siblings"] == 1
    assert c["purpose"] == "Formal policy documents." and c["kind"] == "graph"
    assert c["stores"] == ["graph", "structured"]
    assert c["unit"] == {"documents": 2, "pattern": None, "example": "policy_a_v2.md"}
    assert c["voice"] is None
    assert c["time"] == {"rule": {"valid_from": "document field 'Effective Date'"},
                         "dated": {"from_content": 2, "defaulted_to_ingestion": 0},
                         "span": ["2026-01-01", "2026-06-01"],
                         "versions": {"with_successor": 1, "retired": 1}}
    assert c["vocabulary"] == ["POLICY_PROVISION", "CONTROL_MEASURE", "RISK", "POLICY", "ROLE"]
    assert c["entities"] == 1907
    assert c["assets"] == {"views": 0, "topic_pages": 2, "insights": None, "summaries": "1 of 2"}
    assert "coverage" not in c  # health not cached


def test_chunks_only_card_and_narrator_voice():
    cat = _cat(stores={"graph_extraction": False, "kind": "chunks", "structured_tables": 0}, classes=())
    c = card.build_card(cat, FACTS, narrator={"entity_class": "PERSON", "name": "Ada", "aliases": []},
                        family=(None, 0))
    assert c["kind"] == "chunks-only" and c["stores"] == ["chunks"]
    assert c["voice"] == {"narrator": "Ada", "entity_class": "PERSON", "first_person": True}
    assert c["family"] is None and c["vocabulary"] == [] and "rolls_up" not in c


def test_a_family_root_says_it_rolls_up():
    assert card.build_card(_cat("bank"), FACTS, None, (None, 0), children=8)["rolls_up"] == 8


def test_coverage_only_positive_values_from_cached_health():
    health = {"behind_vault": {"count": 3}, "summaries_missing": {"count": 0}, "conflicts_open": {"count": 9}}
    cat = _cat(health=health, part_keys={**_cat().part_keys, "health": ["ttl"]})
    assert card.build_card(cat, FACTS, None, ("bank", 1))["coverage"] == {"behind_vault": 3}


def test_unread_parts_are_absent_and_reported():
    cat = _cat(part_keys={"classes": [8], "stores": [8]})  # documents, time, versions, assets not loaded
    warnings: list = []
    c = card.build_card(cat, None, None, ("bank", 1), warnings=warnings)
    assert "time" not in c and "unit" not in c and "assets" not in c and "purpose" not in c
    assert any("bank.policy: time not read" in w for w in warnings)
    assert any("bank.policy: schema not found" in w for w in warnings)


def test_family_tree_and_corpus_block():
    tree = card.family_tree(["bank", "bank.cases", "bank.policy", "journal"])
    assert tree == {"bank": ["bank.cases", "bank.policy"], "journal": []}
    assert card.family_of("bank.policy", tree) == ("bank", 1)
    assert card.family_of("bank", tree) == (None, 0)
    cards = [card.build_card(_cat(), FACTS, None, ("bank", 1)),
             card.build_card(_cat("bank.cases"), FACTS, None, ("bank", 1))]
    corpus = card.corpus_block(cards, tree, generation=16, built_at="2026-11-09T00:00:00+00:00")
    # no `bank` card here, so both children count; with a `bank` card only it would (it rolls them up)
    assert corpus == {"domains": 2, "families": {"bank": ["bank.cases", "bank.policy"], "journal": []},
                      "documents": 4, "entities": 3814, "span": ["2026-01-01", "2026-06-01"],
                      "generation": 16, "built_at": "2026-11-09T00:00:00+00:00"}
