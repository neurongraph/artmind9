# Stage walk

Walk the stages in this order and stop at the first that failed. Each later
stage is more expensive to fix. A verdict must cite evidence: a span id, a
result row, a chunk sentence (quoted as `>`), or a staging entry.

## Ground truth

The chunks a correct answer rests on.

1. The chunks the answer **used**: the `chunks` and `documents` evidence of the
   productive spans in `artmind traces show`, and the answer's `### Sources`.
2. The chunks it **should** have used: search the question and the operator's
   `expected` note — `artmind query vector-text --domain D "<expected fact>"` —
   and read the hits with `artmind query chunks --domain D --idList <id> --expand 1`.
3. If no chunk states what a correct answer needs → `corpus-gap`. Stop.

## The stages

### `corpus-gap`
No ingested document states the fact. Proposal: what to ingest (which kind of
document, which domain). No shape audit, no synthetic example.

### `synthesis`
The right rows came back, but the answer misstated them, left them out, or went
beyond them. Evidence: the span rows next to the answer sentence. Proposal: a
change to the `artmind-query` skill's answer rules (file and section).

### `routing`
A view, pattern or command that answers the question exists and was not used,
or was used with the wrong parameters (wrong entity id, wrong relationship
type, wrong domain). Evidence: the unproductive span's command and args, and
the command you ran that does answer it. Proposal: a change to the query skill
(which step, which rule) or to a view's `SKILL.md` description.

### `missing-view`
The question needs specific Cypher and it recurs (`artmind traces list` shows
similar questions). Proposal: a `view.yaml` + `SKILL.md` draft
(`artmind-create-view`'s format); Proof: `artmind views test` output.

### `identity`
The facts exist but are split across duplicate entities or merged into the
wrong one. Evidence: two `entity-resolve` candidates or `entity-context`
results for one real thing. Shape audit required. Proposal: a same-as /
curation task (`artmind-curate`), or a canonicalization prompt fix if it is
systematic.

### `extraction`
The fact is in the chunk, the schema can represent it, and it is absent from
(or wrong in) the staging JSON. Shape audit required. Proposal: a
`meta.yaml` prompt change or extraction-code change, with the Synthetic
example as the test case.

### `schema`
The schema has no class, property or relationship type that could hold the
fact in the shape the question needs. Shape audit required. Proposal: a schema
YAML diff (domain schema in the vault — vault content — or a packaged schema
in `artmind/domains/schemas/` — code). Note the re-ingest it forces.

## Graph misses

A turn with `fell_through: true` and `answer_basis: vector` was answered from
chunk text after the graph failed — even if the answer is right.

- The vector-returned chunks the answer cites are the ground truth; no search.
- Skip `synthesis` when the answer is rated good or unrated.
- Start at `routing`: for every unproductive graph span in the retrieval path,
  was the command, entity id and relationship type right? Re-run it corrected;
  if the corrected call answers, the verdict is `routing`.
- Then `missing-view`, `identity`, `extraction`, `schema`, always with the
  shape audit on the cited chunks.
- The regression question's evaluation comment must require a graph answer:
  "answered from the graph (answer_basis graph or mixed, no fall-through)".

## Shape audit

Build the bundle — text, graph, staging, schema for the ground-truth chunks:

```bash
artmind review shape <trace_id>                      # chunks the answer cited
artmind review shape <trace_id> --chunk <id> --chunk <id>   # the chunks a correct answer needs
```

- `text` — each chunk's sentences, numbered (quote them as `>` in the dossier).
- `graph` — the entities extracted from those chunks plus one `RELATES_TO` hop.
- `staging` — what extraction produced for each chunk before merge/commit. In
  staging but not in the graph → lost at merge/commit (identity, or a code bug).
  Not in staging → lost at extraction (or the schema has no place for it).
- `schema` — the `guidance` and the classes involved.

Write the **answerability pattern** — the traversal that would answer the
question — and pass it with `--pattern`; the bundle reports whether the graph
and the staging match it:

```json
[{"class": "PERSON", "name": "<who>"}, {"rel": "WENT_TO", "dir": "out"}, {"class": "LOCATION"},
 {"require": {"edge": 0, "properties": ["date"]}}]
```

Steps alternate node / relationship (`dir`: out, in, any); `require` steps go
last and index nodes or edges from 0. Names, classes and relationship types
compare case-insensitively. If `match` results include `truncated: true`, narrow
the pattern.

## Proving a schema or prompt fix

Write the proposed schema (a full copy of the domain's schema YAML, edited) or
a proposed `meta.yaml` to a scratch file **outside the checkout** (e.g. under
`$TMPDIR`) — it is derived from vault content. Then:

```bash
artmind review reextract <trace_id> --pattern '<json>'                          # baseline: LLM variance
artmind review reextract <trace_id> --schema "$TMPDIR/proposed.yaml" --pattern '<json>'
```

It re-extracts at most `--maxChunks` chunks (3 LLM calls each), writes
nothing, and reports per chunk the nodes / edges / properties added and
removed against current staging, and answerability `current` vs `proposed`.
The proof is: the baseline does not flip answerability, the proposal does.
Paste the relevant part of the output into the dossier's Proof section.

Grade against the checklist; tag each finding `<domain>/<Class>[.<property>|-<REL>->]`:

| Check | Finding when |
|---|---|
| coverage | a fact the question needs is in the text and absent from the subgraph |
| granularity | a fact is flattened into a string property or `description` when the question needs an entity plus a relationship — or a trivial value is promoted to a node |
| specificity / direction | a generic relationship type where the question needs a typed one, or the edge points the wrong way |
| reification | an n-ary fact (actor, action, object, time, role) collapsed into a binary edge, losing qualifiers |
| qualifiers | temporal, status or provenance qualifiers missing or on the wrong element |
| identity | one real thing split across nodes, or distinct things merged |
| noise | entities or edges the text does not support |
| answerability | the answerability pattern has no match in the subgraph |
