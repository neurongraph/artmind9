"""Refine pipeline unit tests (no Neo4j, no LLM — all steps monkeypatched)."""
import json

import pytest

import artmind.refine_pipeline as rp


# ── resolve_steps ──────────────────────────────────────────────────────────────

def test_resolve_steps_defaults_to_all_in_order():
    assert rp.resolve_steps(None) == list(rp.PIPELINE_STEPS)


def test_resolve_steps_enforces_canonical_order():
    # user asks in the wrong order; pipeline must still run time → merge → embed
    assert rp.resolve_steps(["embed", "merge", "time"]) == ["time", "merge", "embed"]


def test_resolve_steps_rejects_unknown():
    with pytest.raises(ValueError):
        rp.resolve_steps(["time", "wikify"])


# ── run_pipeline orchestration ─────────────────────────────────────────────────

@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """Stub every step function, recording call order and key kwargs."""
    calls = []

    monkeypatch.setattr(rp, "REFINE_DIR", tmp_path)
    monkeypatch.setattr(rp, "load_env", lambda: {})
    monkeypatch.setattr(rp, "resolve_llm_model", lambda env, override=None: "stub-model")
    monkeypatch.setattr(
        rp, "normalize_time", lambda domain, dry_run: calls.append(("time", domain, dry_run)) or {"ok": 1}
    )
    monkeypatch.setattr(
        rp,
        "detect_supersession",
        lambda domain, dry_run: calls.append(("supersession", domain, dry_run)) or {"ok": 1},
    )

    def fake_refine_graph(**kw):
        calls.append(("merge", kw.get("domain"), kw.get("dry_run"), str(kw.get("from_file"))))
        if kw.get("output_file"):
            rp.Path(kw["output_file"]).write_text("{}")
        return {"proposed_merges": {"Alias A": "Canonical A"}, "stats": {}}

    monkeypatch.setattr(rp, "refine_graph", fake_refine_graph)

    def fake_detect_conflicts(**kw):
        calls.append(("conflicts", tuple(kw.get("domains")), kw.get("dry_run"), str(kw.get("from_file"))))
        if kw.get("output_file"):
            rp.Path(kw["output_file"]).write_text("{}")
        return {"conflicts": [], "stats": {}}

    monkeypatch.setattr(rp, "detect_conflicts", fake_detect_conflicts)
    monkeypatch.setattr(
        rp,
        "consolidate_descriptions",
        lambda **kw: calls.append(("consolidate", kw.get("domain"), kw.get("dry_run"), kw.get("limit")))
        or {"counts": {"consolidate": 2, "skipped_over_limit": 5}, "rows": []},
    )
    monkeypatch.setattr(rp, "_null_embeddings_for_canonicals", lambda domain, names: len(names))
    monkeypatch.setattr(rp, "embed_entities_backfill", lambda domain: {"entities_embedded": 7})
    return calls


def test_propose_runs_deterministic_steps_live_and_llm_steps_dry(stubbed):
    report = rp.run_pipeline("banking_products")
    order = [c[0] for c in stubbed]
    assert order == ["time", "supersession", "merge", "conflicts", "consolidate"]
    # time/supersession real, merge/conflicts/consolidate dry
    assert ("time", "banking_products", False) in stubbed
    assert ("supersession", "banking_products", False) in stubbed
    assert stubbed[2][2] is True and stubbed[3][2] is True and stubbed[4][2] is True
    # embed never runs in propose mode; single domain → no cross pass
    pd = report["per_domain"]["banking_products"]
    assert "embed" not in pd
    assert "cross_domain_conflicts" not in report
    assert report["mode"] == "propose"
    assert "apply_with" in report
    assert pd["consolidate"]["candidates_total"] == 7


def test_propose_writes_report_file(stubbed):
    report = rp.run_pipeline("banking_products")
    data = json.loads(rp.Path(report["report_file"]).read_text())
    assert data["domains"] == ["banking_products"]
    assert data["per_domain"]["banking_products"]["merge"]["proposals_file"].endswith(
        "merges_banking_products.json"
    )


def test_one_shot_apply_runs_everything_live(stubbed):
    report = rp.run_pipeline("banking_products", apply=True)
    assert ("merge", "banking_products", False, "None") in stubbed
    assert ("conflicts", ("banking_products",), False, "None") in stubbed
    assert ("consolidate", "banking_products", False, None) in stubbed
    # embed sweep ran and nulled the merged canonical
    assert report["per_domain"]["banking_products"]["embed"] == {
        "canonicals_nulled": 1,
        "entities_embedded": 7,
    }


def test_apply_from_file_uses_vetted_proposals(stubbed):
    propose = rp.run_pipeline("banking_products")
    stubbed.clear()
    report = rp.run_pipeline("banking_products", from_file=propose["report_file"])
    merge_call = [c for c in stubbed if c[0] == "merge"][0]
    conflicts_call = [c for c in stubbed if c[0] == "conflicts"][0]
    assert merge_call[3].endswith("merges_banking_products.json")
    assert conflicts_call[3].endswith("conflicts_banking_products.json")
    assert report["mode"] == "apply"
    assert "embed" in report["per_domain"]["banking_products"]


def test_apply_from_file_rejects_wrong_domain_set(stubbed):
    propose = rp.run_pipeline("banking_products")
    with pytest.raises(ValueError):
        rp.run_pipeline("banking_policy", from_file=propose["report_file"])
    with pytest.raises(ValueError):
        rp.run_pipeline(["banking_products", "banking_policy"], from_file=propose["report_file"])


def test_steps_subset_skips_others(stubbed):
    report = rp.run_pipeline("banking_products", steps=["consolidate"])
    assert [c[0] for c in stubbed] == ["consolidate"]
    assert set(report["per_domain"]["banking_products"].keys()) == {"consolidate"}


def test_propose_consolidation_is_sampled_apply_is_uncapped(stubbed):
    rp.run_pipeline("banking_products", sample_consolidations=2)
    assert ("consolidate", "banking_products", True, 2) in stubbed
    stubbed.clear()
    rp.run_pipeline("banking_products", apply=True)
    assert ("consolidate", "banking_products", False, None) in stubbed


# ── multi-domain: cross-domain conflicts pass ──────────────────────────────────

def test_multi_domain_runs_cross_conflicts_after_all_merges(stubbed):
    report = rp.run_pipeline(["banking_policy", "banking_sop_guides"])
    order = [(c[0], c[1]) for c in stubbed]
    # every domain's merge happens before ANY conflicts pass
    last_merge = max(i for i, c in enumerate(stubbed) if c[0] == "merge")
    first_conflict = min(i for i, c in enumerate(stubbed) if c[0] == "conflicts")
    assert last_merge < first_conflict
    # intra passes per domain plus one cross pass over the full set
    conflict_domains = [c[1] for c in stubbed if c[0] == "conflicts"]
    assert ("banking_policy",) in conflict_domains
    assert ("banking_sop_guides",) in conflict_domains
    assert ("banking_policy", "banking_sop_guides") in conflict_domains
    assert report["cross_domain_conflicts"]["proposals_file"].endswith("conflicts_cross.json")
    # per-domain structure present for both
    assert set(report["per_domain"].keys()) == {"banking_policy", "banking_sop_guides"}


def test_multi_domain_apply_from_file_applies_cross_proposals(stubbed):
    propose = rp.run_pipeline(["banking_policy", "banking_sop_guides"])
    stubbed.clear()
    rp.run_pipeline(
        ["banking_policy", "banking_sop_guides"], from_file=propose["report_file"]
    )
    cross_calls = [
        c for c in stubbed if c[0] == "conflicts" and c[1] == ("banking_policy", "banking_sop_guides")
    ]
    assert len(cross_calls) == 1
    assert cross_calls[0][3].endswith("conflicts_cross.json")


def test_single_domain_has_no_cross_pass(stubbed):
    rp.run_pipeline("banking_products", apply=True)
    conflict_domains = [c[1] for c in stubbed if c[0] == "conflicts"]
    assert conflict_domains == [("banking_products",)]
