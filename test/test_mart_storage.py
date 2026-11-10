import duckdb

import paths
from artmind.mart import storage


def test_write_tables_creates_parquet_and_registers(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path)
    registered = []
    monkeypatch.setattr(storage, "_register", lambda domain, name, path, rows, columns: registered.append((name, rows)))
    out = storage.write_tables("j", {"fact_happening": [{"entity_key": "e1", "date": "2025-01-01",
                                                         "observation_ids": ["o1"]}],
                                     "dim_type": []})
    p = tmp_path / "j" / "mart_fact_happening.parquet"
    assert p.is_file() and out["fact_happening"] == 1
    con = duckdb.connect()
    assert con.execute(f"SELECT entity_key, observation_ids FROM read_parquet('{p}')").fetchall() == [("e1", ["o1"])]
    assert ("mart_fact_happening", 1) in registered and ("mart_dim_type", 0) in registered
    assert not list((tmp_path / "j").glob("*.mart-tmp"))
