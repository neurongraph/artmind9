"""The domain catalogue (spec 2026-10-22 §11): reads, cache layers, freshness."""

import json
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.query_plan import catalogue


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.entities = 3
        self.names = [
            {"_id": "e1", "name": "Ada", "aliases": ["A."], "entity_class": "PERSON", "observation_count": 5},
            {"_id": "e2", "name": "Lakeport", "aliases": None, "entity_class": "PLACE", "observation_count": 2},
        ]

    def __call__(self, cypher, params):
        self.calls.append((cypher, params))
        if "ProjectionState" in cypher:
            return [{"domain": d, "entities": self.entities, "documents": 2, "history": 1, "rebuilt": "t1"}
                    for d in params["domains"]]
        if "count(e) AS n" in cypher:
            return [{"name": "PLACE", "n": 4}, {"name": "PERSON", "n": 4}, {"name": "ITEM", "n": 1}]
        if "RELATES_TO" in cypher:
            return [{"rel_type": "VISITED", "n": 7}]
        if "DISTINCT d.name" in cypher:
            return [{"name": "b.md"}, {"name": "a.md"}, {"name": "a.md"}]
        if "e.aliases" in cypher:
            return self.names[: params["limit"]]
        raise AssertionError(cypher)

    def count(self, marker):
        return sum(1 for c, _ in self.calls if marker in c)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def graph(monkeypatch):
    g = FakeGraph()
    monkeypatch.setattr(graph_query, "_run_read_query", g)
    monkeypatch.setattr(catalogue, "_class_descriptions", lambda d: {"PERSON": "A person."})
    monkeypatch.delenv("ARTMIND_PLAN_CATALOG", raising=False)
    import shutil

    import paths

    shutil.rmtree(Path(paths.LOGS_DIR) / "plan-catalog", ignore_errors=True)
    catalogue.reset()
    clock = Clock()
    catalogue.set_clock(clock)
    yield g, clock
    catalogue.reset()


def test_fingerprints_one_query_for_all_domains(graph):
    g, _ = graph
    fps = catalogue.fingerprints(["a", "b.c"])
    assert len(g.calls) == 1
    cypher, params = g.calls[0]
    assert params == {"domains": ["a", "b.c"]}
    assert "UNWIND $domains" in cypher and "DocumentHistory" in cypher and "last_rebuilt_at" in cypher
    assert "STARTS WITH (dom + '.')" in cypher
    assert fps == {"a": (3, 2, 1, "t1"), "b.c": (3, 2, 1, "t1")}


def test_build_reads_and_sorts(graph):
    g, _ = graph
    cat = catalogue.build("journal", (3, 2, 1, "t1"))
    assert [p for _, p in g.calls] == [
        {"domains": ["journal"]}, {"domains": ["journal"], "limit": 50},
        {"domains": ["journal"]}, {"domains": ["journal"], "limit": catalogue.NAME_INDEX_MAX + 1},
    ]
    assert all("_domain IN $domains" in c for c, _ in g.calls)
    assert cat.classes == (("PLACE", 4, ""), ("PERSON", 4, "A person."), ("ITEM", 1, ""))
    assert cat.relations == (("VISITED", 7),)
    assert cat.documents == ("a.md", "b.md")
    assert [n.name for n in cat.names] == ["Ada", "Lakeport"]
    assert cat.names[1].aliases == () and not cat.truncated


def test_truncated_when_cap_exceeded(graph, monkeypatch):
    g, _ = graph
    monkeypatch.setattr(catalogue, "NAME_INDEX_MAX", 1)
    cat = catalogue.build("journal", ())
    assert cat.truncated and len(cat.names) == 1


def test_json_round_trip(graph):
    cat = catalogue.build("journal", (3, 2, 1, "t1"))
    again = catalogue.DomainCatalogue.from_json(json.loads(json.dumps(cat.to_json())))
    assert again == cat


def test_missing_schema_tolerated(monkeypatch):
    from artmind import temporal

    def boom(domain):
        raise FileNotFoundError(domain)

    monkeypatch.setattr(temporal, "load_schema", boom)
    assert catalogue._class_descriptions("nope") == {}


def test_memory_reuse_and_fingerprint_change(graph):
    g, clock = graph
    cats, src = catalogue.get(["journal"])
    assert src == {"journal": "live"}
    clock.t += 11  # past the fingerprint TTL: one check, no rebuild
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "memory"} and g.count("ProjectionState") == 2 and g.count("e.aliases") == 1
    g.entities = 4
    clock.t += 11
    cats2, src = catalogue.get(["journal"])
    assert src == {"journal": "live"} and g.count("e.aliases") == 2
    assert cats2["journal"].fingerprint[0] == 4


def test_fingerprint_ttl_skips_check(graph):
    g, clock = graph
    catalogue.get(["journal"])
    clock.t += 5
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "memory"} and g.count("ProjectionState") == 1


def test_max_age_forces_rebuild(graph):
    g, clock = graph
    catalogue.get(["journal"])
    clock.t += catalogue.MAX_AGE_S + 1
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "live"} and g.count("e.aliases") == 2


def test_disk_hit_in_fresh_process(graph):
    import paths

    g, clock = graph
    catalogue.get(["journal"])
    assert (Path(paths.LOGS_DIR) / "plan-catalog" / "journal.json").is_file()
    catalogue.reset()
    catalogue.set_clock(clock)
    cats, src = catalogue.get(["journal"])
    assert src == {"journal": "disk"} and g.count("e.aliases") == 1
    assert cats["journal"].names[0].name == "Ada"


def test_corrupt_disk_file_ignored(graph):
    import paths

    g, clock = graph
    path = Path(paths.LOGS_DIR) / "plan-catalog" / "journal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "live"}
    assert json.loads(path.read_text())["domain"] == "journal"  # replaced atomically


def test_disabled_bypasses_cache(graph, monkeypatch):
    import paths

    g, _ = graph
    monkeypatch.setenv("ARTMIND_PLAN_CATALOG", "0")
    catalogue.get(["fresh_dom"])
    _, src = catalogue.get(["fresh_dom"])
    assert src == {"fresh_dom": "live"} and g.count("e.aliases") == 2
    assert not (Path(paths.LOGS_DIR) / "plan-catalog" / "fresh_dom.json").exists()


def test_invalid_domain_name_not_written(graph):
    import paths

    _, src = catalogue.get(["../evil"])
    assert src == {"../evil": "live"}
    assert not (Path(paths.LOGS_DIR) / "plan-catalog").exists() or not list(
        (Path(paths.LOGS_DIR) / "plan-catalog").glob("*evil*"))
    assert not (Path(paths.LOGS_DIR) / "evil.json").exists()
