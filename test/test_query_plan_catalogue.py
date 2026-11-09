"""The domain catalogue (spec 2026-10-22 §11): reads, cache layers, freshness."""

import json
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.catalogue import cache, parts
from artmind.query_plan import catalogue


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.entities = 3
        self.generation = 5
        self.names = [
            {"_id": "e1", "name": "Ada", "aliases": ["A."], "entity_class": "PERSON", "observation_count": 5},
            {"_id": "e2", "name": "Lakeport", "aliases": None, "entity_class": "PLACE", "observation_count": 2},
        ]

    def __call__(self, cypher, params):
        self.calls.append((cypher, params))
        if "DomainState" in cypher:
            return [{"domain": d, "generation": self.generation, "entities": self.entities, "documents": 2,
                     "history": 1} for d in params["domains"]]
        if "e.aliases" in cypher:
            return self.names[: params["limit"]]
        if "count(e) AS n" in cypher:
            return [{"name": "PLACE", "n": 4}, {"name": "PERSON", "n": 4}, {"name": "ITEM", "n": 1}]
        if "RELATES_TO" in cypher:
            return [{"rel_type": "VISITED", "n": 7}]
        if "AS has_summary" in cypher:
            return [{"id": "d2", "name": "b.md", "domain": "journal", "valid_from": "2026-02-01", "live": True,
                     "has_summary": False},
                    {"id": "d1", "name": "a.md", "domain": "journal", "valid_from": None, "live": True,
                     "has_summary": True},
                    {"id": "d0", "name": "a.md", "domain": "journal", "valid_from": "2026-01-01", "live": False,
                     "has_summary": True}]
        if "with_valid_from" in cypher:
            return [{"documents": 4, "with_valid_from": 3, "earliest": "2026-01-01", "latest": "2026-03-01"}]
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
    monkeypatch.setattr(parts, "_class_descriptions", lambda d: {"PERSON": "A person."})
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
    assert "UNWIND $domains" in cypher and "DocumentHistory" in cypher and "DomainState" in cypher
    assert "ProjectionState" not in cypher and "last_rebuilt_at" not in cypher
    assert "STARTS WITH (dom + '.')" in cypher
    stamp = fps["a"][4]
    assert fps == {"a": (5, 3, 2, 1, stamp), "b.c": (5, 3, 2, 1, fps["b.c"][4])}


def test_build_reads_and_sorts(graph):
    g, _ = graph
    cat = catalogue.build("journal", (5, 3, 2, 1, "x"))
    assert [p for _, p in g.calls] == [
        {"domains": ["journal"]}, {"domains": ["journal"], "limit": 50},
        {"domains": ["journal"]}, {"domains": ["journal"], "limit": catalogue.NAME_INDEX_MAX + 1},
    ]
    assert cat.has("names") and not cat.has("stores")
    assert all("_domain IN $domains" in c for c, _ in g.calls)
    assert cat.classes == (("PLACE", 4, ""), ("PERSON", 4, "A person."), ("ITEM", 1, ""))
    assert cat.relations == (("VISITED", 7),)
    assert cat.documents == ("a.md", "b.md")
    assert [n.name for n in cat.names] == ["Ada", "Lakeport"]
    assert cat.names[1].aliases == () and not cat.truncated
    assert [(r.id, r.name, r.valid_from, r.has_summary) for r in cat.doc_records] == [
        ("d1", "a.md", None, True), ("d2", "b.md", "2026-02-01", False)]  # live only, sorted


def test_truncated_when_cap_exceeded(graph, monkeypatch):
    g, _ = graph
    monkeypatch.setattr(parts, "NAME_INDEX_MAX", 1)
    cat = catalogue.build("journal", ())
    assert cat.truncated and len(cat.names) == 1


def test_json_round_trip(graph):
    cat = catalogue.build("journal", (5, 3, 2, 1, "x"))
    again = catalogue.DomainCatalogue.from_json(json.loads(json.dumps(cat.to_json())))
    assert again == cat


def test_missing_schema_tolerated(monkeypatch):
    from artmind import temporal

    def boom(domain):
        raise FileNotFoundError(domain)

    monkeypatch.setattr(temporal, "load_schema", boom)
    assert parts._class_descriptions("nope") == {}


def test_memory_reuse_and_fingerprint_change(graph):
    g, clock = graph
    cats, src = catalogue.get(["journal"])
    assert src == {"journal": "live"}
    clock.t += 11  # past the fingerprint TTL: one check, no rebuild
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "memory"} and g.count("DomainState") == 2 and g.count("e.aliases") == 1
    g.generation = 6  # a partial rebuild or alias change moves the generation, not the counts
    clock.t += 11
    cats2, src = catalogue.get(["journal"])
    assert src == {"journal": "live"} and g.count("e.aliases") == 2
    assert cats2["journal"].fingerprint[0] == 6
    g.entities = 4  # a writer outside the choke points still shows in the counts
    clock.t += 11
    cats3, src = catalogue.get(["journal"])
    assert src == {"journal": "live"} and cats3["journal"].fingerprint[1] == 4


def test_fingerprint_ttl_skips_check(graph):
    g, clock = graph
    catalogue.get(["journal"])
    clock.t += 5
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "memory"} and g.count("DomainState") == 1


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


def test_a_cache_file_of_another_format_is_ignored(graph):
    graph, _ = graph
    cat = catalogue.build("journal")
    path = cache._disk_path("journal")
    data = cat.to_json()
    assert data["format"] == catalogue.CATALOGUE_FORMAT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    assert cache._read_disk("journal") is not None
    data.pop("format")
    path.write_text(json.dumps(data))
    assert cache._read_disk("journal") is None
    data["format"] = catalogue.CATALOGUE_FORMAT - 1
    path.write_text(json.dumps(data))
    assert cache._read_disk("journal") is None


def test_the_names_query_asks_for_domain_and_connections(graph):
    graph, _ = graph
    catalogue.build("journal")
    cypher = next(c for c, _ in graph.calls if "e.aliases" in c)
    assert "e._domain AS domain" in cypher and "RELATES_TO" in cypher and "AS connection_count" in cypher


def test_concurrent_gets_build_a_domain_once_and_other_domains_are_not_blocked(graph, monkeypatch):
    import threading

    graph, clock = graph
    started, release = threading.Event(), threading.Event()
    builds = []
    real_build = cache.build_part

    def slow_build(domain, part, fingerprint):
        if part == "names":
            builds.append(domain)
        if domain == "slow" and part == "names":
            started.set()
            assert release.wait(5)
        return real_build(domain, part, fingerprint)

    monkeypatch.setattr(cache, "build_part", slow_build)
    catalogue.get(["fresh"])  # cached now
    results = {}

    def call(name):
        results[name] = catalogue.get(["slow"])

    t1 = threading.Thread(target=call, args=("one",))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=call, args=("two",))
    t2.start()
    # a fresh domain is served while "slow" is still building
    cats, sources = catalogue.get(["fresh"])
    assert sources == {"fresh": "memory"}
    release.set()
    t1.join(5)
    t2.join(5)
    assert builds.count("slow") == 1
    assert {s["slow"] for _, s in results.values()} <= {"live", "memory"}
    assert results["one"][1] == {"slow": "live"}


def test_a_schema_stamp_change_alone_invalidates_only_the_classes_part(graph, tmp_path, monkeypatch):
    import paths

    g, clock = graph
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", schemas)
    catalogue.get(["journal"])
    clock.t += 11
    _, src = catalogue.get(["journal"])
    assert src == {"journal": "memory"}
    before = g.count("e.aliases")
    (schemas / "journal_schema.yaml").write_text("name: journal\n")  # a schema edit; the graph is untouched
    clock.t += 11
    _, detail = cache.get_detailed(["journal"])
    assert detail["journal"] == {"classes": "live", "relations": "memory", "documents": "memory", "names": "memory"}
    assert g.count("e.aliases") == before  # the name index was not rebuilt


def test_a_generation_change_rebuilds_every_graph_part_alone(graph):
    g, clock = graph
    catalogue.get(["journal"], parts=("classes", "names", "time"))
    g.generation = 6
    clock.t += 11
    _, detail = cache.get_detailed(["journal"], parts=("classes", "names"))
    assert detail["journal"] == {"classes": "live", "names": "live"}


def test_parts_are_built_on_demand_and_cached_separately(graph):
    g, clock = graph
    catalogue.get(["journal"])  # the planner's four
    assert g.count("e.aliases") == 1
    cats, detail = cache.get_detailed(["journal"], parts=("classes", "stores"))
    assert detail["journal"] == {"classes": "memory", "stores": "live"}
    assert cats["journal"].stores is not None and cats["journal"].has("stores")


def test_an_unknown_part_is_refused(graph):
    with pytest.raises(ValueError, match="unknown catalogue part"):
        catalogue.get(["journal"], parts=("nope",))


def test_the_age_limit_is_an_hour():
    assert catalogue.MAX_AGE_S == 3600.0


def test_the_time_part_reports_the_dated_share(graph):
    cat = catalogue.build("journal", (5, 3, 2, 1, "x"), parts=("time",))
    assert cat.time == {"documents": 4, "with_valid_from": 3, "share": 0.75,
                        "earliest": "2026-01-01", "latest": "2026-03-01",
                        "from_content": 0, "content_earliest": None, "content_latest": None}


def test_the_stores_part_reads_the_schema_flag_and_the_registry(graph, monkeypatch):
    from artmind import schema_validate
    from artmind.structured import registry

    monkeypatch.setattr(schema_validate, "domain_graph_extraction", lambda d: False)
    monkeypatch.setattr(registry, "list_tables", lambda domains, **kw: [{"table_name": "t"}] if domains == ["journal"] else [])
    cat = catalogue.build("journal", (5, 3, 2, 1, "x"), parts=("stores",))
    assert cat.stores == {"graph_extraction": False, "kind": "chunks", "structured_tables": 1}


def test_an_empty_domain_has_a_zero_share(graph, monkeypatch):
    g, _ = graph
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: [])
    cat = catalogue.build("journal", (0, 0, 0, 0, ""), parts=("time", "documents"))
    assert cat.time["share"] == 0.0 and cat.time["earliest"] is None and cat.doc_records == ()


def test_the_format_is_four(graph):
    assert catalogue.CATALOGUE_FORMAT == 4


def test_document_dates_are_read_from_the_stored_property(graph):
    """A Document node stores its valid time as `_valid_from` (ingest writes the underscore
    name). A query on `valid_from` finds no date on any document, and a fake that matches
    on a substring never notices."""
    g, _ = graph
    catalogue.build("journal", (5, 3, 2, 1, "x"), parts=("documents", "time"))
    sent = [c for c, _ in g.calls if "AS has_summary" in c or "with_valid_from" in c]
    assert len(sent) == 2
    for cypher in sent:
        assert "d._valid_from" in cypher
        assert "d.valid_from" not in cypher.replace("d._valid_from", "")
