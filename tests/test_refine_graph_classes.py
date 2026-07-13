"""Class-constrained merge tests (no Neo4j, no LLM)."""
from artmind.refine_graph import cluster_entities_by_class, cross_class_pairs


# ── cluster_entities_by_class ──────────────────────────────────────────────────

def test_cross_class_lookalikes_never_share_a_cluster():
    # 'Premium Account' (PRODUCT) vs 'Premium Account Fee — £0.00' (FEE) are
    # highly similar strings — the exact live case that produced a bad proposal.
    pairs = [
        ("Premium Account", "PRODUCT"),
        ("Premium Account Fee — £0.00", "FEE"),
        ("Premium Account Fee", "FEE"),
    ]
    clusters = cluster_entities_by_class(pairs, similarity_threshold=0.7)
    multi = [c for c in clusters if len(c) > 1]
    assert multi == [["Premium Account Fee", "Premium Account Fee — £0.00"]]


def test_same_class_lookalikes_still_cluster():
    pairs = [
        ("SmartSaver Account", "PRODUCT"),
        ("SmartSaver Plus Account", "PRODUCT"),
        ("Mortgage", "PRODUCT"),
    ]
    clusters = cluster_entities_by_class(pairs, similarity_threshold=0.7)
    multi = [c for c in clusters if len(c) > 1]
    assert multi == [["SmartSaver Account", "SmartSaver Plus Account"]]


def test_none_class_groups_together_and_dedupes():
    pairs = [("Alpha Thing", None), ("Alpha Things", None), ("Alpha Thing", None)]
    clusters = cluster_entities_by_class(pairs, similarity_threshold=0.7)
    assert clusters == [["Alpha Thing", "Alpha Things"]]


def test_same_name_in_two_classes_stays_separate():
    # one name string, two nodes of different classes — neither cluster should
    # bridge them into a proposal
    pairs = [("Standing Order", "TRANSACTION"), ("Standing Order Fee", "FEE")]
    clusters = cluster_entities_by_class(pairs, similarity_threshold=0.7)
    assert all(len(c) == 1 for c in clusters)


# ── cross_class_pairs (apply-time guard) ───────────────────────────────────────

def test_cross_class_pairs_flags_disjoint_classes():
    merges = {"Premium Account": "Premium Account Fee — £0.00"}
    cmap = {"Premium Account": {"PRODUCT"}, "Premium Account Fee — £0.00": {"FEE"}}
    assert cross_class_pairs(merges, cmap) == merges


def test_cross_class_pairs_allows_shared_class():
    merges = {"SmartSaver": "SmartSaver Account"}
    cmap = {"SmartSaver": {"PRODUCT"}, "SmartSaver Account": {"PRODUCT", "PRODUCT_FEATURE"}}
    assert cross_class_pairs(merges, cmap) == {}


def test_cross_class_pairs_tolerates_unknown_class():
    # missing entity_class must not block a merge — only a positive mismatch
    merges = {"A": "B", "C": "D"}
    cmap = {"A": set(), "B": {"FEE"}, "C": {"FEE"}}  # D absent entirely
    assert cross_class_pairs(merges, cmap) == {}
