---
name: artmind-review
description: Review recorded artmind query traces (admin console and benchmark) and turn a failure into a diagnosed review dossier — the failing pipeline stage with cited evidence, a graph-shape audit, a proposal (view, schema change, prompt change or code change), a synthetic example, a regression question and a privacy-guarded agent prompt. Use for "review the last queries", "why did this answer fail", "triage traces", "open a review for trace X", or "what should we fix in extraction or the schema". Run from Claude Code in the artmind checkout with ARTMIND_VAULT set; never from the chat or admin UI.
---

# artmind Review

You turn one recorded query turn into one **review dossier**: a markdown file in
the vault (`<vault>/.artmind/reviews/<review_id>.md`) that says **which stage of
the pipeline failed**, **proves it with evidence**, and **proposes the fix**. A
human applies fixes — you never do.

Read `references/stage-walk.md` before your first verdict and
`references/dossier-format.md` before filling a dossier.

## Setup

Run from the artmind code checkout, pointing artmind at the vault:

```bash
export ARTMIND_VAULT=<path to the vault>
artmind traces list --limit 5        # confirms the trace DB is the vault's
```

`artmind traces …` and `artmind review …` read this machine's trace DB and the
vault's reviews folder. Query commands (`artmind query …`) hit the live graph.

## Ground rules

1. **Privacy.** A dossier holds private corpus data (names, quotes, values). The
   checkout you are running in is a **public** repo. Never write dossier
   content, names, quotes or values into any file in the checkout, a commit
   message, an issue or a PR. Never publish a dossier anywhere — it lives only
   in the vault. Quote source text in the dossier as `>` blockquotes, and add
   every private name or value you quote to the frontmatter `private_terms`.
2. **Evidence or no verdict.** Every verdict cites at least one span id, result
   row, chunk sentence or staging entry. "Probably" is not a verdict.
3. **Cheapest stage first.** Walk the stages in the fixed order and stop at the
   first that failed. Blame the schema last: a schema change forces a re-ingest.
4. **Shape matters.** For identity, extraction and schema verdicts, compare the
   source text, the extracted subgraph and the question (the shape audit).
5. **One problem, one dossier.** Before opening a dossier, check
   `artmind review list --element <element>` for an open dossier on the same
   schema element; if one exists, add this trace to it (append the symptom,
   add the trace id to `trace_ids`) instead of opening another.
6. **Propose, never apply.** No graph writes, no schema edits, no code edits,
   no `artmind update`. The dossier's Agent prompt is for a later coding session.

## Flow

1. **Pick.** `artmind traces triage --since 14d` lists turns worth reviewing
   (rated wrong first, then partial, then unrated turns by signal score; graph
   misses rated good last). Take the top one unless the user named a trace.
2. **Load.** `artmind traces show <trace_id>` — the question, answer, rating
   and `expected` note, every agent tool call with the CLI spans it ran
   (command, args, rows, evidence ids), the retrieval path, `fell_through` and
   `answer_basis`. A warning about missing spans means a stale `serve`
   daemon recorded nothing; say so and stop.
3. **Scaffold.** `artmind review new <trace_id>` writes the dossier with the
   Symptom, Regression question, Agent prompt preamble and Log pre-filled and
   prints its path. Edit that file for the remaining steps.
4. **Ground truth.** Find the chunks a correct answer rests on — the
   procedure is in `references/stage-walk.md` ("Ground truth"). None at all →
   verdict `corpus-gap`.
5. **Walk the stages** (`references/stage-walk.md`): synthesis → routing →
   missing-view → identity → extraction → schema. A **graph miss**
   (`fell_through` with `answer_basis: vector`) has its own entry point.
6. **Shape audit** for identity / extraction / schema verdicts — the checklist
   and the staging lookup are in `references/stage-walk.md`.
7. **Fill the dossier** (`references/dossier-format.md`): set `stage` and
   `elements` in the frontmatter, then Diagnosis, Shape audit, Proposal, Proof
   (for a view: run `artmind views test`), Synthetic example, the Regression
   question's title and evaluation comment, and the Agent prompt task.
   Add every private name or value you quoted to `private_terms`.
8. **Validate.** `artmind review validate <review_id>` must report `ok: true`.
   Fix every error; read the warnings.
9. **Report** to the user: the review id, the verdict in one sentence, the
   proposal in one sentence, and the dossier path. Do not paste dossier
   content beyond that.

## Commands

| Command | Use |
|---|---|
| `artmind traces triage [--since 7d]` | turns worth reviewing |
| `artmind traces show <trace_id>` | one turn's full evidence chain |
| `artmind traces stats --by week` | fall-through and graph-miss trends per domain |
| `artmind review new <trace_id> [--stage …] [--element …]` | scaffold a dossier |
| `artmind review list [--status] [--stage] [--element]` | dossiers in the vault |
| `artmind review show <review_id> [--markdown]` | one dossier |
| `artmind review validate <review_id>` | completeness for its stage |
| `artmind review status <review_id> <status> --note "…"` | open → in-progress → fixed → verified, or dismissed |
| `artmind review leakcheck <review_id> --base master` | run in a coding session before committing |
| `artmind query chunks --domain D --idList <id> --expand 1` | source text of a chunk and its neighbours |
| `artmind query entity-context --domain D --entityId <id>` | an entity's properties, relationships and source chunks |
| `artmind query vector-text --domain D "<text>"` | find the chunks that state a fact |
| `artmind query views list --domain D` / `artmind views test …` | existing views / prove a drafted view |

`review shape` and `review reextract` (automatic shape bundles and dry-run
re-extraction) arrive in a later phase; until then do the shape audit by hand.

## Failure modes

- **No spans in the trace** — the `serve` daemon predates trace capture;
  restart it and ask the user to re-run the question.
- **The graph changed since the trace** — compare `graph_state` in the Symptom
  with `artmind vault status`; re-check the evidence against the live graph
  before blaming extraction.
- **You can't decide between two stages** — pick the cheaper (earlier) one,
  and say in the Diagnosis what evidence would move it later.
