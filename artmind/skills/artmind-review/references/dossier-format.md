# Dossier format

A dossier is `<vault>/.artmind/reviews/<review_id>.md`: YAML frontmatter, then
nine `## ` sections in a fixed order. `artmind review new` writes the skeleton;
`artmind review validate` checks it. HTML comments (`<!-- … -->`) are
placeholders and do not count as content.

## Frontmatter

| Key | Value |
|---|---|
| `review_id` | `rv-YYYY-MM-DD-xxxxxx` (set by `review new`; never change it) |
| `status` | `open` · `in-progress` · `fixed` · `verified` · `dismissed` — change with `artmind review status` |
| `stage` | `corpus-gap` · `synthesis` · `routing` · `missing-view` · `identity` · `extraction` · `schema` |
| `elements` | schema elements `domain/Class`, `domain/Class.property`, `domain/Class-REL->`; required for identity / extraction / schema |
| `trace_ids` | every trace this dossier covers |
| `private_terms` | every private name or value the dossier quotes (pre-filled from the trace; add more) — `review leakcheck` uses it |
| `graph_state` | what the first trace ran against (from the trace) |
| `created`, `updated` | dates (`updated` is set on every save) |

## Sections

| Section | Content | Required |
|---|---|---|
| Symptom | question, answer excerpt, rating and note, retrieval path (pre-filled) | always |
| Diagnosis | the stage verdict and its evidence chain: span id → rows → chunk sentence (`>` quote) → staging entry | always |
| Shape audit | findings table `| element | check | source sentence | why the question needs it |`, then the answerability pattern (JSON) | identity, extraction, schema |
| Proposal | view.yaml + SKILL.md draft · schema YAML diff · meta.yaml prompt diff · code-change spec (files, functions, behaviour) · for corpus-gap: what to ingest | always |
| Proof | `artmind views test` output for a view; `artmind review reextract` baseline and proposal output (shape diff + answerability) for a schema or prompt change | recommended |
| Synthetic example | the same failure with **invented** entities and text — the only material a coding session may put in the public checkout | all but corpus-gap |
| Regression question | exactly one `### R-<review_id> — <title>` / `**Question:**` / `**Evaluation comment:**` block | always |
| Agent prompt | starts with the privacy preamble (pre-filled; keep it verbatim), then a self-contained task: context, files to change, acceptance criteria — tests built on the Synthetic example; `artmind review leakcheck <review_id> --base master` passes; the regression question passes (after a re-ingest if the schema changed) | always |
| Log | dated lines; `review status` appends status changes | always |

## Quoting

Quote source sentences and other private text as `>` blockquotes, and put
source sentences in the Shape audit table. `review leakcheck` treats exactly
those — plus `private_terms` — as private. Your own analysis prose is not
checked, so a coding session may paraphrase it.
