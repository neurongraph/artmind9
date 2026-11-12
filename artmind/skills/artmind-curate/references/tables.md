# Structured tables: grain, bridge columns, mappings

Ingest classifies each registered table in three steps, and every result lands unconfirmed for
review:

- **grain**: whether rows record facts (`instance`), reference values (`lookup`) or rules a document
  also asserts (`normative`);
- **bridge**: which columns hold values worth searching the graph for;
- **mapping**: which columns denote instances of which `entity_class`.

When a step's status is `failed` or `pending`, there is nothing to review: send the user to
artmind-ingestion-helper (`db propose`). Come here when the steps ran and the question is whether the
answers are right. A re-ingest does not undo review: confirmed grain and mappings survive it.

## Read

```bash
artmind db review --domain <d> --compact              # every table with something unconfirmed; pending_count
artmind db mappings <table> --domain <d> --compact    # column → entity_class, confirmed flag
artmind db grain <table> --domain <d> --compact       # grain and bridge columns
artmind db schema <table> --domain <d> --compact      # each column's profile and distinct_sample
artmind domains entities-prompt <d>                   # the class descriptions the proposer read
```

Read the schema through `domains entities-prompt`, not `domains/schemas/*.yaml`: the schema lives in
the run folder.

## Judge

Judge sampled values against the class description, never the column name.

- **High confidence is not correctness.** `agents.name → CUSTOMER` at 0.9 is wrong when agents are
  staff. Read every proposal, not just the low scores.
- **Identifier columns are the point.** `customer_id → CUSTOMER` on opaque keys (`CUST-0019`) is right:
  it says what the column denotes.
- **Several classes per column are legitimate** (`category` → `PRODUCT` and `ISSUE_TYPE`). Reject only
  the wrong ones.
- **Booleans, dates and measures map to nothing.** A proposal on `resolved_first_contact` or a `*_gbp`
  amount is noise.
- **Grain**: only `normative` changes behaviour: the table is kept out of answer synthesis because a
  document asserts the same content. When torn, the proposer picks `normative` to force this review,
  so a surprising `normative` is working as intended. Confirming it requires `refresh_mode=temporal`.

## Apply

```bash
artmind db mappings <table> --domain <d> confirm --column <c> --entityClass <CLASS>
artmind db mappings <table> --domain <d> --acceptProposed          # all, only after reading all
artmind db mappings <table> --domain <d> clear --column <c>        # drops EVERY class on the column
artmind db mappings <table> --domain <d> set --column <c> --entityClass <CLASS>   # one the model missed
artmind db grain <table> --domain <d> --set instance|lookup|normative             # no --dryRun yet
artmind db bridge confirm --table <table> --domain <d> --column <c>
artmind db bridge clear   --table <table> --domain <d> --column <c>
```

Tell the user before they act:

- `clear --column` drops every class on that column. Where one of two is right, `confirm` it first: a
  re-propose never overwrites a confirmed pair.
- `clear` is not a durable rejection: `db propose --redo` may propose it again. Confirming the right
  mapping is what makes a decision stick.
- A class removed from the schema: the next mapping step deletes unconfirmed mappings to it
  (`pruned_mappings`) but keeps confirmed ones (`stale_confirmed_mappings`); re-pointing those
  (`clear` + `set`) is the user's decision.
- Confirming is a trust signal, not a switch: unconfirmed mappings are already used for routing,
  with `confirmed: false`.
- Nothing reads a bridge column's confirmed flag yet; confirming records the judgment so the column
  is not argued again.

## Verify

```bash
artmind db bridge --domain <d> --compact   # the routing surface queries read
artmind db catalogue --domain <d>          # push confirmations into Neo4j on this machine
artmind db review --domain <d> --compact   # repeat until pending_count is 0
```

Confirmations update the registry and the table's `.meta.json`; `db catalogue` is what reaches the
graph here. Other machines get them through `vault sync` (after `vault sync --store graph` alone, run
`db catalogue` there).
