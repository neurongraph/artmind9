"""Projects the structured-store's physical schema + confirmed mappings into a
distinctly-labelled Neo4j catalogue subgraph.

Level-1 integration (spec §3): the catalogue mirrors what's in the SQLite
registry (``artmind/structured/registry.py``) as ``:Table``/``:TableColumn``/
``:EntityClass`` nodes, linked ``HAS_COLUMN`` and ``MAPS_TO_CLASS``. Nothing
authoritative lives in this subgraph — it is a derived projection — so a
rebuild is always safe: wipe the domain's catalogue nodes, then re-MERGE from
the registry.

These labels must never carry the ``:Entity`` label. That is what keeps them
out of `query graph pattern*`, vector/fulltext search, and
`query graph metadata` — those all key off ``:Entity``/``:Document``/
``:DocChunk``. Do not add ``:Entity`` to any node this module writes.

Unlike ``ingest.py``'s ``_write_to_neo4j`` (which hand-rolls a driver because
it needs an isolated connection lifecycle inside a background job), this
module goes through ``graph_query.neo4j_session`` — the shared, env-driven
connection helper used everywhere else outside the ingestion worker.
"""

from artmind.graph_query import neo4j_session
from artmind.structured import registry


def project_catalogue(domain: str) -> dict:
    """Wipe and re-project the catalogue subgraph for ``domain``.

    Returns ``{"tables": n, "columns": n, "mappings": n}`` — counts of nodes/
    edges written, not touched-but-unchanged. ``mappings`` only counts
    *confirmed* column→EntityClass mappings; proposed-but-unconfirmed
    mappings are not projected (spec: the confirm gate is what makes a
    mapping trustworthy enough to surface to text2sql/query).
    """
    tables = registry.list_tables([domain])

    tables_count = 0
    columns_count = 0
    mappings_count = 0

    with neo4j_session(access_mode="WRITE") as session:
        # ── Wipe: nothing authoritative lives here, so a clean slate per
        # domain is cheap and avoids stale nodes for renamed/removed tables.
        session.run(
            """
            MATCH (t:Table {domain: $domain})
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:TableColumn)
            DETACH DELETE t, c
            """,
            domain=domain,
        )

        for table in tables:
            # list_tables() rolls sub-domains up into the parent (e.g. a
            # "banking" query also returns "banking.retail" tables) — but the
            # wipe above only clears nodes tagged with this exact domain, so
            # keep projection scoped the same way to stay idempotent.
            if table["domain"] != domain:
                continue

            table_key = f"{table['datasource']}::{table['table_name']}"
            session.run(
                """
                MERGE (t:Table {key: $key})
                SET t += $props
                """,
                key=table_key,
                props={
                    "name": table["table_name"],
                    "domain": table["domain"],
                    "row_count": table.get("row_count"),
                    "parquet_path": table.get("parquet_path"),
                    "datasource": table["datasource"],
                },
            )
            tables_count += 1

            columns = registry.get_columns(table["id"])
            mappings_by_column: dict[str, list[dict]] = {}
            for mapping in registry.list_mappings(table["id"]):
                mappings_by_column.setdefault(mapping["column"], []).append(mapping)

            for column in columns:
                column_key = f"{table_key}::{column['name']}"
                session.run(
                    """
                    MERGE (c:TableColumn {key: $colkey})
                    SET c += $props
                    WITH c
                    MATCH (t:Table {key: $key})
                    MERGE (t)-[:HAS_COLUMN]->(c)
                    """,
                    colkey=column_key,
                    props={
                        "name": column["name"],
                        "dtype": column["dtype"],
                        "profile": column.get("profile_json"),
                    },
                    key=table_key,
                )
                columns_count += 1

                for mapping in mappings_by_column.get(column["name"], []):
                    if not mapping["confirmed"]:
                        continue
                    entity_class = mapping["entity_class"]
                    entity_class_key = f"{domain}::{entity_class}"
                    session.run(
                        """
                        MERGE (ec:EntityClass {key: $eckey})
                        SET ec += {name: $name, domain: $domain}
                        WITH ec
                        MATCH (c:TableColumn {key: $colkey})
                        MERGE (c)-[:MAPS_TO_CLASS]->(ec)
                        """,
                        eckey=entity_class_key,
                        name=entity_class,
                        domain=domain,
                        colkey=column_key,
                    )
                    mappings_count += 1

                    # RESOLVES_AGAINST (spec §4.3) — deliberately not built
                    # here. It would link (:TableColumn)-[:RESOLVES_AGAINST]->
                    # (:Entity) for a small, bounded sample of matched entity
                    # nodes, but the resting link between the structured and
                    # graph stores is meant to be shared strings (name +
                    # class + domain), not persisted anchors — see Phase 3
                    # risk (b) in the ingestion plan. Documented gap, not an
                    # oversight; add only if a concrete consumer needs it.

    return {
        "tables": tables_count,
        "columns": columns_count,
        "mappings": mappings_count,
    }
