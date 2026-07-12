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
        rp, "normalize_time", lambda domain, dry_run: calls.append(("time", dry_run)) or {"ok": 1}
    )
    monkeypatch.setattr(
        rp,
        "detect_supersession",
        lambda domain, dry_run: calls.append(("supersession", dry_run)) or {"ok": 1},
    )

    def fake_refine_graph(**kw):
        calls.append(("merge", kw.get("dry_run"), str(kw.get("from_file"))))
        if kw.get("output_file"):
            rp.Path(kw["output_file"]).write_text("{}")
        return {"proposed_merges": {"Alias A": "Canonical A"}, "stats": {}}

    monkeypatch.setattr(rp, "refine_graph", fake_refine_graph)

    def fake_detect_conflicts(**kw):
        calls.append(("conflicts", kw.get("dry_run"), str(kw.get("from_file"))))
        if kw.get("output_file"):
            rp.Path(kw["output_file"]).write_text("{}")
        return {"conflicts": [], "stats": {}}

    monkeypatch.setattr(rp, "detect_conflicts", fake_detect_conflicts)
    monkeypatch.setattr(
        rp,
        "consolidate_descriptions",
        lambda **kw: calls.append(("consolidate", kw.get("dry_run"), kw.get("limit")))
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
    assert ("time", False) in stubbed and ("supersession", False) in stubbed
    assert stubbed[2][1] is True and stubbed[3][1] is True and stubbed[4][1] is True
    # embed never runs in propose mode
    assert "embed" not in report["steps"]
    assert report["mode"] == "propose"
    assert "apply_with" in report
    assert report["steps"]["consolidate"]["candidates_total"] == 7


def test_propose_writes_report_file(stubbed):
    report = rp.run_pipeline("banking_products")
    data = json.loads(rp.Path(report["report_file"]).read_text())
    assert data["domain"] == "banking_products"
    assert data["steps"]["merge"]["proposals_file"].endswith("merges.json")


def test_one_shot_apply_runs_everything_live(stubbed):
    report = rp.run_pipeline("banking_products", apply=True)
    assert ("merge", False, "None") in stubbed
    assert ("conflicts", False, "None") in stubbed
    assert ("consolidate", False, None) in stubbed
    # embed sweep ran and nulled the merged canonical
    assert report["steps"]["embed"] == {"canonicals_nulled": 1, "entities_embedded": 7}


def test_apply_from_file_uses_vetted_proposals(stubbed, tmp_path):
    propose = rp.run_pipeline("banking_products")
    stubbed.clear()
    report = rp.run_pipeline("banking_products", from_file=propose["report_file"])
    merge_call = [c for c in stubbed if c[0] == "merge"][0]
    conflicts_call = [c for c in stubbed if c[0] == "conflicts"][0]
    assert merge_call[2].endswith("merges.json")
    assert conflicts_call[2].endswith("conflicts.json")
    assert report["mode"] == "apply"
    assert "embed" in report["steps"]


def test_apply_from_file_rejects_wrong_domain(stubbed, tmp_path):
    propose = rp.run_pipeline("banking_products")
    with pytest.raises(ValueError):
        rp.run_pipeline("banking_policy", from_file=propose["report_file"])


def test_steps_subset_skips_others(stubbed):
    report = rp.run_pipeline("banking_products", steps=["consolidate"])
    assert [c[0] for c in stubbed] == ["consolidate"]
    assert set(report["steps"].keys()) == {"consolidate"}


def test_propose_consolidation_is_sampled_apply_is_uncapped(stubbed):
    rp.run_pipeline("banking_products", sample_consolidations=2)
    assert ("consolidate", True, 2) in stubbed
    stubbed.clear()
    rp.run_pipeline("banking_products", apply=True)
    assert ("consolidate", False, None) in stubbed
