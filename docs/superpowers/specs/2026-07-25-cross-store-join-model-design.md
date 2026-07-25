# The cross-store join model: two bridges, not one

**Status:** Design — approved in brainstorming
**Date:** 2026-07-25
**Owner:** Surjit Das
**Amends:** `2026-07-23-structured-data-ingestion-design.md` — supersedes its
principle 5 and the §3 "shared strings scoped by domain" clause.

## 1. Purpose

The 2026-07-23 design made **domain** the unifying scope across the graph and the
structured store (principle 5): *"Every table is assigned a domain; hybrid queries are
always domain-bounded; the resolver only matches a column's values against entities in
the same domain."*

That principle does not survive contact with hierarchical domains. `domains-overview`
lists `banking.cases`, `banking.policy`, … — document *genres* — while all six
structured tables are registered under bare `banking`. An agent following the documented
routing workflow (get domains, then `db list --domain <d>` per domain, treat empty as
"pure-graph") concludes no structured store exists and never finds real, queryable data.

The same mismatch is compiled into the catalogue projection: `catalogue.py:114` keys
`EntityClass` as `f"{table_domain}::{entity_class}"` → `banking::CUSTOMER`, a string no
`banking.policy` entity can ever match.

This document replaces that principle with a model derived from measurement.

## 2. What the graph actually contains

Measured against the live banking corpus (63 documents) on 2026-07-25:

- **65 distinct entity classes, 5,430 entities.**
- **Only 6 classes appear in more than one domain** — `ACCOUNT`, `AML_SCREENING`,
  `CARD`, `CUSTOMER`, `PRODUCT`, `TRANSACTION` — totalling 93 entities, **1.7%**.
- **The parent schema's 12 harmonised classes hold 27 entities — 0.5%.** `CUSTOMER` has
  3, `ACCOUNT` 6, `KYC_VERIFICATION` 1, `SAR` 1, `FRAUD_ALERT` 1.
- The graph is in fact `PROCESS_STEP` (1,102), `POLICY_PROVISION` (730),
  `RESPONSE_ACTION` (129), `WARNING_SIGN` (106), `METRIC_TARGET` (178).

Joining the 24-row `customers` table to the `CUSTOMER` class therefore reaches **3 graph
nodes**.

**This sparsity is correct, not a defect.** Normative documents state rules *about*
customers; they do not instantiate them. There is no class-to-class join to find,
because the two stores hold complementary — not overlapping — content by design.

## 3. The model

### 3.1 `domain` is demoted

`domain` keeps exactly two jobs, both of which it does well:

1. A **query filter** that narrows the graph, so a question does not confront all 65
   classes and 5,430 entities.
2. The **scope at which an extraction schema lives**, keeping any one extraction prompt
   short enough for an LLM to work from.

It is **not** the cross-store join. This supersedes principle 5.

### 3.2 `entity_class` is the routing key — and only that

It answers "does a structured store exist for this question, and which tables are
relevant". It is many-to-many in both directions — `complaints` maps to `CUSTOMER`,
`EMPLOYEE` and `PRODUCT` at once — which is precisely what a single-valued dotted
`domain` could never express.

It is **structurally incapable** of being the fusion key, per §2.

### 3.3 Fusion runs on values → semantic retrieval

SQL results supply query strings for `query vector-text` / `entity-resolve`. This is the
2026-07-23 design's Level 2, unchanged, and it needs no new bridge.

**Verified end to end on 2026-07-25.** For the acceptance question, the SQL join yields
`vulnerability_driver = "Life Events"`, `support_needed = "Safe Space"`. Feeding those
values to `vector-text` **unscoped** across `banking` returns, in ~1.4s:

- top hit `branch_operations_training.md` — *"Vulnerable Customers … Abuse victims:
  Offer safe space, confidential help"* (`banking.communications`);
- and for complaint handling, 6 of 6 hits relevant with zero noise, spanning
  `banking.communications`, `banking.policy` and `banking.sop_guides`.

### 3.4 The relation that matters is *governs*, not equality

The useful relation is "table subject → normative classes that govern it"
(`vulnerable_customers` → `TRAINING_MODULE`, `WARNING_SIGN`, `POLICY_PROVISION`). That
is not an equality, which is why no amount of class alignment would have produced it —
and why schema harmonisation was rejected as a path.

## 4. Content definitions (grain)

- **`instance`** — rows denote particular real-world individuals or events, identified by
  a business key. No ingested document asserts them. *Home: tables.*
- **`lookup`** — rows denote members of a controlled vocabulary or code list. Type-level,
  but no ingested document asserts them. *Home: tables.*
- **`normative`** — rows assert rules, thresholds, obligations or entitlements that an
  ingested document also states or could state. *Home: graph.* If loaded as a table it
  must declare `grain=normative` and must use `refresh_mode=temporal`, because facts
  that get superseded need history.

**Quarantine rule.** Fires only on the conjunction `grain=normative` **and** the table's
derived class scope intersecting the graph classes in query scope. Graph wins; the
disagreement is surfaced, never silently resolved.

Rationale, narrowed by measurement: valid-time is *already* unified across both stores —
`scd2.py` mirrors `graph_query.asof_predicate`'s semantics and `text2sql.py` imports
`resolve_as_of` from `graph_query` — so temporality is **not** an argument for
quarantine. What remains graph-only is **conflict detection** (`conflicts.py`,
`CONFLICTS_WITH` operate on `:Entity` nodes) and **provenance quality** (graph entities
carry document provenance and verbatim context snippets; a row carries a file path).

## 5. The metadata catalogue

Three layers, separated by what you must know to produce them. That separation
determines who authors each and when it applies.

| Layer | Contains | Knowable from | Authored by | Volatility |
|---|---|---|---|---|
| **Physical** | column names, dtypes, row counts, profiles | the file alone | profiler, deterministic — no LLM, no human | stable |
| **Semantic** | `column → entity_class`, `bridge_role`, `grain` | file + corpus | LLM proposes by consulting the graph, operator confirms | stable |
| **Relational** | `governed_by` | file + what the graph *says* | not built — see §5.2 | volatile |

Class scope stays **derived**, never declared:
`SELECT DISTINCT entity_class FROM column_mappings WHERE table_id = ?`.

### 5.1 Applied at two distinct times

- **Build time** (ingest / refresh / `db catalogue`): profile → propose → confirm →
  project to Neo4j.
- **Query time**: routing reads classes, SQL generation reads profiles, fusion reads
  `bridge_role`, quarantine reads `grain`.

Nothing in the catalogue is computed at query time.

### 5.2 `governed_by` is not built

It would record which graph domains/classes hold normative content about a table's
subject. The §3.3 measurement shows fusion does not need it — unscoped value-driven
retrieval already returns the right guidance with zero noise on the complaints query. It
would buy only scoping precision, cost, and explainability, and it is the one volatile
layer: ingesting a document can stale it, so a persisted form needs an invalidation
policy.

Revisit only if retrieval precision proves inadequate at larger corpus size. If built,
granularity is **per table**.

## 6. Consequences

- No schema harmonisation, no collapsing the role/org synonym clusters
  (`ROLE_PERSON` / `ROLE_ACTOR` / `ROLE_RESPONSIBILITY` / `EMPLOYEE`), no re-ingestion.
  The 0.5% measurement says the parent spine is not load-bearing.
- The domain schema YAML stays untouched, as the 2026-07-23 design already requires.
- Discovery becomes class-first: `db list --entityClass`, and `domains-overview` unions
  graph domains with domains holding structured tables.
- Unchanged non-goals: no row-level fusion; no `RESOLVES_AGAINST` anchor nodes.
- No FIBO/APQC formalisation. Per `docs/apqc-fibo-vs-banking-schemas.md` they stay
  post-hoc references — APQC a coverage audit, FIBO a crosswalk for the narrow
  party/account/product slice. Their value here is as a **naming reference**, not as a
  join mechanism.
