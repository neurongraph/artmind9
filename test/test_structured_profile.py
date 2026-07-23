import csv

import pytest

duckdb = pytest.importorskip("duckdb")


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def _build_datasource(tmp_path, monkeypatch, rows):
    """Load a small csv (categorical, numeric, and high-cardinality id columns)
    into a fresh DuckDBDatasource and return it plus the table name."""
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    csv_path = tmp_path / "widgets.csv"
    _write_csv(csv_path, rows)

    ds = DuckDBDatasource(db_path=tmp_path / "profile.duckdb")
    ds.load_table(csv_path, "widgets", "banking")
    return ds


def _rows(n):
    # "id" is a string so it is never dtype-inferred as numeric — its high
    # cardinality is what routes it to the "other" branch.
    header = ["id", "category", "amount"]
    body = [
        [f"ID{i:05d}", "red" if i % 3 == 0 else ("green" if i % 3 == 1 else "blue"), i * 1.5]
        for i in range(1, n + 1)
    ]
    return [header, *body]


def test_profile_column_numeric(tmp_path, monkeypatch):
    from artmind.structured.profile import profile_column

    ds = _build_datasource(tmp_path, monkeypatch, _rows(10))
    profile = profile_column(ds.con, "widgets", "amount", "DOUBLE")

    assert profile.kind == "numeric"
    assert profile.minimum == 1.5
    assert profile.maximum == 15.0
    assert profile.null_rate == 0.0


def test_profile_column_categorical(tmp_path, monkeypatch):
    from artmind.structured.profile import profile_column

    ds = _build_datasource(tmp_path, monkeypatch, _rows(10))
    profile = profile_column(ds.con, "widgets", "category", "VARCHAR")

    assert profile.kind == "categorical"
    assert profile.cardinality == 3
    assert set(profile.distinct_sample) <= {"red", "green", "blue"}
    assert profile.null_rate == 0.0


def test_profile_column_high_cardinality_is_other(tmp_path, monkeypatch):
    from artmind.structured.profile import profile_column

    ds = _build_datasource(tmp_path, monkeypatch, _rows(10))
    # "id" is unique per row -> high cardinality relative to a small categorical_max
    profile = profile_column(ds.con, "widgets", "id", "VARCHAR", categorical_max=3)

    assert profile.kind == "other"
    assert profile.distinct_sample == []
    assert profile.cardinality == 10


def test_profile_column_null_rate(tmp_path, monkeypatch):
    from artmind.structured.profile import profile_column

    rows = [["id", "category", "amount"], [1, "red", 1.0], [2, None, 2.0], [3, "red", None]]
    ds = _build_datasource(tmp_path, monkeypatch, rows)

    profile = profile_column(ds.con, "widgets", "category", "VARCHAR")
    assert profile.kind == "categorical"
    assert profile.null_rate == pytest.approx(1 / 3)


def test_datasource_profile_columns_returns_profile_per_column(tmp_path, monkeypatch):
    # 250 unique ids exceeds the default categorical_max=200, so "id" profiles as "other".
    ds = _build_datasource(tmp_path, monkeypatch, _rows(250))

    profiles = ds.profile_columns("widgets")

    assert set(profiles) == {"id", "category", "amount"}
    assert profiles["amount"].kind == "numeric"
    assert profiles["category"].kind == "categorical"
    assert profiles["id"].kind == "other"
    assert profiles["id"].distinct_sample == []
    assert profiles["id"].cardinality == 250
