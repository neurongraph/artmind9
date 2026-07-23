# Ingesting the synthetic structured test data

Five small csv files in this folder (`customers.csv`, `vulnerable_customers.csv`,
`agents.csv`, `complaints.csv`, `csat_scores.csv`) feed the structured-data
ingestion pipeline — DuckDB/parquet + registry, not the graph. See
`../schema_mapping.md` for what each file contains and why.

Note: `artmind ingest ...` and `artmind db ...` always run in-process — only
`artmind query ...` gets proxied to a running `serve` daemon (`artmind/_entry.py`),
so `ARTMIND_NO_PROXY=1` is not needed for the steps below.

## 1. Ingest

One command per file (order doesn't matter — no cross-table foreign keys are
enforced at load time):

```bash
artmind ingest sync banking_document_corpus/structured/customers.csv --domain banking
artmind ingest sync banking_document_corpus/structured/agents.csv --domain banking
artmind ingest sync banking_document_corpus/structured/vulnerable_customers.csv --domain banking
artmind ingest sync banking_document_corpus/structured/complaints.csv --domain banking
artmind ingest sync banking_document_corpus/structured/csat_scores.csv --domain banking
```

Don't point `ingest sync` at this whole folder — it walks every file
recursively regardless of extension, so it would also try to KG-extract this
README itself. Point it at each csv individually as above.

Every table lands in the literal `banking` domain (not a `banking.*` child) —
this is deliberate, so `--domain banking` finds the tables directly and so a
future mapping-proposer run (`entity_listing(["banking"])`, Phase 2) rolls up
across every sibling document domain (`banking.products`, `banking.organization`, …).

## 2. Verify

```bash
# tables + row counts
artmind db list --domain banking --compact

# columns + dtypes for one table
artmind db schema complaints --compact

# raw read-only SQL — no LLM involved
artmind db sql "SELECT count(*) FROM complaints" --compact
artmind db sql "SELECT channel, round(avg(csat_score),2) AS avg_csat FROM csat_scores GROUP BY channel" --compact
```

Expected row counts: `customers` 24, `vulnerable_customers` 7, `agents` 16,
`complaints` 20, `csat_scores` 30.

## 3. Re-ingesting

Re-running step 1 unchanged is a no-op (`"status": "skipped"` — same-hash
dedup per table). To force a reload and bump `version`, add `--force`:

```bash
artmind ingest sync banking_document_corpus/structured/complaints.csv --domain banking --force
```

## 4. Cleaning up (registry + parquet only — never touches the graph)

```bash
rm -rf ~/artmind_data/structured/banking
sqlite3 ~/artmind_data/document_registry.db "
DELETE FROM column_mappings WHERE table_id IN (SELECT id FROM \"tables\" WHERE domain='banking');
DELETE FROM \"columns\" WHERE table_id IN (SELECT id FROM \"tables\" WHERE domain='banking');
DELETE FROM \"tables\" WHERE domain='banking';
"
```

(Adjust `~/artmind_data` if `$ARTMIND_DATA_DIR` is set to something else.)

## Not available yet

`db mappings`, `db catalogue`, `db refresh`, `query text2sql`, and
`query resolve-key` land in later phases of the structured-data-ingestion plan
(`docs/superpowers/plans/2026-07-23-structured-data-ingestion-plan.md`) — as of
Phase 1 only `db list` / `db schema` / `db sql` (+ ingest) exist.
