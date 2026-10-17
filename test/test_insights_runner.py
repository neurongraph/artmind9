import pytest

import paths
from _insights_helpers import FakeProjector
from artmind.insights import runner, store
from artmind.insights.model import InsightsError

SCHEMA = {"entity_types": {"PERSON": {"kind": "recurrent", "type_examples": ["self", "friend"]}}}


def _tables():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    rows = [{"_id": f"a{i}", "name": f"A{i}", "cls": "PERSON", "type": "friend", "days": da, "docs": 20} for i in range(1, 5)]
    rows += [{"_id": f"b{i}", "name": f"B{i}", "cls": "PERSON", "type": "friend", "days": db, "docs": 20} for i in range(1, 5)]
    rows.append({"_id": "x", "name": "X", "cls": "PERSON", "type": "friend",
                 "days": da[:3] + db[:3] + da[10:13] + db[10:13], "docs": 12})
    rows.append({"_id": "me", "name": "Me", "cls": "PERSON", "type": "self", "days": da + db, "docs": 40})
    days = sorted(set(da + db))
    docs = [{"id": f"d{i}", "valid_from": d, "time_source": "document_label"} for i, d in enumerate(days)]
    return {"entity_evidence": rows, "documents": docs, "property_stats": [], "relation_triples": [], "open_conflicts": []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", tmp_path / "cfg")
    monkeypatch.setattr(runner, "load_schema", lambda domain: SCHEMA)
    monkeypatch.setattr(runner, "_hashes", lambda domain: {"schema": "s", "same_as": "a", "config": "c"})
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "demo.yaml").write_text("min_period_docs: 5\n")
    return tmp_path


def refresh(**kw):
    return runner.refresh("demo", projector=FakeProjector(_tables()), now=kw.pop("now", "2026-10-17T09:00:00Z"), **kw)


def test_first_refresh_writes_a_snapshot(env):
    result = refresh()
    assert result["status"] == "written"
    assert result["anchors"] == [{"_id": "me", "name": "Me", "rule": "type=self"}]
    run = store.load_latest("demo")
    assert run.as_of == "2024-06-10"
    assert {f.diff for f in run.findings} == {"new"}
    assert {p.detector for p in run.plan if p.status == "ran"} >= {"communities", "brokers", "kind_mismatch"}
    assert "brokers:PERSON:x" in {f.key for f in run.findings}
    assert all(f.subjects == [] or "me" not in {s["_id"] for s in f.subjects} for f in run.findings)
    assert run.circles and run.circles[0]["name_source"] == "template"


def test_unchanged_graph_is_up_to_date_and_force_rewrites(env):
    refresh()
    assert refresh(now="2026-10-17T10:00:00Z")["status"] == "up_to_date"
    forced = refresh(now="2026-10-17T10:00:00Z", force=True)
    assert forced["status"] == "written"
    assert forced["diff"]["new"] == 0 and forced["diff"]["gone"] == 0
    assert len(store.list_runs("demo")) == 2


def test_only_runs_selected_detectors_and_carries_the_rest(env):
    refresh()
    before = {f.key for f in store.load_latest("demo").findings}
    result = refresh(now="2026-10-17T11:00:00Z", only=["kind_mismatch"])
    run = store.load_latest("demo")
    assert {f.key for f in run.findings if f.diff != "gone"} == before
    assert {p.detector for p in run.plan} == {"kind_mismatch"}
    assert result["status"] == "written"


def test_unknown_detector_and_empty_graph(env):
    with pytest.raises(InsightsError, match="unknown detector"):
        refresh(only=["nope"])
    with pytest.raises(InsightsError, match="no entities"):
        runner.refresh("demo", projector=FakeProjector({}), now="2026-10-17T09:00:00Z")


def test_same_rows_give_identical_findings(env):
    refresh(force=True)
    first = [{**f.to_dict(), "diff": None} for f in store.load_latest("demo").findings]
    first_profile = store.load_latest("demo").profile
    refresh(force=True)
    second = [{**f.to_dict(), "diff": None} for f in store.load_latest("demo").findings]
    assert first == second  # only the diff state (new -> same) may differ
    assert store.load_latest("demo").profile == first_profile


def test_retention_follows_config(env):
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\nkeep_runs: 1\n")
    refresh(force=True)
    result = refresh(now="2026-10-17T12:00:00Z", force=True)
    assert result["pruned"] == ["2026-10-17T09-00-00Z.json"]


def _refresh_tables(tables, **kw):
    return runner.refresh("demo", projector=FakeProjector(tables), now=kw.pop("now", "2026-10-17T09:00:00Z"), **kw)


def test_only_brokers_keeps_circles_and_brokers_findings(env):
    refresh()
    before = store.load_latest("demo")
    assert before.circles and any(f.detector == "brokers" for f in before.findings)
    refresh(now="2026-10-17T11:00:00Z", only=["brokers"])
    run = store.load_latest("demo")
    assert run.circles == before.circles
    brokers = [f for f in run.findings if f.detector == "brokers"]
    assert brokers and {f.diff for f in brokers} == {"same"}
    assert {f.key for f in run.findings if f.detector == "communities"} == \
        {f.key for f in before.findings if f.detector == "communities"}
    assert {f.diff for f in run.findings if f.detector == "communities"} == {"same"}


def test_fingerprint_sees_changed_evidence_days(env):
    refresh()
    tables = _tables()
    row = next(r for r in tables["entity_evidence"] if r["_id"] == "a1")
    row["days"] = row["days"][1:]  # same entity count and latest date, one day fewer
    assert _refresh_tables(tables, now="2026-10-17T10:00:00Z")["status"] == "written"


def test_fingerprint_sees_a_moved_day_with_equal_counts(env):
    refresh()
    tables = _tables()
    row = next(r for r in tables["entity_evidence"] if r["_id"] == "a1")
    row["days"] = ["2023-02-15"] + row["days"][1:]  # one day moved: same count, same latest date
    assert _refresh_tables(tables, now="2026-10-17T10:00:00Z")["status"] == "written"


def test_partial_run_never_short_circuits_a_full_one(env):
    refresh()
    tables = _tables()
    tables["entity_evidence"][0]["days"] = tables["entity_evidence"][0]["days"][:-1]
    assert _refresh_tables(tables, now="2026-10-17T10:00:00Z", only=["dormancy"])["status"] == "written"
    assert _refresh_tables(tables, now="2026-10-17T11:00:00Z")["status"] == "written"


def test_corrupt_latest_is_ignored_with_force(env):
    refresh()
    (store.domain_dir("demo") / store.LATEST).write_text("{not json")
    with pytest.raises(InsightsError):
        refresh(now="2026-10-17T10:00:00Z")
    assert refresh(now="2026-10-17T10:00:00Z", force=True)["status"] == "written"


def test_yaml_error_in_schema_becomes_insights_error(env, monkeypatch):
    import yaml

    def boom(domain):
        raise yaml.YAMLError("bad schema")

    monkeypatch.setattr(runner, "load_schema", boom)
    with pytest.raises(InsightsError, match="bad schema"):
        refresh()


def test_skipped_detector_keeps_previous_findings(env):
    refresh()
    run = store.load_latest("demo")
    seeded = [f for f in run.findings if f.detector == "dormancy"]
    if not seeded:
        from dataclasses import replace
        proto = run.findings[0]
        run.findings.append(replace(proto, key="dormancy:PERSON:a1", detector="dormancy", diff="new"))
        store.write_run(run)
    tables = _tables()
    for d in tables["documents"]:
        d["time_source"] = "default_ingestion"
    _refresh_tables(tables, now="2026-10-17T10:00:00Z", force=True)
    out = store.load_latest("demo")
    dorm = [f for f in out.findings if f.detector == "dormancy"]
    assert dorm and {f.diff for f in dorm} == {"same"}
    assert any(p.detector == "dormancy" and p.status == "skipped" for p in out.plan)


def test_disabled_detector_drops_its_findings(env):
    refresh()
    run = store.load_latest("demo")
    from dataclasses import replace
    run.findings.append(replace(run.findings[0], key="dormancy:PERSON:a1", detector="dormancy", diff="new"))
    store.write_run(run)
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\ndetectors:\n  dormancy: {enabled: false}\n")
    refresh(now="2026-10-17T10:00:00Z", force=True)
    dorm = [f for f in store.load_latest("demo").findings if f.detector == "dormancy"]
    assert {f.diff for f in dorm} == {"gone"}


def test_context_extras_are_loaded_and_findings_shared(env, monkeypatch):
    seen = {}
    real = runner.run_detectors

    def spy(ctx, selected):
        out = real(ctx, selected)
        seen["ctx"] = ctx
        return out
    monkeypatch.setattr(runner, "run_detectors", spy)
    tables = _tables()
    tables["anchor_relations"] = [{"rel": "WORKS_AT", "_id": "a1", "name": "A1", "cls": "PERSON", "type": None,
                                   "days": ["2023-01-01", "bad"]}]
    _refresh_tables(tables, force=True)
    ctx = seen["ctx"]
    assert ctx.anchor_relations == [{"rel": "WORKS_AT", "_id": "a1", "name": "A1", "cls": "PERSON", "type": None,
                                     "days": ("2023-01-01",)}]
    assert {f.detector for f in ctx.memo["findings"]} >= {"communities", "brokers"}


def test_dependencies_for_phase_two_detectors():
    from artmind.insights.runner import DEPENDS, DETECTORS_VERSION
    assert DEPENDS["split_signal"] == {"same_as_candidates", "salience_trend", "outcome_lift"}
    assert DEPENDS["transitions"] == {"communities"}
    assert DETECTORS_VERSION == 3


def test_entity_props_loaded_for_configured_blocks(env):
    from artmind.insights.config import parse_config
    from artmind.insights.profile import build_profile
    from artmind.insights.runner import load_entity_props
    tables = _tables()
    tables["property_stats"] = [
        {"cls": "PERSON", "property": "relationship_to_diarist", "filled": 3, "distinct_values": 2, "sample": []},
        {"cls": "PERSON", "property": "mood", "filled": 3, "distinct_values": 2, "sample": []},
    ]
    tables["entity_property"] = [{"_id": "a1", "value": "friend"}]
    proj = FakeProjector(tables)
    cfg = parse_config({"outcome": {"class": "PERSON", "property": "mood", "positive": ["up"], "negative": ["down"]}}, "demo")
    profile, _ = build_profile(proj, SCHEMA, cfg)
    props = load_entity_props(proj, cfg, profile)
    assert set(props) == {("PERSON", "mood"), ("PERSON", "relationship_to_diarist")}
    assert ("entity_property", {"cls": "PERSON", "prop": "mood"}) in proj.calls
