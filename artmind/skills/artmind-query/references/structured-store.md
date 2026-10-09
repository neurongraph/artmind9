# Structured store (`db`)

Read this when `artmind db bridge` returns any tables for the selected domain(s) — a
pure-graph domain never needs this file; go straight to Views in SKILL.md.

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above. Rows never become graph
nodes — the graph only ever holds a catalogue of what tables/columns exist.

- `artmind db bridge --domain <d> --compact` — **the routing entry point.** Per
  table: `entity_classes` (routing key), `bridge_columns` (values that seed graph
  retrieval), and `grain`. Add `--entityClass <CLASS>` for class-first discovery.
- `artmind db list --domain <d> --compact` — which structured tables (if any)
  exist for this domain. Physical listing only; prefer `db bridge` for routing.
- `artmind db schema <table> --compact` — columns, types, and (once confirmed)
  column→entity-class mappings for a table.
- `artmind db sql "<SQL>" --compact` — raw read-only SQL, no LLM involved.
- `artmind db timeline <table> --domain <d> [--asOf <date>] --compact` — point-in-time
  query over a `refresh_mode: temporal` table's captured SCD-2 history: omit `--asOf`
  for the currently-open rows, or pass a date to see the table as it stood then. Use
  this instead of hand-writing `_valid_from`/`_valid_to` filters in `db sql` — it only
  applies to temporal tables (check `db schema`'s `refresh_mode` field first; a
  `replace`-mode table has no history to query and the command errors clearly if asked).
- `artmind db mappings <table> --compact` — review proposed vs confirmed
  column→entityClass mappings for a table (registry rows, not a file). Bulk-confirm
  everything proposed with `--acceptProposed`, or manage one mapping at a time with
  the `set`/`confirm`/`clear` subcommands (`db mappings <table> set --column c
  --entityClass PRODUCT`, `... confirm --column c --entityClass PRODUCT`,
  `... clear --column c` or `... clear` for all).
- `artmind db catalogue --domain <d> --compact` — rebuild the Neo4j catalogue
  subgraph (Table/TableColumn/EntityClass) for a domain from the registry. Ingest
  already does this automatically; use this on demand after confirming mappings
  later, to reflect that confirmation in the graph without re-ingesting.
- `artmind query text2sql "<question>" --domain <d> --compact` — natural language
  to read-only DuckDB SQL against the structured store, then executes it (add
  `--dry-run` to see the generated SQL without running it). The SQL/graph analogue
  of `query graph text2cypher`.
- `artmind query resolve-key "<phrase>" --domain <d> --column <col> --compact` —
  resolve a free-text value (e.g. from a user question or a structured row) to a
  canonical column value and/or graph entity name, via exact/fuzzy matching.
  `--column` is optional; omit it to resolve against the graph only. Useful to
  normalize a value before using it in `text2sql`/graph retrieval, or to check
  whether a structured column value and a KG entity name refer to the same thing.

There is no monolithic `hybrid` command — the skill itself is the router/fuser,
composing `resolve-key`, `text2sql`/`db sql`, and graph patterns in its own
reasoning. See "Store routing" below for how to decide.

## Store routing

Once the domain set is fixed, read the **bridge** — the one call that says
whether a structured store exists here, which tables are about the classes in
the question, and which of their columns hold values worth searching the graph
for:

```bash
artmind db bridge --domain <d> --compact
```

Per table it returns `entity_classes` (the routing key — many-to-many with
tables, which a single dotted `--domain` never could be), `bridge_columns`
(whose *values* seed graph retrieval), and `grain`.

Do not route on `--domain` alone. Documents usually carry a genre-scoped domain
(`<corpus>.<genre>`) because that is the level an extraction schema lives at,
while tables carry the corpus root (`<corpus>`), because a table has no genre.
`db bridge` matches the hierarchy in both directions, so asking from a leaf
domain still finds tables registered at the root. To go the other way — "which
tables involve this class at all?" — use `--entityClass`:

```bash
artmind db bridge --entityClass <CLASS> --compact
```

An empty `tables` list means the domain is genuinely pure-graph — skip
SQL/hybrid entirely and go straight to Views in SKILL.md. Otherwise classify the
question first; only pull table shape if the classification needs it, so a
narrative-only session in a domain that happens to have tables never pays for a
schema call it doesn't use:

- **Narrative/relationship** ("tell me about X", "how are X and Y related",
  "why did…") → graph path — Discover/Resolve/Retrieve as documented below
  (patterns / `text2cypher`). No structured store involved, no `db schema` call.
- **Analytical/aggregate** ("average/total/count/sum by X") → pull the table
  shape you need with `artmind db schema --domain <d> --compact` (or `db schema
  <table>` for one table) — the column/type context an LLM needs to write SQL —
  then SQL only: `artmind query text2sql "<question>" --domain <d> --compact`
  (or `db sql "<SQL>"` if you already know the exact query — e.g. from a prior
  `--dry-run`). No graph retrieval needed.
- **Hybrid** (the question names something that lives as a graph entity but
  needs a number that lives in a table) → pull `db schema` as above, then
  canonicalize, then query SQL, then optionally add graph context, then
  synthesize:
  1. `artmind query resolve-key "<phrase>" --domain <d> --column <col> --compact`
     to turn the user's phrase into the exact value stored in the column (and/or
     the matching graph entity name) — don't hand a raw user phrase to
     `text2sql`/`db sql` and hope it matches the stored spelling.
  2. `artmind query text2sql "<question with canonical value>" --domain <d>
     --compact` (or `db sql` with the canonical value substituted in) for the
     numbers.
  3. If the question also needs relationship context (not just a number), add
     one graph pattern or `entity-context` call on the resolved entity.
  4. Synthesize the combined answer yourself in this turn — there is no
     "fusion" command; steps 1-3 are already composed by you, the skill.
- **Records-plus-guidance** ("which X is in state Y, and what does our
  policy/training require when handling it") → the two stores hold
  *complementary* content, not overlapping content: the tables record what IS
  true of particular people and cases, the graph states what SHOULD be done
  about that kind of case. Do not try to join them by class: a subject class the
  tables are full of is often nearly empty in the graph, because documents state
  rules *about* that subject rather than instantiating it. Join on **values**
  instead:
  1. `db sql`/`text2sql` for the records, selecting the `bridge_columns` that
     `db bridge` listed, not just the ids.
  2. Feed those returned cell values as the query string to
     `artmind query vector-text "<values + question terms>" --domain <corpus>
     --asOf today --compact`. Unscoped across the corpus is fine and fast;
     `--asOf today` matters — without it retired policy versions rank
     alongside current ones.
  3. Synthesize, citing the guidance documents by name.

If a table's `grain` is `normative`, it asserts rules a document may also
state. The graph wins on disagreement — report the difference rather than
silently picking a side.

Worked examples:

- **Usage A — "Total balance across SmartSaver accounts."** Hybrid: `SmartSaver`
  is a PRODUCT entity in the graph but `balance` lives in a table. Run
  `resolve-key "SmartSaver" --domain banking --column product_name --compact` to
  get the canonical `product_name` value, then `text2sql "total balance where
  product_name is <canonical>" --domain banking --compact` (or `db sql` with the
  literal substituted in) for the sum. Add a graph pattern only if the answer
  also needs product relationships/ownership, not just the total.
- **Usage B — "Average X by month."** Analytical-only: no entity to resolve, no
  graph involvement — go straight to `text2sql "average X by month" --domain
  <d> --compact`, or `db sql` if you already have the exact SQL from a prior
  `--dry-run`.
- **Usage C — "Which vulnerable customer has an open complaint, and what extra
  care does our training and complaints guidance require?"** Records-plus-
  guidance. `db bridge --domain banking --compact` shows
  `vulnerable_customers` carrying `vulnerability_driver` and `support_needed`
  as `bridge_columns`. `db sql` joins it to `complaints` on the open status,
  returning those two columns alongside the customer. Their *values* then
  become the retrieval phrase: `query vector-text "<driver> <support> handling
  complaints extra care" --domain banking --asOf today --compact`, which
  reaches the training and complaints-policy documents. Note the class join
  would have failed here — the graph has almost no CUSTOMER entities, because
  the documents set rules about vulnerable customers rather than listing them.

`--asOf` consistency: if the question is temporal ("as of last quarter", "as of
<date>"), pass the SAME `--asOf <date>` to every command in the hybrid chain that
accepts one — `vector-text` and `query text2sql` both honor it. `db sql` itself has
no notion of "as of" (raw SQL, nothing injected) — for a temporal structured table,
use `artmind db timeline <table> --asOf <date> --compact` instead of hand-writing
`_valid_from`/`_valid_to` filters in `db sql`, and see `references/temporal.md` for
which other commands take `--asOf` and what it means on each. One date, threaded
through every command that can take one, not decided independently per command.

`--compact` applies to `db`/`query text2sql`/`query resolve-key` exactly like
every other command in this skill — nothing SQL-specific changes that.
