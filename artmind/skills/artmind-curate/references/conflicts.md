# Conflicts: two sources disagree

A conflict is two authorities asserting different things about the same entity. Detection never
closes one; a person decides. There are two kinds, told apart by `_source`:

- **`adjudicator`**: two entities (usually in different domains) whose claims an LLM judged to
  disagree. Listed by `query graph conflicts`, closed with `ingest resolve-conflict`.
- **`projection`**: one entity's own property given two values for the same time. It clears itself on
  the next rebuild once the observations stop disagreeing; there is no close step, and no CLI lists
  it yet (it shows in entity reads and in the query skill's conflict reference).

## Detect

```bash
artmind ingest detect-conflicts --domain <a> --domain <b> --maxPairs 20 --dryRun --output conflicts.json
# review conflicts.json with the user
artmind ingest detect-conflicts --domain <a> --domain <b> --fromFile conflicts.json
```

Run name clustering on each domain first (same-as.md) so pairing works on de-duplicated entities.
`sameas propose` also writes conflicts when its verdict is "conflicting claims". Pairing follows the
same scope rule as `sameas propose`: one leaf domain compares inside it, several domains compare
across them only.

## Read

```bash
artmind query graph conflicts --domain <a> --domain <b> [--entityName "<name>"] --compact
```

For each conflict, read both sides' documents. Before calling it a live disagreement, check:

- **Is one side an older version of the other?** If one document replaces the other, record the
  supersession (`ingest supersede`, documents.md) and resolve the conflict with that reason. Do not
  retire the older document instead: retiring drops the link between versions, which dated
  questions are answered from.
- **Different scopes?** Two products, two dates, two customer groups: a dismissal, not a conflict.
- **Extraction error?** One side misread its text: dismiss, and note the document for re-extraction.

Report conflicts grouped by root cause (shared aspect wording, shared entities), with severity and
both documents cited per finding.

## Close

```bash
artmind ingest resolve-conflict <conflict_id> --status resolved|dismissed --reason "<why>" --dryRun
artmind ingest resolve-conflict <conflict_id> --status resolved|dismissed --reason "<why>"
```

`dismissed` is for a false positive. The decision is a record file under
`.artmind/data/curation/conflicts/`, travels with the vault, and is never reopened by a later
detection. `query graph conflicts --status all` shows closed ones.
