"""`artmind query orient` (spec 2026-11-09 §2.2, §5)."""
import json

import pytest
from click.testing import CliRunner

from artmind import orient
from artmind.catalogue.model import DocRecord, DomainCatalogue
from artmind.cli import cli


def _cat(d, fp=(3, 2, 1, 0, ""), built_at="2026-11-09T00:00:00+00:00"):
    return DomainCatalogue(
        domain=d, fingerprint=fp, built_at=built_at,
        classes=(("EVENT", 2, ""),), shapes=(), types=(("EVENT", {"total": 2, "distinct": 1, "dated": 2,
                                                                   "top": [["visit", 2]]}),),
        doc_records=(DocRecord("x", f"{d}-1.md", d, "2026-01-01", True),), versions=(),
        stores={"graph_extraction": True, "kind": "graph", "structured_tables": 0},
        time={"documents": 1, "with_valid_from": 1, "share": 1.0, "earliest": "2026-01-01",
              "latest": "2026-01-01", "from_content": 1, "content_earliest": "2026-01-01",
              "content_latest": "2026-01-01"},
        assets=(),
        part_keys={p: [3] for p in ("classes", "shapes", "types", "documents", "versions", "stores", "time",
                                    "assets")})


@pytest.fixture
def wired(monkeypatch):
    calls = {}

    def get_detailed(domains, parts):
        calls["parts"] = tuple(parts)
        calls["domains"] = list(domains)
        fps = calls.get("fp", {})
        return {d: _cat(d, fps.get(d, (3, 2, 1, 0, "")), calls.get("built", {}).get(d, "2026-11-09T00:00:00+00:00"))
                for d in domains}, {d: {p: "memory" for p in parts} for d in domains}

    from artmind.catalogue import cache
    from artmind.kg_views import store

    monkeypatch.setattr(cache, "get_detailed", get_detailed)
    monkeypatch.setattr(orient, "_known_domains", lambda warnings=None: ["journal", "journal.work"])
    monkeypatch.setattr(orient.schema_facts, "read", lambda d: {"purpose": f"{d} notes", "time_rule": {},
                                                                "filename_pattern": None, "classes": {}})
    monkeypatch.setattr(orient.domain_config, "narrator", lambda d: None)
    monkeypatch.setattr(store, "list_views", lambda domains: ([], []))
    return calls


def _run(*args, code=0):
    r = CliRunner().invoke(cli, ["query", "orient", *args, "--compact"])
    assert r.exit_code == code, r.output
    return json.loads(r.stdout) if code == 0 else r


def test_peel_one_lists_every_domain_and_names_the_next_peel(wired):
    out = _run()
    assert list(out)[:3] == ["command", "warnings", "next"]
    assert out["command"] == "query orient" and out["peel"] == 1
    assert out["next"] == "artmind query orient --domain <domain> --compact"
    assert [c["domain"] for c in out["domains"]] == ["journal", "journal.work"]
    assert out["domains"][0]["rolls_up"] == 1
    assert out["corpus"]["families"] == {"journal": ["journal.work"]}
    assert out["corpus"]["documents"] == 1  # the root rolls its child up: counted once
    assert "health" in wired["parts"] and "names" not in wired["parts"]


def test_peel_two_expands_a_family_and_has_no_next(wired):
    out = _run("--domain", "journal")
    assert out["peel"] == 2 and out["next"] is None
    assert [b["domain"] for b in out["domains"]] == ["journal.work"]  # a root opens its children
    assert out["domains"][0]["classes"][0]["types"] == {"top": [["visit", 2]], "distinct": 1}
    assert [b["domain"] for b in _run("--domain", "journal.work")["domains"]] == ["journal.work"]


def test_output_budget_never_stores_orient_behind_a_handle(wired, monkeypatch):
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "50")
    out = _run()
    assert "handle" not in json.dumps(out) and out["domains"]


def test_graph_down_peel_one_degrades_peel_two_fails(wired, monkeypatch):
    from artmind.catalogue import cache

    from neo4j.exceptions import ServiceUnavailable

    def down(domains, parts):
        raise ServiceUnavailable("connection refused")

    monkeypatch.setattr(cache, "get_detailed", down)
    out = _run()
    assert any("graph unreachable" in w for w in out["warnings"])
    first = out["domains"][0]
    assert first["purpose"] == "journal notes" and "dated" not in first["time"] and "unit" not in first
    r = _run("--domain", "journal", code=3)
    assert "neo4j unreachable" in r.output  # printed by the root group's store-failure mapping


def test_read_only_and_listed_in_the_cli_guide():
    from artmind import cli_conventions as cc

    assert "query orient" not in cc.WRITE_COMMANDS
    import artmind.cli as c

    lookups = next(g for g in c.click.rich_click.COMMAND_GROUPS["artmind query"] if g["name"] == "Lookups")
    assert lookups["commands"][0] == "orient"


def test_peel_two_discovers_domains_once_and_asks_for_the_orient_parts(wired, monkeypatch):
    n = []
    monkeypatch.setattr(orient, "_known_domains", lambda warnings=None: n.append(1) or ["journal", "journal.work"])
    _run("--domain", "journal")
    assert len(n) == 1
    assert wired["parts"] == orient.PARTS


def test_root_with_data_of_its_own_is_reported(wired):
    wired["fp"] = {"journal": (3, 5, 4, 0, ""), "journal.work": (3, 2, 1, 0, "")}
    out = _run("--domain", "journal")
    assert any(w.startswith("journal: 3 documents and 3 entities sit at the root itself") for w in out["warnings"])
    assert [b["domain"] for b in out["domains"]] == ["journal.work"]
    wired["fp"] = {"journal": (3, 2, 1, 0, ""), "journal.work": (3, 2, 1, 0, "")}
    assert not any("root itself" in w for w in _run("--domain", "journal")["warnings"])


def test_corpus_built_at_is_the_oldest(wired):
    wired["built"] = {"journal": "2026-11-09T00:00:00+00:00", "journal.work": "2026-11-01T00:00:00+00:00"}
    out = _run()
    assert out["corpus"]["built_at"] == "2026-11-01T00:00:00+00:00"
    assert out["cuts"] == []


def test_a_bug_in_the_catalogue_propagates_in_peel_one(wired, monkeypatch):
    from artmind.catalogue import cache

    def boom(domains, parts):
        raise KeyError("bug")

    monkeypatch.setattr(cache, "get_detailed", boom)
    r = CliRunner().invoke(cli, ["query", "orient", "--compact"])
    assert r.exit_code not in (0, 3) and isinstance(r.exception, KeyError)


def test_graph_down_while_discovering_domains_is_warned(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    from artmind import domain_guard

    monkeypatch.setattr(domain_guard, "_schema_domains", lambda: {"a"})
    monkeypatch.setattr(domain_guard, "_table_domains", lambda: set())

    def down():
        raise ServiceUnavailable("refused")

    monkeypatch.setattr(domain_guard, "_graph_domains", down)
    w = []
    assert orient._known_domains(w) == ["a"]
    assert w == ["graph unreachable: domains with only graph data are not listed"]
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: (_ for _ in ()).throw(KeyError("bug")))
    with pytest.raises(KeyError):
        orient._known_domains([])


def test_coverage_gap_is_one_warning_not_one_per_domain(wired):
    out = _run()
    assert [w for w in out["warnings"] if w.startswith("coverage not computed")] == [
        "coverage not computed for 2 domains: health not cached"]
    assert len([w for w in _run("--domain", "journal")["warnings"] if w.startswith("coverage not")]) == 1


def test_corpus_generation_sums_top_level_domains_only(wired):
    wired["fp"] = {"journal": (10, 2, 1, 0, ""), "journal.work": (4, 2, 1, 0, "")}
    assert _run()["corpus"]["generation"] == 10


def test_table_store_failure_is_warned_and_a_bug_propagates(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    from artmind import domain_guard

    monkeypatch.setattr(domain_guard, "_schema_domains", lambda: {"a"})
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: set())

    def down():
        raise ServiceUnavailable("refused")

    monkeypatch.setattr(domain_guard, "_table_domains", down)
    w = []
    assert orient._known_domains(w) == ["a"]
    assert w == ["structured store unreachable: domains with only tables are not listed"]
    monkeypatch.setattr(domain_guard, "_table_domains", lambda: (_ for _ in ()).throw(KeyError("bug")))
    with pytest.raises(KeyError):
        orient._known_domains([])
