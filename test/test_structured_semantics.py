"""Grain / bridge-column proposal — prompt shape, persistence, and guards.

The LLM is always stubbed: this suite stays hermetic like the rest (see
test/conftest.py), and the point under test is what we do with a response, not
what a model returns.
"""

import json

import pytest

pytest.importorskip("openpyxl")


def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()


def _seed_table(grain="instance", refresh_mode="replace"):
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "vulnerable_customers", "banking",
        parquet_path="/tmp/v.parquet", row_count=7, grain=grain,
        refresh_mode=refresh_mode,
        business_key="customer_id" if refresh_mode == "temporal" else None,
    )
    registry.replace_columns(table_id, [
        {"name": "customer_id", "dtype": "VARCHAR",
         "profile_json": json.dumps({"kind": "categorical", "distinct_sample": ["CUST-0019"]})},
        {"name": "vulnerability_driver", "dtype": "VARCHAR",
         "profile_json": json.dumps({"kind": "categorical",
                                     "distinct_sample": ["Life Events", "Bereavement"]})},
        {"name": "support_needed", "dtype": "VARCHAR", "profile_json": None},
    ])
    return table_id


def _stub_llm(monkeypatch, payload):
    """Stub the LLM at the point semantics.py imports it, and capture the prompt."""
    import artmind.extraction as extraction

    seen = {}

    def fake_call_llm(model, prompt):
        seen["model"] = model
        seen["prompt"] = prompt
        return json.dumps(payload)

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    return seen


_MAPPING_SCHEMA_ENTITIES_PROMPT = """Some preamble text a real schema file would have here.

ENTITY TYPES YOU MUST EXTRACT:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRODUCT
  A banking product a customer holds, such as a savings account or credit card.
  example type values: savings_account | credit_card

BRANCH
  A physical bank branch location.
  example type values: branch
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTRACTION RULES:
Some trailing rules text a real schema file would have here.
"""


def _stub_schema(monkeypatch, entities_prompt=_MAPPING_SCHEMA_ENTITIES_PROMPT):
    """Stub the schema lookup at the point semantics.py imports it -- same
    lazy-import-patching approach as _stub_llm."""
    import artmind.temporal as temporal

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {"entity_types": ["PRODUCT", "BRANCH"], "entities_prompt": entities_prompt},
    )


def test_mapping_prompt_includes_schema_classes_and_columns(tmp_path, monkeypatch):
    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    seen = _stub_llm(monkeypatch, {"mappings": []})

    semantics.propose_mapping(table_id, "banking")

    prompt = seen["prompt"]
    assert "vulnerable_customers" in prompt
    assert "PRODUCT" in prompt and "savings account or credit card" in prompt
    assert "BRANCH" in prompt
    # No live-KG dependency: nothing about entity_listing/graph names in the prompt.
    assert "vulnerability_driver" in prompt  # still carries column samples


def test_mapping_persists_proposals_above_floor(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == [
        {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9}
    ]
    persisted = registry.list_mappings(table_id)
    assert len(persisted) == 1
    assert persisted[0]["column"] == "vulnerability_driver"
    assert persisted[0]["entity_class"] == "PRODUCT"
    assert persisted[0]["confirmed"] == 0


def test_mapping_below_floor_unmapped(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [{"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.1}],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == []
    assert registry.list_mappings(table_id) == []


def test_mapping_ignores_hallucinated_column_or_class(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "not_a_real_column", "entity_class": "PRODUCT", "confidence": 0.99},
            {"column": "vulnerability_driver", "entity_class": "NOT_A_REAL_CLASS", "confidence": 0.99},
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.7},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == [
        {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.7}
    ]


def test_mapping_does_not_overwrite_confirmed(tmp_path, monkeypatch):
    """Same guarantee propose_semantics gives to bridge columns: a re-proposal must
    never silently un-confirm an operator's review."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.upsert_mapping(table_id, "vulnerability_driver", "PRODUCT", 1.0, confirmed=True)
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [{"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.3}],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == []
    row = registry.list_mappings(table_id)[0]
    assert row["confirmed"] == 1
    assert row["confidence"] == 1.0


def test_mapping_only_columns_restricts_persistence(tmp_path, monkeypatch):
    """The replace-refresh new-column trigger (a later task) needs to classify
    only genuinely new columns, leaving an existing (already-classified-or-not)
    column's mapping state untouched even if the model proposes for it too."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9},
            {"column": "support_needed", "entity_class": "BRANCH", "confidence": 0.9},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking", only_columns={"support_needed"})

    assert proposals == [{"column": "support_needed", "entity_class": "BRANCH", "confidence": 0.9}]
    persisted_columns = {m["column"] for m in registry.list_mappings(table_id)}
    assert persisted_columns == {"support_needed"}


def test_mapping_fails_clearly_when_domain_has_no_schema(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    import artmind.temporal as temporal

    monkeypatch.setattr(temporal, "load_schema", lambda domain: {})

    with pytest.raises(ValueError, match="no schema file"):
        semantics.propose_mapping(table_id, "banking")


def test_mapping_no_entity_classes_in_schema_returns_empty_not_error(tmp_path, monkeypatch):
    """A schema file that exists but whose entities_prompt has no parseable
    classes is a valid (if unusual) state -- distinct from no schema at all."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    import artmind.temporal as temporal

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {"entity_types": [], "entities_prompt": "no banner here at all"},
    )

    assert semantics.propose_mapping(table_id, "banking") == []
    assert registry.list_mappings(table_id) == []


def test_prompt_includes_columns_dtypes_and_samples(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    seen = _stub_llm(monkeypatch, {"grain": "instance", "bridge_columns": []})

    semantics.propose_semantics(table_id)

    prompt = seen["prompt"]
    assert "vulnerable_customers" in prompt
    assert "vulnerability_driver (VARCHAR)" in prompt
    assert "Bereavement" in prompt
    # A column with no profile still has to appear, or the model can't rule on it.
    assert "support_needed" in prompt
    assert registry.get_table_by_id(table_id) is not None


def test_persists_grain_and_bridge_columns(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_llm(monkeypatch, {
        "grain": "instance",
        "grain_reason": "records about particular customers",
        "bridge_columns": [
            {"column": "vulnerability_driver", "confidence": 0.9},
            {"column": "support_needed", "confidence": 0.8},
        ],
    })

    result = semantics.propose_semantics(table_id)

    assert result["grain"] == "instance"
    assert result["grain_written"] is True
    roles = registry.list_column_roles(table_id)
    assert [r["column"] for r in roles] == ["support_needed", "vulnerability_driver"]
    assert all(r["bridge_role"] == "term" and r["confirmed"] == 0 for r in roles)


def test_ignores_hallucinated_column_and_low_confidence(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_llm(monkeypatch, {
        "grain": "instance",
        "bridge_columns": [
            {"column": "not_a_real_column", "confidence": 0.99},
            {"column": "customer_id", "confidence": 0.1},
            {"column": "vulnerability_driver", "confidence": 0.7},
        ],
    })

    semantics.propose_semantics(table_id)

    assert [r["column"] for r in registry.list_column_roles(table_id)] == ["vulnerability_driver"]


def test_does_not_overwrite_confirmed_grain_or_roles(tmp_path, monkeypatch):
    """Same guarantee propose_mappings gives: a re-proposal must never silently
    un-confirm an operator's review."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.set_grain(table_id, "lookup", confirmed=True)
    registry.upsert_column_role(table_id, "vulnerability_driver", "term", 1.0, confirmed=True)

    _stub_llm(monkeypatch, {
        "grain": "normative",
        "bridge_columns": [{"column": "vulnerability_driver", "confidence": 0.2}],
    })
    result = semantics.propose_semantics(table_id)

    assert result["grain_written"] is False
    row = registry.get_table_by_id(table_id)
    assert row["grain"] == "lookup"
    assert row["grain_confirmed"] == 1
    role = registry.list_column_roles(table_id)[0]
    assert role["confirmed"] == 1
    assert role["confidence"] == 1.0


def test_unparseable_grain_leaves_existing_value(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_llm(monkeypatch, {"grain": "something_invented", "bridge_columns": []})

    result = semantics.propose_semantics(table_id)

    assert result["grain"] == "instance"
    assert result["grain_written"] is False
    assert registry.get_table_by_id(table_id)["grain"] == "instance"


def test_confirming_normative_requires_temporal_refresh_mode(tmp_path, monkeypatch):
    """Normative rows get superseded, so they need SCD-2 history — a replace-mode
    table would overwrite the old rule leaving no record it ever applied."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table(refresh_mode="replace")

    with pytest.raises(ValueError, match="requires refresh_mode 'temporal'"):
        registry.set_grain(table_id, "normative", confirmed=True)

    # A *proposal* of normative is allowed through — that is the signal worth
    # surfacing for review, not something to suppress.
    registry.set_grain(table_id, "normative", confirmed=False)
    assert registry.get_table_by_id(table_id)["grain"] == "normative"


def test_confirming_normative_allowed_on_temporal_table(tmp_path, monkeypatch):
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table(refresh_mode="temporal")

    registry.set_grain(table_id, "normative", confirmed=True)
    row = registry.get_table_by_id(table_id)
    assert row["grain"] == "normative"
    assert row["grain_confirmed"] == 1


def test_pipeline_proposes_all_three_steps_only_on_first_registration(tmp_path, monkeypatch):
    """First registration auto-runs grain+bridge+mapping once; a same-column
    replace refresh must not re-propose anything (mapping used to run
    unconditionally on every write before this design — that's the regression
    this guards against)."""
    import csv

    import artmind.db as db
    import paths
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()

    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append((table_id, domain, kw)) or {
            "table": "widgets", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    csv_path = tmp_path / "widgets.csv"

    def write_rows(rows):
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerows([["id", "name"], *rows])

    write_rows([[1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(csv_path, "banking")
    assert len(calls) == 1, "first registration should classify"
    assert set(calls[0][2]["steps"]) == {"grain", "bridge", "mapping"}

    # Same columns, different content -- must not re-propose anything (no new
    # columns, replace-mode, version > 1).
    write_rows([[1, "Widget"], [2, "Gadget"], [3, "Doohickey"]])
    result = ingest_structured_file(csv_path, "banking")

    from artmind.structured import registry

    assert result["status"] == "ok", result
    assert registry.get_table("widgets", domain="banking")["version"] == 2
    assert len(calls) == 1, "a same-column refresh must not re-classify"


def test_pipeline_replace_refresh_new_column_triggers_bridge_and_mapping_for_new_column_only(
    tmp_path, monkeypatch
):
    """A replace-mode refresh that adds a genuinely new column proposes
    bridge/mapping for that column only -- grain stays untouched, and existing
    columns' mapping/bridge state is untouched too."""
    import csv

    import artmind.db as db
    import paths
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()

    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(kw) or {
            "table": "widgets", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    csv_path = tmp_path / "widgets.csv"
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows([["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")
    assert len(calls) == 1  # first-registration run

    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows([["id", "name", "category"], [1, "Widget", "Tools"]])
    ingest_structured_file(csv_path, "banking", force=True)

    assert len(calls) == 2
    new_column_call = calls[1]
    assert set(new_column_call["steps"]) == {"bridge", "mapping"}
    assert new_column_call["only_columns"] == {"category"}
    assert new_column_call["redo"] is True


def test_propose_semantics_write_grain_false_suppresses_persistence(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table(grain="instance")
    _stub_llm(monkeypatch, {"grain": "lookup", "bridge_columns": []})

    result = semantics.propose_semantics(table_id, write_grain=False)

    assert result["grain_written"] is False
    assert result["grain"] == "instance"  # unchanged -- not asked to act on it this run
    assert registry.get_table_by_id(table_id)["grain"] == "instance"


def test_propose_semantics_only_columns_restricts_bridge_persistence(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_llm(monkeypatch, {
        "grain": "instance",
        "bridge_columns": [
            {"column": "vulnerability_driver", "confidence": 0.9},
            {"column": "support_needed", "confidence": 0.9},
        ],
    })

    semantics.propose_semantics(table_id, only_columns={"support_needed"})

    assert [r["column"] for r in registry.list_column_roles(table_id)] == ["support_needed"]


def test_propose_table_semantics_runs_all_three_steps_by_default(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "grain": "instance", "grain_reason": "records", "bridge_columns": [],
        "mappings": [],
    })

    result = semantics.propose_table_semantics(table_id, "banking")

    row = registry.get_table_by_id(table_id)
    assert row["grain_status"] == "ok"
    assert row["bridge_status"] == "ok"
    assert row["mapping_status"] == "ok"
    assert result["grain_status"] == "ok"
    assert result["mapping_status"] == "ok"


def test_propose_table_semantics_resumes_only_failed_step(tmp_path, monkeypatch):
    """Mirrors kg_chunk_status's resumability: a relationships-step failure
    doesn't force re-running an already-ok entities step."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.set_step_status(table_id, "grain", "ok")
    registry.set_step_status(table_id, "bridge", "ok")
    registry.set_step_status(table_id, "mapping", "failed")

    calls = {"semantics": 0, "mapping": 0}
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: calls.__setitem__("semantics", calls["semantics"] + 1) or {"grain": "instance", "bridge_columns": []},
    )
    monkeypatch.setattr(
        semantics, "propose_mapping",
        lambda *a, **k: calls.__setitem__("mapping", calls["mapping"] + 1) or [],
    )

    semantics.propose_table_semantics(table_id, "banking")

    assert calls == {"semantics": 0, "mapping": 1}
    assert registry.get_table_by_id(table_id)["mapping_status"] == "ok"


def test_propose_table_semantics_step_flag_targets_one_step(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()

    calls = {"semantics": 0, "mapping": 0}
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: calls.__setitem__("semantics", calls["semantics"] + 1) or {"grain": "instance", "bridge_columns": []},
    )
    monkeypatch.setattr(
        semantics, "propose_mapping",
        lambda *a, **k: calls.__setitem__("mapping", calls["mapping"] + 1) or [],
    )

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"])

    assert calls == {"semantics": 0, "mapping": 1}
    assert registry.get_table_by_id(table_id)["grain_status"] == "pending"


def test_propose_table_semantics_redo_reruns_ok_step(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.set_step_status(table_id, "mapping", "ok")

    calls = []
    monkeypatch.setattr(semantics, "propose_mapping", lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: {"grain": "instance", "bridge_columns": []},
    )

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"])
    assert calls == []  # already ok, no redo -> skipped

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"], redo=True)
    assert calls == [1]


def test_propose_table_semantics_records_failed_step_without_raising(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()

    def _boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(semantics, "propose_mapping", _boom)
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: {"grain": "instance", "bridge_columns": []},
    )

    result = semantics.propose_table_semantics(table_id, "banking")  # must not raise

    assert result["mapping_status"] == "failed"
    assert "mapping_error" in result
    assert registry.get_table_by_id(table_id)["mapping_status"] == "failed"
    # grain/bridge succeeded independently of mapping's failure.
    assert registry.get_table_by_id(table_id)["grain_status"] == "ok"


def test_propose_table_semantics_unknown_table_raises(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="no registered table"):
        semantics.propose_table_semantics(99999, "banking")
