"""Faceted `topic build` (spec 2026-11-10 §6–§7): shape rule, per-cell gather, map per
(document, subject) through the cache, reduce per facet, page shape, plan written beside."""
import pytest
import yaml

import _topic_world as w
from artmind.topics import build, mapcache, plan_store, prompts, shape, store


@pytest.fixture
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))
    monkeypatch.setenv("ARTMIND_TOPIC_MAP_CONCURRENCY", "1")
    return tmp_path


def _build(tmp_path, llms=None, **kw):
    session, factory = w.session_factory(kw.pop("versions", None))
    llms = llms or w.LLMs()
    out = build.build_topic("Garden work", ["j"], facets=["tomato planting", "compost heap"], out=tmp_path / "_Topics",
                            model="m", cards=w.CARDS, session_factory=factory, search=w.search,
                            map_llm=llms.map, reduce_llm=llms.reduce, now="2026-11-10T00:00:00Z",
                            generations={"j": 7}, **kw)
    return out, llms, session


def test_shape_rule_reads_orient_facts():
    long_dated = {"documents": 100, "dated_from_content": 90, "months": 40}
    assert shape.resolve_by("auto", long_dated) == "year"
    assert shape.resolve_by("auto", {**long_dated, "dated_from_content": 50}) == "none"
    assert shape.resolve_by("auto", {"documents": 60, "dated_from_content": 60, "months": 12}) == "month"
    assert shape.resolve_by("auto", {"documents": 58, "dated_from_content": 57, "months": 5.7}) == "none"
    assert shape.resolve_by("month", {}) == "month"
    f = shape.facts(["bank"], [{"domain": "bank", "unit": {"documents": 58},
                                "time": {"dated": {"from_content": 57}, "span": ["2026-01-15", "2026-07-07"]}},
                               {"domain": "bank.a"}, {"domain": "bank.b"}, {"domain": "bank.a.x"}])
    assert f["documents"] == 58 and f["children"] == 2 and f["months"] == pytest.approx(5.7, abs=0.1)
    counted = [[{"domain": "bank.a.x"}], [{"domain": "bank.b"}]]
    assert shape.split_from_counts("auto", ["bank"], counted) == "domain"
    assert shape.split_from_counts("auto", ["bank"], [[{"domain": "bank.a"}]]) == "none"


def test_dry_run_reports_the_grid_and_calls_and_writes_nothing(world):
    out, llms, _ = _build(world, dry_run=True)
    assert llms.maps == [] and llms.reduces == []
    assert not (world / "_Topics").exists() and not (world / "plans").exists()
    assert out["grid"]["slices"] == {"by": "year", "split": None}
    assert [(c["label"], c["matching"], c["take"]) for c in out["grid"]["cells"]] == [
        ("subject 1 / 2023", 2, 2), ("subject 1 / 2024", 0, 0), ("subject 2 / 2023", 1, 1), ("subject 2 / 2024", 1, 1)]
    # D2 is found by both subjects: one map call per (document, subject)
    assert out["documents"] == 3 and out["calls"] == {"map": 4, "map_cached": 0, "reduce": 3}
    assert out["effect"]["llm"]["calls"] == 7 and out["effect"]["llm"]["est_cost_usd"] > 0


def test_build_writes_a_faceted_page_and_its_plan(world):
    out, llms, _ = _build(world)
    assert len(llms.maps) == 4 and len(llms.reduces) == 3  # two sections + the overview
    assert "Section subject: tomato planting" in llms.reduces[0] and "Section subject: compost heap" in llms.reduces[1]
    assert "tomato planting (part of: Garden work)" in llms.maps[0]
    page = store.read_page(world / "_Topics", "garden-work")
    headings = [line for line in page.body.splitlines() if line.startswith("## ")]
    assert headings == ["## Overview", "## tomato planting", "## compost heap", "## Timeline", "## Where sources differ"]
    assert "- 2023-03-01: Planted tomato seedlings in the raised bed." in page.body
    assert page.meta["built"]["generations"] == {"j": 7} and page.meta["subjects"] == ["tomato planting", "compost heap"]
    loaded = plan_store.load_plan("garden-work")
    assert loaded.spec.by == "year" and loaded.spec.split == "none" and loaded.spec.version == 1
    built = loaded.spec.built
    assert built["at"] == "2026-11-10T00:00:00Z" and built["documents"] == ["D1", "D2", "D3"]
    assert {"subject": 1, "period": "2023", "domain": None, "matching": 2, "taken": 2} in built["grid"]
    assert out["plan_path"] == str(world / "plans" / "garden-work") and out["version"] == 1


def test_a_rebuild_maps_only_what_the_cache_lacks_and_keeps_the_version(world, monkeypatch):
    _build(world)
    out, llms, _ = _build(world)  # nothing changed: only the overview is reduced again
    assert out["calls"] == {"map": 0, "map_cached": 4, "reduce": 1, "made": 1} and llms.maps == []
    monkeypatch.setitem(w.DOCS, "D3", ("2024-05-02", "j", "New compost bin built from pallets and wire."))
    out, llms, _ = _build(world, versions={"D3": "v-new"})  # D3 changed since the first build
    assert out["calls"]["map"] == 1 and out["calls"]["map_cached"] == 3 and len(llms.maps) == 1
    assert "New compost bin" in llms.maps[0]
    # D3 is only under subject 2: its section and the overview are reduced, subject 1's is reused
    assert out["calls"]["reduce"] == 2 and len(llms.reduces) == 2
    assert "Section subject: compost heap" in llms.reduces[0]
    assert plan_store.load_plan("garden-work").spec.version == 1  # same definition
    out, llms, _ = _build(world, full=True)
    assert len(llms.maps) == 4


def test_a_changed_definition_bumps_the_version_and_an_edited_skill_md_is_kept(world):
    _build(world)
    folder = world / "plans" / "garden-work"
    data = yaml.safe_load((folder / "plan.yaml").read_text())
    data["version"] = 2
    (folder / "plan.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    (folder / "SKILL.md").write_text((folder / "SKILL.md").read_text() + "\nOperator note.\n")
    _build(world, per_cell=1)
    loaded = plan_store.load_plan("garden-work")
    assert loaded.spec.version == 3 and loaded.spec.per_cell == 1 and "Operator note." in loaded.skill_md


def test_the_map_cache_key_is_sha_subject_prompt_and_model():
    k = mapcache.key("sha1", "tomato  planting", prompts.MAP_PROMPT_VERSION, "m")
    assert k == mapcache.key("sha1", "tomato planting", prompts.MAP_PROMPT_VERSION, "m")
    assert k != mapcache.key("sha1", "tomato planting", prompts.MAP_PROMPT_VERSION, "other")
    assert k != mapcache.key("sha2", "tomato planting", prompts.MAP_PROMPT_VERSION, "m")
    assert mapcache.key(None, "x", 1, "m") is None


def test_cache_lives_under_logs(world):
    _build(world)
    files = sorted(p.name for p in (world / "logs" / "topic-map-cache").glob("*.json"))
    assert len(files) == 6 and sum(f.startswith("r") for f in files) == 2  # 4 maps + 2 section reduces


def test_nothing_matching_writes_nothing(world, monkeypatch):
    monkeypatch.setattr(w, "MATCHES", {})
    out, llms, _ = _build(world)
    assert out["error"].startswith("no document matched") and llms.maps == []
    assert any("matches no document" in x for x in out["warnings"])
    assert not (world / "plans").exists()


def test_a_long_section_is_reduced_per_period_then_merged(world, monkeypatch):
    monkeypatch.setattr(prompts, "REDUCE_INPUT_CHARS", 10)
    _, llms, _ = _build(world)
    merges = [r for r in llms.reduces if "Merge these partial section bodies" in r]
    assert len(merges) == 2


def test_map_concurrency_env_is_checked(monkeypatch):
    monkeypatch.setenv("ARTMIND_TOPIC_MAP_CONCURRENCY", "0")
    with pytest.raises(ValueError, match="ARTMIND_TOPIC_MAP_CONCURRENCY"):
        build.map_concurrency()
    monkeypatch.delenv("ARTMIND_TOPIC_MAP_CONCURRENCY")
    assert build.map_concurrency() == 8


def test_overview_answer_is_split_into_its_two_parts():
    assert prompts.split_overview("## Overview\n\nA [#x].\n\n## Where sources differ\n\nB.") == ("A [#x].", "B.")
    assert prompts.split_overview("Just text.") == ("Just text.", "No disagreements found.")


def test_map_prompt_lists_passages_with_ids_and_parse_filters_ids():
    from artmind.topics.gather import Material, Source

    m = Material("D1", "One", "one.md", "j", "S1", [Source("c1_p01", "c1", "Notice is 30 days.")])
    p = prompts.map_prompt("notice", None, m)
    assert "[c1_p01] Notice is 30 days." in p and "S1" in p
    parsed = prompts.parse_map({"relevant": True, "notes": [{"claim": "x", "passages": ["c1_p01", "no"]}]}, {"c1_p01"})
    assert parsed == {"relevant": True, "notes": [{"claim": "x", "passages": ["c1_p01"]}]}


def test_a_domain_split_nests_child_domains_under_one_section_per_subject(world, monkeypatch):
    docs = {"D1": ("2023-03-01", "j.a", "Planted tomato seedlings."), "D2": ("2023-07-12", "j.b", "Tomato harvest."),
            "D3": ("2024-05-02", "j.b", "Compost bin built.")}
    monkeypatch.setattr(w, "DOCS", docs)
    out, _, _ = _build(world, by="none", split="domain")
    assert out["grid"]["slices"] == {"by": "none", "split": "domain"}
    body = store.read_page(world / "_Topics", "garden-work").body
    headings = [line for line in body.splitlines() if line.startswith("#")]
    assert headings[:4] == ["## Overview", "## tomato planting", "### j.a", "### j.b"]
    assert "## Timeline" not in body
