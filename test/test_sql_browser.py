"""text2sql delegated to sql-browser (artmind/sql_browser.py; spec 2026-11-07 §8)."""
import json
from types import SimpleNamespace

import duckdb
import pytest
import yaml

import paths
from artmind import sql_browser, text2sql


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path / "meta")
    return tmp_path


def _parquet(path, rows):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (id INTEGER, status VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?, ?)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")


def test_engine_values(monkeypatch):
    monkeypatch.setenv("ARTMIND_TEXT2SQL_ENGINE", "Auto")
    assert sql_browser.engine() == "auto"
    monkeypatch.setenv("ARTMIND_TEXT2SQL_ENGINE", "nope")
    with pytest.raises(ValueError, match="ARTMIND_TEXT2SQL_ENGINE"):
        sql_browser.engine()


def test_scoped_db_holds_only_the_listed_tables(work):
    p = work / "a.parquet"
    _parquet(p, [(1, "Open")])
    db = sql_browser.scoped_db([{"domain": "bank", "table_name": "complaints", "parquet_path": str(p), "version": 1}])
    con = duckdb.connect(str(db), read_only=True)
    names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "bank__complaints" in names
    assert con.execute("SELECT count(*) FROM bank__complaints").fetchone()[0] == 1


def test_metadata_merges_authored_over_generated(work):
    d = work / "meta"
    d.mkdir()
    (d / "bank.meta.yaml").write_text(yaml.safe_dump({"rules": ["authored"], "tables": {"x": {"description": "A"}}}))
    (d / "bank.mart.meta.yaml").write_text(yaml.safe_dump({"rules": ["generated"], "tables": {"x": {"description": "G"},
                                                                                             "y": {"description": "Y"}}}))
    merged = yaml.safe_load(sql_browser.metadata_for(["bank"]).read_text())
    assert merged["rules"] == ["authored", "generated"]
    assert merged["tables"]["x"]["description"] == "A" and merged["tables"]["y"]["description"] == "Y"
    assert sql_browser.metadata_for(["other"]) is None


def test_llm_env_maps_ibm_ica_to_openai(monkeypatch):
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica", "ANTHROPIC_AUTH_TOKEN": "tok",
           "ARTMIND_KG_ANTHROPIC_BASE_URL": "https://gw.example/v1"}
    assert sql_browser.llm_env(env) == {"SQL_BROWSER_LLM_PROVIDER": "openai",
                                        "SQL_BROWSER_LLM_BASE_URL": "https://gw.example/v1",
                                        "SQL_BROWSER_LLM_API_KEY": "tok"}
    assert sql_browser.llm_env({"ARTMIND_KG_LLM_PROVIDER": "ollama"}) == {"SQL_BROWSER_LLM_PROVIDER": "ollama"}


def test_run_builds_the_command_and_passes_secrets_by_env(work, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"sql": "SELECT 1", "rows": [{"x": 1}]}), stderr="")
    monkeypatch.setattr(sql_browser, "executable", lambda: "/bin/sql-browser")
    monkeypatch.setattr(sql_browser.subprocess, "run", fake_run)
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica", "ANTHROPIC_AUTH_TOKEN": "secret", "ARTMIND_KG_ANTHROPIC_BASE_URL": "u"}
    out = sql_browser.run("q", work / "db.duckdb", None, "m", env, dry_run=True)
    assert out["sql"] == "SELECT 1"
    assert seen["cmd"] == ["/bin/sql-browser", "query", "q", str(work / "db.duckdb"), "--model", "m", "--json",
                           "--no-metadata", "--dry-run"]
    assert "secret" not in " ".join(seen["cmd"]) and seen["env"]["SQL_BROWSER_LLM_API_KEY"] == "secret"


def test_run_raises_on_error_payload(work, monkeypatch):
    monkeypatch.setattr(sql_browser, "executable", lambda: "/bin/sql-browser")
    monkeypatch.setattr(sql_browser.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout=json.dumps({"sql": "", "rows": [], "error": "no such column"}), stderr=""))
    with pytest.raises(sql_browser.SqlBrowserError, match="no such column"):
        sql_browser.run("q", work / "db", None, "m", {})


TABLES = [{"id": 1, "domain": "bank", "table_name": "complaints", "parquet_path": "/x.parquet", "version": 1}]


def _stub_store(monkeypatch):
    monkeypatch.setattr(text2sql.structured_registry, "list_tables", lambda domains=None, **k: TABLES)
    monkeypatch.setattr(text2sql, "_fusion_hints", lambda domains: {})
    monkeypatch.setattr(sql_browser, "scoped_db", lambda tables: "/tmp/scope.duckdb")
    monkeypatch.setattr(sql_browser, "metadata_for", lambda domains: None)


def test_text2sql_delegates_when_engine_is_sql_browser(monkeypatch):
    monkeypatch.setenv("ARTMIND_TEXT2SQL_ENGINE", "sql-browser")
    _stub_store(monkeypatch)
    monkeypatch.setattr(sql_browser, "run", lambda *a, **k: {"sql": "SELECT * FROM bank__complaints",
                                                            "rows": [{"id": 1}], "notes": "n",
                                                            "corrections": [], "warnings": ["w"]})
    out = text2sql.execute_text2sql("how many", ["bank"])
    assert out["engine"] == "sql-browser" and out["rows"] == [{"id": 1}] and out["warnings"] == ["w"]
    assert out["generated_sql"].startswith("SELECT") and out["command"] == "text2sql"


def test_auto_falls_back_to_builtin_with_a_warning(monkeypatch):
    monkeypatch.setenv("ARTMIND_TEXT2SQL_ENGINE", "auto")
    _stub_store(monkeypatch)
    monkeypatch.setattr(sql_browser, "executable", lambda: "/bin/sql-browser")

    def boom(*a, **k):
        raise sql_browser.SqlBrowserError("timed out")
    monkeypatch.setattr(sql_browser, "run", boom)
    monkeypatch.setattr(text2sql, "generate_sql", lambda *a, **k: {"sql": "SELECT 1", "notes": ""})
    out = text2sql.execute_text2sql("how many", ["bank"], dry_run=True)
    assert out["engine"] == "builtin" and "timed out" in out["warnings"][0]


def test_explicit_sql_browser_failure_raises(monkeypatch):
    monkeypatch.setenv("ARTMIND_TEXT2SQL_ENGINE", "sql-browser")
    _stub_store(monkeypatch)
    monkeypatch.setattr(sql_browser, "run", lambda *a, **k: (_ for _ in ()).throw(sql_browser.SqlBrowserError("x")))
    with pytest.raises(ValueError, match="sql-browser failed"):
        text2sql.execute_text2sql("q", ["bank"])
