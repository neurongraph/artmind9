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


def test_pipeline_proposes_semantics_only_on_first_registration(tmp_path, monkeypatch):
    """Grain and bridge_role describe what the table means, which does not change
    when rows arrive — so the ingest hook must not pay for an LLM call on every
    replace refresh or SCD-2 batch, unlike propose_mappings which runs each time."""
    import csv

    import artmind.db as db
    import artmind.structured.semantics as semantics
    import paths
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()

    calls = []
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda table_id, model=None: calls.append(table_id) or {"grain": "instance"},
    )

    csv_path = tmp_path / "widgets.csv"

    def write_rows(rows):
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerows([["id", "name"], *rows])

    write_rows([[1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(csv_path, "banking")
    assert len(calls) == 1, "first registration should propose"

    # The content must actually change: ingest_structured_file short-circuits to
    # {"status": "skipped"} on an unchanged sha256 and never reaches _write_table,
    # so re-ingesting the identical file would prove nothing about the refresh path.
    write_rows([[1, "Widget"], [2, "Gadget"], [3, "Doohickey"]])
    result = ingest_structured_file(csv_path, "banking")

    from artmind.structured import registry

    assert result["status"] == "ok", result
    assert registry.get_table("widgets", domain="banking")["version"] == 2
    assert len(calls) == 1, "a refresh must not re-propose semantics"
