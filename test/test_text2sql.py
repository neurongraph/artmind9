import csv
import json

import pytest

from artmind import text2sql


FAKE_TABLES = [
    {
        "id": 1,
        "datasource": "local",
        "table_name": "products",
        "domain": "banking",
        "row_count": 2,
        "parquet_path": "/tmp/products.parquet",
    },
]

FAKE_COLUMNS_BY_TABLE = {
    1: [
        {"table_id": 1, "name": "id", "dtype": "BIGINT", "profile_json": None},
        {"table_id": 1, "name": "name", "dtype": "VARCHAR", "profile_json": None},
    ],
}

FAKE_MAPPINGS_BY_TABLE = {
    1: [
        {
            "table_id": 1,
            "column": "name",
            "entity_class": "PRODUCT",
            "confirmed": True,
            "confidence": 0.9,
        },
        {
            "table_id": 1,
            "column": "id",
            "entity_class": "SKU",
            "confirmed": False,
            "confidence": 0.4,
        },
    ],
}


def _patch_registry(monkeypatch, tables=FAKE_TABLES):
    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(structured_registry, "list_tables", lambda domains=None: tables)
    monkeypatch.setattr(
        structured_registry, "get_columns", lambda table_id: FAKE_COLUMNS_BY_TABLE.get(table_id, [])
    )
    monkeypatch.setattr(
        structured_registry, "list_mappings", lambda table_id: FAKE_MAPPINGS_BY_TABLE.get(table_id, [])
    )


def test_schema_summary_includes_table_column_and_mapping():
    summary = text2sql._schema_summary_sql(FAKE_TABLES, FAKE_COLUMNS_BY_TABLE, FAKE_MAPPINGS_BY_TABLE)

    assert "TABLE products" in summary
    assert "domain=banking" in summary
    assert "rows=2" in summary
    assert "id:BIGINT" in summary
    assert "name:VARCHAR" in summary
    # confirmed mapping shows up, unconfirmed one does not
    assert "PRODUCT" in summary
    assert "SKU" not in summary


def test_schema_summary_handles_no_tables():
    assert "no tables" in text2sql._schema_summary_sql([], {}, {})


def test_build_prompt_includes_schema_and_domains():
    schema_info = text2sql._schema_summary_sql(FAKE_TABLES, FAKE_COLUMNS_BY_TABLE, FAKE_MAPPINGS_BY_TABLE)

    prompt = text2sql.build_text2sql_prompt(
        question="How many products are there?",
        schema_info=schema_info,
        domains=["banking"],
    )

    assert "products" in prompt
    assert "banking" in prompt
    assert "How many products are there?" in prompt
    assert "READ-ONLY" in prompt


def test_validate_read_only_sql_accepts_select():
    text2sql.validate_read_only_sql("SELECT * FROM products")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO products VALUES (1, 'x')",
        "UPDATE products SET name = 'x'",
        "DELETE FROM products",
        "CREATE TABLE evil (id INT)",
        "DROP TABLE products",
        "ALTER TABLE products ADD COLUMN x INT",
        "TRUNCATE products",
        "COPY products TO 'out.csv'",
        "ATTACH 'evil.db' AS evil",
        "DETACH evil",
        "PRAGMA database_list",
        "CALL some_proc()",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET threads=4",
        "EXPORT DATABASE 'x'",
        "CHECKPOINT",
    ],
)
def test_validate_read_only_sql_rejects_write_operations(sql):
    with pytest.raises(ValueError, match="write"):
        text2sql.validate_read_only_sql(sql)


def test_generate_sql_returns_sql_and_notes(monkeypatch):
    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "SELECT count(*) AS n FROM products", "notes": "counts rows"})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    result = text2sql.generate_sql("How many products?", "banking", model="test-model")

    assert result["sql"] == "SELECT count(*) AS n FROM products"
    assert result["notes"] == "counts rows"


def test_generate_sql_rejects_write_sql(monkeypatch):
    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "DELETE FROM products", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    with pytest.raises(ValueError, match="write"):
        text2sql.generate_sql("Delete everything", "banking", model="test-model")


def test_generate_sql_raises_on_empty_sql(monkeypatch):
    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    with pytest.raises(ValueError, match="did not return"):
        text2sql.generate_sql("Something", "banking", model="test-model")


def test_execute_text2sql_dry_run_skips_execution(monkeypatch):
    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "SELECT count(*) AS n FROM products", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    class _ExplodingDatasource:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DuckDBDatasource should not be constructed in dry-run mode")

    monkeypatch.setattr(text2sql, "DuckDBDatasource", _ExplodingDatasource)

    result = text2sql.execute_text2sql("How many products?", "banking", model="test-model", dry_run=True)

    assert result["dry_run"] is True
    assert result["rows"] == []
    assert result["command"] == "text2sql"
    assert result["query_type"] == "sql"
    assert "SELECT" in result["generated_sql"]


def test_execute_text2sql_runs_query(monkeypatch):
    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "SELECT count(*) AS n FROM products", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    class _FakeDatasource:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def in_memory(cls):
            return cls()

        def ensure_views(self, tables):
            pass

        def run_sql(self, sql):
            return [{"n": 2}]

    monkeypatch.setattr(text2sql, "DuckDBDatasource", _FakeDatasource)

    result = text2sql.execute_text2sql("How many products?", "banking", model="test-model")

    assert result["rows"] == [{"n": 2}]
    assert result["query_type"] == "sql"
    assert result["command"] == "text2sql"


def test_execute_text2sql_wraps_duckdb_error_with_sql(monkeypatch):
    import duckdb

    _patch_registry(monkeypatch)
    llm_response = json.dumps({"sql": "SELECT * FROM nope", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    class _FakeDatasource:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def in_memory(cls):
            return cls()

        def ensure_views(self, tables):
            pass

        def run_sql(self, sql):
            raise duckdb.CatalogException("Table with name nope does not exist!")

    monkeypatch.setattr(text2sql, "DuckDBDatasource", _FakeDatasource)

    with pytest.raises(ValueError, match="SELECT \\* FROM nope"):
        text2sql.execute_text2sql("bogus question", "banking", model="test-model")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def _ingest_domain_table(tmp_path, domain, table_name, rows):
    from artmind.structured.pipeline import ingest_structured_file

    csv_path = tmp_path / f"{table_name}.csv"
    _write_csv(csv_path, rows)
    result = ingest_structured_file(csv_path, domain, table=table_name)
    assert result["status"] == "ok"


def test_execute_text2sql_cannot_see_out_of_domain_table(tmp_path, monkeypatch):
    """Cross-domain isolation: text2sql scoped to ``banking`` must not be able
    to read a table that only exists in ``retail``, even though both tables
    are ingested into the same shared, persistent DuckDB catalog file."""
    pytest.importorskip("openpyxl")
    _patch_stores(tmp_path, monkeypatch)

    _ingest_domain_table(
        tmp_path, "banking", "banking_table", [["id", "name"], [1, "Checking"]]
    )
    _ingest_domain_table(
        tmp_path, "retail", "retail_table", [["id", "name"], [1, "Widget"]]
    )

    # A misbehaving/malicious LLM response naming the out-of-domain table.
    llm_response = json.dumps({"sql": "SELECT * FROM retail_table", "notes": ""})
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    with pytest.raises(ValueError, match="Generated SQL failed"):
        text2sql.execute_text2sql(
            "Show me the retail table", domains=["banking"], model="test-model"
        )


def test_execute_text2sql_in_domain_table_still_works(tmp_path, monkeypatch):
    """Regression check: the isolation fix doesn't break the legitimate,
    in-domain path — a domain-scoped query against its own table still
    executes and returns real rows."""
    pytest.importorskip("openpyxl")
    _patch_stores(tmp_path, monkeypatch)

    _ingest_domain_table(
        tmp_path, "banking", "banking_table", [["id", "name"], [1, "Checking"]]
    )
    _ingest_domain_table(
        tmp_path, "retail", "retail_table", [["id", "name"], [1, "Widget"]]
    )

    llm_response = json.dumps(
        {"sql": "SELECT id, name FROM banking_table", "notes": ""}
    )
    monkeypatch.setattr(text2sql, "call_llm", lambda model, prompt: llm_response)

    result = text2sql.execute_text2sql(
        "Show me the banking table", domains=["banking"], model="test-model"
    )

    assert result["rows"] == [{"id": 1, "name": "Checking"}]
