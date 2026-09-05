"""project_catalogue(domain): wipe + re-project a domain's catalogue subgraph
(:Table, :TableColumn, :EntityClass) from the SQLite registry into Neo4j.

Hermetic — no live Neo4j. Uses the same fake-session-recording-calls pattern
as test_supersession.py, extended to actually record ``run(cypher, **params)``
calls (rather than just stub a return value) so we can assert on the wipe
query, the Table MERGE, the HAS_COLUMN edges, and the MAPS_TO_CLASS edges
(projected for proposed and confirmed mappings alike, flagged via
``m.confirmed``).
"""

import json

import artmind.structured.catalogue as catalogue


def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    return db


class _Result:
    def data(self):
        return []


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _seed(tmp_path, monkeypatch, *, confirmed_mapping=True):
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")
    table_id = registry.register_table(
        "default",
        "customers",
        "banking",
        parquet_path="/tmp/structured/banking/customers.parquet",
        row_count=42,
    )
    registry.replace_columns(
        table_id,
        [
            {
                "name": "customer_name",
                "dtype": "VARCHAR",
                "profile_json": json.dumps({"n_distinct": 10}),
            },
            {
                "name": "balance",
                "dtype": "DOUBLE",
                "profile_json": json.dumps({"min": 0}),
            },
        ],
    )
    registry.upsert_mapping(
        table_id, "customer_name", "Customer", 0.9, confirmed=confirmed_mapping
    )
    return table_id


def test_project_catalogue_wipes_then_merges_table_columns_and_confirmed_mapping(
    tmp_path, monkeypatch
):
    _seed(tmp_path, monkeypatch)

    fake = FakeSession()
    monkeypatch.setattr(catalogue, "neo4j_session", lambda **kw: fake)

    result = catalogue.project_catalogue("banking")

    assert result == {"tables": 1, "columns": 2, "mappings": 1}

    wipe_calls = [(c, p) for c, p in fake.calls if "DETACH DELETE" in c]
    assert len(wipe_calls) == 1
    wipe_cypher, wipe_params = wipe_calls[0]
    assert "t._domain = $domain" in wipe_cypher
    assert "t._domain STARTS WITH $domain" in wipe_cypher
    assert "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:TableColumn)" in wipe_cypher
    assert wipe_params["domain"] == "banking"

    table_calls = [(c, p) for c, p in fake.calls if "MERGE (t:Table" in c]
    assert len(table_calls) == 1
    _, table_params = table_calls[0]
    assert table_params["key"] == "default::banking::customers"

    column_calls = [c for c, _ in fake.calls if "MERGE (c:TableColumn" in c]
    assert len(column_calls) == 2
    assert all("HAS_COLUMN" in c for c in column_calls)

    mapping_calls = [(c, p) for c, p in fake.calls if "MAPS_TO_CLASS" in c]
    assert len(mapping_calls) == 1
    _, mapping_params = mapping_calls[0]
    assert mapping_params["eckey"] == "banking::Customer"


def test_project_catalogue_rolls_up_sub_domain_tables(tmp_path, monkeypatch):
    """registry.list_tables() rolls sub-domains up into the parent — the same
    convention as graph_query.domain_predicate() and every `query graph *`
    command. project_catalogue("banking") must pick up a "banking.retail"
    table too, and the wipe query must share that rollup scope (so a table
    later removed from "banking.retail" still gets its stale node swept by a
    "banking" rebuild)."""
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")
    parent_id = registry.register_table(
        "default",
        "customers",
        "banking",
        parquet_path="/tmp/structured/banking/customers.parquet",
        row_count=5,
    )
    registry.replace_columns(
        parent_id,
        [{"name": "customer_name", "dtype": "VARCHAR", "profile_json": "{}"}],
    )
    sub_id = registry.register_table(
        "default",
        "loans",
        "banking.retail",
        parquet_path="/tmp/structured/banking/retail/loans.parquet",
        row_count=7,
    )
    registry.replace_columns(
        sub_id,
        [{"name": "loan_officer", "dtype": "VARCHAR", "profile_json": "{}"}],
    )
    registry.upsert_mapping(sub_id, "loan_officer", "Employee", 0.8, confirmed=True)

    fake = FakeSession()
    monkeypatch.setattr(catalogue, "neo4j_session", lambda **kw: fake)

    result = catalogue.project_catalogue("banking")

    assert result == {"tables": 2, "columns": 2, "mappings": 1}

    table_calls = [(c, p) for c, p in fake.calls if "MERGE (t:Table" in c]
    assert {p["key"] for _, p in table_calls} == {
        "default::banking::customers",
        "default::banking.retail::loans",
    }

    mapping_calls = [(c, p) for c, p in fake.calls if "MAPS_TO_CLASS" in c]
    assert len(mapping_calls) == 1
    _, mapping_params = mapping_calls[0]
    # Scoped to the table's own domain ("banking.retail"), not the broader
    # "banking" argument project_catalogue was called with.
    assert mapping_params["eckey"] == "banking.retail::Employee"
    assert mapping_params["domain"] == "banking.retail"

    wipe_cypher, wipe_params = next((c, p) for c, p in fake.calls if "DETACH DELETE" in c)
    assert "t._domain = $domain" in wipe_cypher
    assert "t._domain STARTS WITH $domain" in wipe_cypher
    assert wipe_params["domain"] == "banking"


def test_project_catalogue_projects_unconfirmed_mapping_flagged(tmp_path, monkeypatch):
    """Behaviour change: an unconfirmed mapping is now projected carrying
    ``confirmed: false`` rather than dropped. Dropping it meant a freshly
    ingested table routed to nothing until a human reviewed it, and on a
    query-only host (no $ARTMIND_DATA_DIR, hence no registry DB) this subgraph
    is the only place the mapping exists at all. The reported ``mappings``
    count still means *confirmed* edges, so its meaning is unchanged."""
    _seed(tmp_path, monkeypatch, confirmed_mapping=False)

    fake = FakeSession()
    monkeypatch.setattr(catalogue, "neo4j_session", lambda **kw: fake)

    result = catalogue.project_catalogue("banking")

    assert result["mappings"] == 0, "count still tracks confirmed edges only"

    mapping_calls = [(c, p) for c, p in fake.calls if "MAPS_TO_CLASS" in c]
    assert len(mapping_calls) == 1
    cypher, params = mapping_calls[0]
    assert "m.confirmed" in cypher
    assert params["confirmed"] is False
    assert params["confidence"] == 0.9


def test_project_catalogue_projects_grain_and_bridge_role(tmp_path, monkeypatch):
    """Routing and quarantine inputs have to reach the graph: a query-only host
    has no registry DB, so this subgraph is its only source for them."""
    from artmind.structured import registry

    table_id = _seed(tmp_path, monkeypatch)
    registry.set_grain(table_id, "lookup", confirmed=True)
    registry.upsert_column_role(table_id, "customer_name", "term", 0.9, confirmed=True)

    fake = FakeSession()
    monkeypatch.setattr(catalogue, "neo4j_session", lambda **kw: fake)

    catalogue.project_catalogue("banking")

    _, table_params = next((c, p) for c, p in fake.calls if "MERGE (t:Table" in c)
    assert table_params["props"]["grain"] == "lookup"
    assert table_params["props"]["grain_confirmed"] is True

    column_params = [p for c, p in fake.calls if "MERGE (c:TableColumn" in c]
    by_name = {p["props"]["name"]: p["props"] for p in column_params}
    assert by_name["customer_name"]["bridge_role"] == "term"
    assert by_name["customer_name"]["bridge_role_confirmed"] is True
    # A column with no declared role carries an explicit null, not a missing key.
    assert by_name["balance"]["bridge_role"] is None
    assert by_name["balance"]["bridge_role_confirmed"] is False


def test_project_catalogue_never_labels_catalogue_nodes_entity(tmp_path, monkeypatch):
    """Phase 3 risk (a): catalogue labels must never carry :Entity, or they'd
    leak into `query graph pattern*`/vector search/`query graph metadata`."""
    _seed(tmp_path, monkeypatch)

    fake = FakeSession()
    monkeypatch.setattr(catalogue, "neo4j_session", lambda **kw: fake)

    catalogue.project_catalogue("banking")

    for cypher, _ in fake.calls:
        assert ":Entity)" not in cypher
        assert ":Entity {" not in cypher


def test_project_catalogue_uses_write_access_mode(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    seen_kwargs = {}

    def fake_neo4j_session(**kw):
        seen_kwargs.update(kw)
        return FakeSession()

    monkeypatch.setattr(catalogue, "neo4j_session", fake_neo4j_session)

    catalogue.project_catalogue("banking")

    assert seen_kwargs.get("access_mode") == "WRITE"
