"""`ingest table2graph`: a declarative table mapping, executed deterministically.

Hermetic: `build_staged` is pure, and the CLI tests patch the table read and
the Neo4j commit. Graph writes are asserted on the parameters actually sent
(CLAUDE.md, "A mocked graph session answers every query successfully").
"""
import json
from datetime import date

import pytest
import yaml
from click.testing import CliRunner

from artmind import table2graph as t2g
from artmind.observations import aggregate_key, build_observation, normalize_identity


SCHEMA = {
    "name": "perf",
    "entity_types": {
        "PERSON": {
            "kind": "recurrent",
            "properties": {
                "talent_id": {}, "first_name": {}, "last_name": {}, "full_name": {},
                "band": {}, "roles": {}, "is_functional_manager": {},
            },
            "relates_to": {"PERSON": ["reports_to"], "PERFORMANCE_SEGMENT": ["received"]},
        },
        "PERFORMANCE_SEGMENT": {
            "kind": "occurrent",
            "properties": {
                "segment": {}, "year": {}, "segmented_on": {"temporal": "valid_from"},
                "productive_utilization": {},
            },
        },
    },
}

MAPPING = {
    "table": "hr_export_*",
    "domain": "perf",
    "null_values": ["--", "N/A"],
    "row_key": ["TalentID"],
    "entities": {
        "person": {
            "class": "PERSON",
            "name": "{first_name} {last_name} ({talent_id})",
            "type": "practitioner",
            "properties": {
                "talent_id": {"column": "TalentID", "required": True},
                "first_name": {"column": "Employee Name", "transform": [{"split": {"sep": ",", "index": 1}}, "title"]},
                "last_name": {"column": "Employee Name", "transform": [{"split": {"sep": ",", "index": 0}}, "title"]},
                "full_name": {"template": "{first_name} {last_name}"},
                "band": "Band",
                "is_functional_manager": {"column": "Mgr?", "map": {"Y": "yes", "N": "no"}},
            },
        },
        "segment": {
            "class": "PERFORMANCE_SEGMENT",
            "name": "{year} Performance Segment - {person.name}",
            "properties": {
                "segment": {"column": "Segment", "map": {"Top": "top", "Core": "core", "Low": "low"}, "default": "not_applicable"},
                "segmented_on": {"column": "Seg Date", "transform": "date"},
                "year": {"column": "Seg Date", "transform": "year", "default": {"column": "$ingested_at", "transform": "year"}},
                "productive_utilization": {"column": "Ute", "transform": "number"},
            },
        },
        "manager": {
            "class": "PERSON",
            "lookup": {
                "entity": "person", "column": "FLM", "match_column": "Employee Name",
                "prefer": [{"column": "Mgr?", "equals": "Y"}],
                "on_unresolved": "stub", "on_ambiguous": "skip",
            },
            "name": "{first_name} {last_name}",
            "properties": {
                "first_name": {"column": "FLM", "transform": [{"split": {"sep": ",", "index": 1}}, "title"]},
                "last_name": {"column": "FLM", "transform": [{"split": {"sep": ",", "index": 0}}, "title"]},
                "roles": {"value": ["people_manager"]},
            },
        },
    },
    "relationships": [
        {"source": "person", "rel_type": "reports_to", "target": "manager"},
        {"source": "person", "rel_type": "received", "target": "segment"},
    ],
}

COLUMNS = ["TalentID", "Employee Name", "Band", "Mgr?", "FLM", "Segment", "Seg Date", "Ute"]

TABLE = {
    "id": 1, "table_name": "hr_export_20260921", "domain": "perf", "version": 1,
    "refresh_mode": "replace", "business_key": None,
    "ingested_at": "2026-09-23T11:57:52.163766", "source_file": "hr/export.xlsx",
}


def _row(tid, name, flm, *, band="7", mgr="N", segment="Core", seg_date="2025-10-31", ute="72.5"):
    return {
        "TalentID": tid, "Employee Name": name, "Band": band, "Mgr?": mgr, "FLM": flm,
        "Segment": segment, "Seg Date": seg_date, "Ute": ute,
    }


ROWS = [
    _row("001", "JAIN, POOJA", "Kothari, Anubhav"),
    _row("002", "Jain, Pooja", "Kothari, Anubhav", segment="--", seg_date="N/A"),
    _row("003", "Kothari, Anubhav", "DAS, SURJIT", mgr="Y", segment="Top", band="9"),
    # Two "Deshmukh, Neha": only the functional manager is anyone's manager.
    _row("004", "Deshmukh, Neha", "Kothari, Anubhav", mgr="Y"),
    _row("005", "Deshmukh, Neha", "Kothari, Anubhav", mgr="N"),
    _row("006", "Rao, Ravi", "Deshmukh, Neha"),
]


def _mapping(**overrides):
    return t2g.parse_mapping({**MAPPING, **overrides})


def _build(rows=ROWS, table=TABLE, mapping=None, columns=COLUMNS):
    return t2g.build_staged(table, rows, columns, mapping or _mapping(), SCHEMA)


def _obs(staged, name):
    matches = [o for o in staged["observations"] if o["name"] == name]
    assert len(matches) == 1, f"{name!r}: {[o['name'] for o in staged['observations']]}"
    return matches[0]


# ── identity ─────────────────────────────────────────────────────────────────


def test_declared_identity_keeps_the_digit_parenthetical_normalize_name_strips():
    assert aggregate_key("Pooja Jain (002PZE744)", "PERSON", "d")[0] == "pooja jain"
    assert aggregate_key("x", "PERSON", "d", identity="Pooja Jain (002PZE744)")[0] == "pooja jain (002pze744)"
    assert normalize_identity("  Pooja Jain  (002PZE744) ") == "pooja jain (002pze744)"


def test_build_observation_identity_separates_same_named_people():
    def obs(name):
        return build_observation(
            {"name": name, "entity_class": "PERSON", "_domain": "d"}, canonical_name=name,
            domain_props={}, doc_id="doc", doc_version=1, chunk_id="c", kind="recurrent",
            doc_valid_from="2026-01-01", identity=name,
        )
    a, b = obs("Pooja Jain (002PZE744)"), obs("Pooja Jain (003SZR744)")
    assert a["key"] != b["key"] and a["id"] != b["id"]
    assert a["key"] == "pooja jain (002pze744)|PERSON|d"


# ── mapping parse + validation ───────────────────────────────────────────────


@pytest.mark.parametrize("patch, message", [
    ({"table": None}, "'table'"),
    ({"entities": {}}, "'entities'"),
    ({"entities": {"p": {"class": "PERSON"}}}, "'name'"),
    ({"entities": {"p": {"class": "PERSON", "name": "{x}", "versions": "some"}}}, "versions"),
    ({"entities": {"p": {"class": "PERSON", "name": "{x}", "properties": {"x": {"column": "A", "transform": "shout"}}}}}, "unknown transform"),
    ({"entities": {"p": {"class": "PERSON", "name": "{x}", "properties": {"x": {"column": "A", "value": 1}}}}}, "exactly one of"),
])
def test_parse_mapping_rejects_malformed_documents(patch, message):
    with pytest.raises(t2g.MappingError, match=message):
        t2g.parse_mapping({**MAPPING, **patch})


def test_validate_mapping_accepts_the_reference_mapping():
    errors, warnings = t2g.validate_mapping(_mapping(), SCHEMA, COLUMNS)
    assert errors == [] and warnings == []


def test_validate_mapping_reports_errors_and_schema_drift():
    mapping = t2g.parse_mapping({
        "table": "t",
        "entities": {
            "person": {
                "class": "PERSON", "name": "{talent_id} {nickname}",
                "properties": {
                    "talent_id": "TalentID",
                    "name": "Employee Name",           # reserved
                    "shoe_size": "Band",               # undeclared -> warning
                    "band": "Missing Column",          # unknown column
                },
            },
            "thing": {"class": "GADGET", "name": "{person.name}"},
        },
        "relationships": [
            {"source": "person", "rel_type": "admires", "target": "person"},
            {"source": "person", "rel_type": "EXTRACTED_FROM", "target": "thing"},
        ],
    })
    errors, warnings = t2g.validate_mapping(mapping, SCHEMA, COLUMNS)
    joined = "\n".join(errors)
    assert "'name' is reserved" in joined
    assert "'Missing Column' is not in the table" in joined
    assert "{nickname}" in joined
    assert "class 'GADGET' is not declared" in joined
    assert "reserved for artmind" in joined
    assert any("shoe_size" in w for w in warnings)
    assert any("'admires' is not declared between PERSON and PERSON" in w for w in warnings)


def test_find_mappings_matches_table_globs_and_domain(tmp_path):
    (tmp_path / "hr.yaml").write_text(yaml.safe_dump(MAPPING, sort_keys=False))
    (tmp_path / "other.yaml").write_text(yaml.safe_dump({**MAPPING, "table": "sales_*"}, sort_keys=False))
    assert [m.path.name for m in t2g.find_mappings("hr_export_20261021", "perf", tmp_path)] == ["hr.yaml"]
    assert t2g.find_mappings("hr_export_20261021", "banking", tmp_path) == []


# ── building: the replace-mode snapshot ──────────────────────────────────────


def test_rows_become_named_entities_with_declared_properties():
    staged, report = _build()
    pooja = _obs(staged, "Pooja Jain (001)")
    assert pooja["key"] == "pooja jain (001)|PERSON|perf"
    assert pooja["first_name"] == "Pooja" and pooja["last_name"] == "Jain"
    assert pooja["full_name"] == "Pooja Jain"
    assert pooja["is_functional_manager"] == "no"
    assert pooja["type"] == "practitioner"
    assert pooja["source_kind"] == "table"
    assert pooja["chunk_id"] == "table:perf:hr_export_20260921#001"
    # The two Pooja Jains stay two entities.
    assert _obs(staged, "Pooja Jain (002)")["key"] != pooja["key"]
    assert report["entities"]["person"]["kept"] == 6


def test_segment_value_map_default_and_year_fallback():
    staged, _ = _build()
    seg = _obs(staged, "2025 Performance Segment - Pooja Jain (001)")
    assert seg["segment"] == "core"
    assert seg["year"] == 2025
    assert seg["productive_utilization"] == 72.5
    # The schema tags segmented_on `temporal: valid_from`: the fact's own date.
    assert seg["_valid_from"] == "2025-10-31" and seg["_valid_time_source"] == "property"
    # "--" is not skipped: it becomes the declared default, and a missing date
    # falls back to the table's ingest year.
    unsegmented = _obs(staged, "2026 Performance Segment - Pooja Jain (002)")
    assert unsegmented["segment"] == "not_applicable"
    assert "segmented_on" not in unsegmented
    assert unsegmented["_valid_from"] == "2026-09-23"   # document.valid_from = ingest date


def test_lookup_resolves_prefers_and_stubs():
    staged, report = _build()
    by_id = {o["id"]: o["name"] for o in staged["observations"]}
    reports_to = {
        (by_id[r["source_observation_id"]], by_id[r["target_observation_id"]])
        for r in staged["relationships"] if r["rel_type"] == "reports_to"
    }
    assert ("Pooja Jain (001)", "Anubhav Kothari (003)") in reports_to
    # The ambiguous "Deshmukh, Neha" is settled by `prefer` (the functional manager).
    assert ("Ravi Rao (006)", "Neha Deshmukh (004)") in reports_to
    # A manager with no row of their own becomes a name-only stub.
    assert ("Anubhav Kothari (003)", "Surjit Das") in reports_to
    stub = _obs(staged, "Surjit Das")
    assert stub["key"] == "surjit das|PERSON|perf" and stub["roles"] == ["people_manager"]

    lookup = report["lookups"]["manager"]
    assert lookup["resolved"] == 5 and lookup["stubbed"] == 1
    assert lookup["unresolved_values"] == ["DAS, SURJIT"]


def test_ambiguous_lookup_is_skipped_without_a_tiebreak():
    mapping = json.loads(json.dumps(MAPPING))
    mapping["entities"]["manager"]["lookup"]["prefer"] = []
    staged, report = _build(mapping=t2g.parse_mapping(mapping))
    assert report["lookups"]["manager"]["skipped_ambiguous"] == 1
    assert report["lookups"]["manager"]["ambiguous_values"] == ["Deshmukh, Neha"]
    assert report["relationships"]["person-reports_to->manager"]["skipped"] == {
        "an endpoint is missing in this row": 1
    }


def test_relationships_name_observation_ids_and_the_row_chunk():
    staged, _ = _build()
    ids = {o["id"] for o in staged["observations"]}
    for rel in staged["relationships"]:
        assert rel["source_observation_id"] in ids and rel["target_observation_id"] in ids
        assert rel["chunk_id"].startswith("table:perf:hr_export_20260921#")
    received = [r for r in staged["relationships"] if r["rel_type"] == "received"]
    assert len(received) == 6


def test_required_property_skips_the_entity_and_counts_why():
    rows = ROWS + [_row(None, "Nobody, No", "Kothari, Anubhav")]
    staged, report = _build(rows=rows)
    skipped = report["entities"]["person"]["skipped"]
    assert skipped == {"required property 'talent_id' is empty": 1}
    # Its segment's name needs {person.name}, so it is skipped too.
    assert "name template needs {person.name}, which is empty" in report["entities"]["segment"]["skipped"]
    # A row that contributed nothing is not a chunk.
    assert len(staged["chunks"]) == 6


def test_document_and_chunks_carry_table_provenance():
    staged, _ = _build()
    doc = staged["document"]
    assert doc["id"] == "table:perf:hr_export_20260921"
    assert doc["source_kind"] == "table" and doc["_valid_from"] == "2026-09-23"
    chunk = next(c for c in staged["chunks"] if c["row_key"] == "001")
    assert chunk["doc_id"] == doc["id"]
    assert chunk["text"].splitlines()[:3] == [
        "Table hr_export_20260921, row 001", "TalentID: 001", "Employee Name: JAIN, POOJA",
    ]
    # Null tokens are left out of the rendered text.
    assert "Seg Date" not in next(c for c in staged["chunks"] if c["row_key"] == "002")["text"]


def test_build_is_deterministic():
    assert _build()[0] == _build()[0]


# ── building: temporal (SCD-2) tables ────────────────────────────────────────


TEMPORAL = {**TABLE, "refresh_mode": "temporal", "business_key": "TalentID"}


def _version(row, valid_from, current):
    return {**row, "_valid_from": date.fromisoformat(valid_from), "_valid_to": None, "_is_current": current}


def test_temporal_latest_keeps_each_identity_once_and_old_segments_survive():
    rows = [
        _version(_row("001", "Jain, Pooja", "Kothari, Anubhav", band="6", ute="60"), "2025-11-01", False),
        # Same 2025 segment, drifted metric: `latest` keeps only this version.
        _version(_row("001", "Jain, Pooja", "Kothari, Anubhav", band="6", ute="65"), "2025-12-01", False),
        _version(_row("001", "Jain, Pooja", "Rao, Ravi", band="7", segment="Top", seg_date="2026-10-02"), "2026-10-05", True),
        _version(_row("003", "Kothari, Anubhav", "DAS, SURJIT", mgr="Y"), "2025-11-01", True),
        _version(_row("006", "Rao, Ravi", "Kothari, Anubhav"), "2025-11-01", True),
    ]
    staged, report = _build(rows=rows, table=TEMPORAL)

    persons = [o for o in staged["observations"] if o["name"] == "Pooja Jain (001)"]
    assert len(persons) == 1 and persons[0]["band"] == "7"
    assert persons[0]["chunk_id"] == "table:perf:hr_export_20260921#001@2026-10-05"
    assert persons[0]["_doc_valid_from"] == "2026-10-05"

    seg_2025 = _obs(staged, "2025 Performance Segment - Pooja Jain (001)")
    assert seg_2025["productive_utilization"] == 65
    assert seg_2025["chunk_id"].endswith("@2025-12-01")
    assert _obs(staged, "2026 Performance Segment - Pooja Jain (001)")["segment"] == "top"

    by_id = {o["id"]: o["name"] for o in staged["observations"]}
    edges = {(by_id[r["source_observation_id"]], r["rel_type"], by_id[r["target_observation_id"]]) for r in staged["relationships"]}
    # Both years' segments are still received by the one PERSON...
    assert ("Pooja Jain (001)", "received", "2025 Performance Segment - Pooja Jain (001)") in edges
    assert ("Pooja Jain (001)", "received", "2026 Performance Segment - Pooja Jain (001)") in edges
    # ...but only the current manager is reported to.
    assert ("Pooja Jain (001)", "reports_to", "Ravi Rao (006)") in edges
    assert ("Pooja Jain (001)", "reports_to", "Anubhav Kothari (003)") not in edges
    assert report["relationships"]["person-reports_to->manager"]["skipped"]["superseded row version"] == 2
    # Only contributing row versions become chunks (the first 2025 version didn't).
    assert not any(c["id"].endswith("#001@2025-11-01") for c in staged["chunks"])


def test_temporal_versions_all_keeps_every_version():
    mapping = json.loads(json.dumps(MAPPING))
    mapping["entities"]["person"]["versions"] = "all"
    rows = [
        _version(_row("001", "Jain, Pooja", "Kothari, Anubhav", band="6"), "2025-11-01", False),
        _version(_row("001", "Jain, Pooja", "Kothari, Anubhav", band="7"), "2026-10-05", True),
    ]
    staged, _ = _build(rows=rows, table=TEMPORAL, mapping=t2g.parse_mapping(mapping))
    bands = sorted(o["band"] for o in staged["observations"] if o["name"] == "Pooja Jain (001)")
    assert bands == ["6", "7"]


# ── transforms ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    ("DAS, SURJIT", "Das, Surjit"), ("McDonald", "McDonald"), ("O'BRIEN-SMITH", "O'Brien-Smith"), ("G", "G"),
])
def test_title_leaves_mixed_case_alone(value, expected):
    assert t2g._smart_title(value) == expected


def test_render_template_reports_the_missing_variable():
    assert t2g.render_template("{a} ({b})", {"a": "x", "b": 2.0}) == ("x (2)", None)
    assert t2g.render_template("{a} ({b})", {"a": "x"}) == (None, "b")
    assert t2g.render_template("{{literal}} {a}", {"a": "x"}) == ("{literal} x", None)


def test_optional_template_variable_drops_out_cleanly():
    assert t2g.render_template("{first} {last?} ({id})", {"first": "Srajana", "id": "000ZE0744"}) == (
        "Srajana (000ZE0744)", None,
    )
    assert t2g.template_vars("{first} {last?}") == ["first", "last"]


def test_placeholder_surname_becomes_a_null_via_null_values():
    mapping = json.loads(json.dumps(MAPPING))
    mapping["null_values"].append(".")
    person = mapping["entities"]["person"]
    person["name"] = "{first_name} {last_name?} ({talent_id})"
    person["properties"]["full_name"] = {"template": "{first_name} {last_name?}"}
    staged, _ = _build(rows=[_row("009", "., Srajana", "Kothari, Anubhav")], mapping=t2g.parse_mapping(mapping))
    srajana = _obs(staged, "Srajana (009)")
    assert srajana["full_name"] == "Srajana" and "last_name" not in srajana


# ── the commit path: explicit endpoints reach ASSERTS_RELATION ───────────────


class _Session:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **kw):
        self.calls.append((cypher, kw))
        return self


def test_relation_writer_uses_explicit_observation_ids_and_drops_foreign_ones():
    from artmind.ingest import _write_relation_observations

    observations = [
        {"id": "obs-a", "name": "Same Name", "chunk_id": "c1", "key": "a|PERSON|d"},
        {"id": "obs-b", "name": "Same Name", "chunk_id": "c1", "key": "b|PERSON|d"},
    ]
    relationships = [
        {"source_observation_id": "obs-b", "target_observation_id": "obs-a", "rel_type": "reports_to", "chunk_id": "c1"},
        {"source_observation_id": "obs-b", "target_observation_id": "not-in-this-doc", "rel_type": "reports_to", "chunk_id": "c1"},
    ]
    session = _Session()
    written = _write_relation_observations(session, relationships, {"id": "doc"}, observations)

    assert written == 1
    (cypher, params), = [(c, kw) for c, kw in session.calls if "ASSERTS_RELATION" in c]
    # Resolving by name would have picked obs-a for both ends (a self-loop).
    assert (params["src"], params["tgt"], params["rel_type"]) == ("obs-b", "obs-a", "REPORTS_TO")
    assert "source_observation_id" not in params["props"]


# ── the batched rebuild ──────────────────────────────────────────────────────


def test_batch_keys_splits_by_size_but_never_splits_a_same_as_group():
    keys = [(f"k{i:02d}", "PERSON", "d") for i in range(10)]
    group = [("k03", "PERSON", "d"), ("k08", "PERSON", "d")]
    batches = t2g.batch_keys(keys, [group], size=4)
    assert sorted(k for b in batches for k in b) == sorted(keys)
    home = [b for b in batches if group[0] in b]
    assert len(home) == 1 and group[1] in home[0]
    assert all(len(b) <= 4 for b in batches if group[0] not in b)


def test_rebuild_in_batches_commits_one_transaction_per_batch(monkeypatch):
    import artmind.graph_query as graph_query
    import artmind.projection as projection
    import artmind.same_as as same_as

    rebuilt: list[list] = []

    class Session:
        def execute_write(self, fn):
            return fn(object())

    class Ctx:
        def __enter__(self):
            return Session()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(graph_query, "neo4j_session", lambda: Ctx())
    monkeypatch.setattr(same_as, "load_groups", lambda: [])
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: rebuilt.append(list(keys)) or {"rebuilt": len(keys), "keys": len(keys)})
    monkeypatch.setattr(t2g, "REBUILD_BATCH", 3)

    keys = [(f"k{i}", "PERSON", "d") for i in range(7)]
    totals = t2g._rebuild_in_batches(keys)
    assert [len(b) for b in rebuilt] == [3, 3, 1]
    assert totals["rebuilt"] == 7 and totals["batches"] == 3


# ── CLI ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    import artmind.cli as cli
    import artmind.temporal as temporal
    import paths

    mappings = tmp_path / "table_mappings"
    mappings.mkdir()
    (mappings / "hr.yaml").write_text(yaml.safe_dump(MAPPING, sort_keys=False))
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", mappings)
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(cli, "_resolve_table_row", lambda name, domain: {**TABLE, "table_name": name})
    monkeypatch.setattr(t2g, "read_table_rows", lambda table, as_of=None: (ROWS, COLUMNS))
    monkeypatch.setattr(temporal, "load_schema", lambda domain: SCHEMA)
    return tmp_path


def test_cli_dry_run_reports_and_writes_nothing(cli_env, monkeypatch):
    import artmind.cli as cli
    import artmind.ingest as ingest

    monkeypatch.setattr(ingest, "_write_to_neo4j", lambda *a, **k: pytest.fail("dry run committed"))
    result = CliRunner().invoke(cli.cli, ["ingest", "table2graph", "hr_export_20260921", "--dryRun", "--compact"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output.strip().splitlines()[-1])["tables"][0]
    assert report["dry_run"] is True and report["observations"] == 13
    assert not (cli_env / "kg").exists()


def test_cli_commit_stages_then_commits_that_directory(cli_env, monkeypatch):
    import artmind.cli as cli
    import artmind.ingest as ingest

    committed = {}
    keys = [("pooja jain (001)", "PERSON", "perf")]

    def fake_write(directory, domain, defer_rebuild=False):
        committed["dir"], committed["domain"], committed["deferred"] = directory, domain, defer_rebuild
        return {"chunks": 6, "observations": 13, "relationships": 12, "deferred_keys": keys, "unembedded_chunk_ids": []}

    monkeypatch.setattr(ingest, "_write_to_neo4j", fake_write)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda k: committed.setdefault("rebuilt", k) and {"keys": len(k)})
    result = CliRunner().invoke(cli.cli, ["ingest", "table2graph", "hr_export_20260921", "--noEmbed", "--compact"])
    assert result.exit_code == 0, result.output
    assert committed["domain"] == "perf"
    assert committed["dir"] == cli_env / "kg" / "perf" / "table__hr_export_20260921"
    # Observations commit with the rebuild deferred; the deferred keys are then rebuilt.
    assert committed["deferred"] is True and committed["rebuilt"] == keys
    staged_obs = json.loads((committed["dir"] / "observations.json").read_text())
    assert len(staged_obs) == 13
    assert json.loads((committed["dir"] / "document.json").read_text())["id"] == "table:perf:hr_export_20260921"


def test_cli_errors_without_a_matching_mapping(cli_env):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["ingest", "table2graph", "sales_2026"])
    assert result.exit_code != 0
    assert "no table mapping matches 'sales_2026'" in result.output


def test_cli_invalid_mapping_names_every_error(cli_env, tmp_path):
    import artmind.cli as cli

    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({**MAPPING, "row_key": ["Nope"]}, sort_keys=False))
    result = CliRunner().invoke(cli.cli, ["ingest", "table2graph", "hr_export_20260921", "--mapping", str(bad), "--dryRun"])
    assert result.exit_code != 0
    assert "row_key: column 'Nope' is not in the table" in result.output


def test_table_to_graph_defer_rebuild_skips_its_own_rebuild_and_sweeps(tmp_path, monkeypatch):
    """§5 step 2 of the vault-sync spec: `defer_rebuild=True` must return the
    affected keys for the CALLER to batch-rebuild later, not rebuild/sweep
    internally like the ordinary (CLI) path does."""
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    mapping = t2g.parse_mapping(MAPPING)
    table = {"domain": "perf", "table_name": "hr_export_1", "id": 1}

    monkeypatch.setattr(t2g, "read_table_rows", lambda table, as_of=None: ([
        {"TalentID": "1", "Employee Name": "Doe, Jane", "Band": "B3", "Mgr?": "N",
         "FLM": "N/A", "Segment": "N/A", "Seg Date": "N/A", "Ute": "N/A"},
    ], COLUMNS))

    import artmind.ingest as ing
    fake_summary = {
        "chunks": 1, "observations": 1, "relationships": 0, "retracted": {},
        "deferred_keys": [("Jane Doe (1)", "PERSON", "perf")],
        "unembedded_chunk_ids": [],
    }
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda directory, domain, defer_rebuild=False: fake_summary,
    )
    rebuild_called = []
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys: rebuild_called.append(keys) or {})
    sweep_called = []
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: sweep_called.append(keys) or 0)

    report = t2g.table_to_graph(table, mapping, schema=SCHEMA, embed=True, defer_rebuild=True)

    assert rebuild_called == [], "the rebuild must be deferred to the caller"
    assert sweep_called == [], "the embed sweep must be deferred to the caller too"
    assert report["commit"]["deferred_keys"] == [("Jane Doe (1)", "PERSON", "perf")]
    assert report["commit"]["projection"] == {"deferred": True}


def test_table_to_graph_default_behaviour_still_rebuilds_immediately(tmp_path, monkeypatch):
    """Regression pin: `defer_rebuild=False` (the default) is byte-for-byte
    today's existing CLI behavior."""
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    mapping = t2g.parse_mapping(MAPPING)
    table = {"domain": "perf", "table_name": "hr_export_1", "id": 1}

    monkeypatch.setattr(t2g, "read_table_rows", lambda table, as_of=None: ([
        {"TalentID": "1", "Employee Name": "Doe, Jane", "Band": "B3", "Mgr?": "N",
         "FLM": "N/A", "Segment": "N/A", "Seg Date": "N/A", "Ute": "N/A"},
    ], COLUMNS))

    import artmind.ingest as ing
    fake_summary = {
        "chunks": 1, "observations": 1, "relationships": 0, "retracted": {},
        "deferred_keys": [("Jane Doe (1)", "PERSON", "perf")],
        "unembedded_chunk_ids": [],
    }
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda directory, domain, defer_rebuild=False: fake_summary,
    )
    rebuild_called = []
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys: rebuild_called.append(keys) or {"rebuilt": 1})

    report = t2g.table_to_graph(table, mapping, schema=SCHEMA, embed=False, defer_rebuild=False)

    assert rebuild_called == [[("Jane Doe (1)", "PERSON", "perf")]]
    assert "deferred_keys" not in report["commit"]
    assert report["commit"]["projection"] == {"rebuilt": 1}
