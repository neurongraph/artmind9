---
name: artmind-query-bundle
description: Answer natural-language questions over artmind domains by calling `artmind query run` once per lookup turn and reading the evidence bundle it returns. The bundle-first query contract (P3), chosen by the admin console or a benchmark run. Use it for any question about the content of the ingested documents, graph or tables.
---

# artmind query, bundle contract

One command does the lookup: `artmind query run`. It plans the question, runs the commands, and returns one evidence bundle. You read the bundle, check it against the question, and answer.

## Grounding

Cite only what the bundle's `evidence` holds and what commands you ran returned. If the data is insufficient, say so. Do not invent entities, dates, motives or sources.

A shell command is allowed only as a single `artmind ...` invocation: no `cd`, `&&`, `;`, pipes, redirects or `$(...)`. The working directory is already the vault.

A stderr line starting `artmind: ` about a store being behind or unreachable is advisory. Tell the user once, in your own words. Never run `vault sync` yourself.

## Does this turn need a lookup?

Look up when the user asks about a new subject, says "check again", or asks a follow-up that needs more than you already hold.

Do not look up when the turn is:

- a reshape of your last answer (shorter, a table, another language);
- a deliverable built from the conversation (a file, a table, an email);
- a curation hand-off (use the curate or update skills);
- a status check or an ops request.

If the turn needs no lookup, answer from the conversation and say so.

## The one call

Run `artmind query run "<question>" --compact`. Use the user's own words as the question.

- Add `--domain <d>` only when the user named a domain.
- Never pass `--previous`. The UI hands the previous turn to the command for you.
- Never call `artmind query plan` yourself. `query run` plans for you.

## Reading a bundle

Each key has one job:

- `evidence`: passages, each with a `doc`. This is the only thing you cite.
- `documents`: brief rows of the documents found. Use them for coverage and depth. A row is not evidence.
- `facts`: rows from the graph or the SQL store. Each links to evidence by `evidence` ids.
- `guides`: topic pages and insights. They steer you to the right documents. Never cite them.
- `coverage`: counts of what ran and what was cut. Quote the numbers when the answer is incomplete.
- `path`: which steps ran, and which failed.
- `escalate`: why the bundle may not be enough, with `suggested` commands to run next.
- `truncated`, `handle`, `next`: the printed bundle was cut. The whole is stored under the `handle`.

To page a cut list, run `artmind query page <handle> --list evidence --compact`. Use `--list documents` or `--list facts` for the others, and `--from <n>` to continue.

## Mode

Every bundle says `mode: reasoning` today. The fast path is off until a route passes its gate. Treat the bundle as your starting evidence, not as the answer.

- A bundle whose plan route is `search` or `survey` is usually close to complete. Check that every part of the question is covered, then answer.
- On other routes the bundle often misses documents. Before you answer, check each named part of the question against `evidence` and `documents`. A name, a date, a year or a count the bundle never mentions is a gap.
- Fill a gap with the commands in `escalate.suggested`, or with the route's commands in `references/routes.md`. The most common fill is `artmind query survey --shape brief --domain <d> --compact "<question>"`.
- If a later bundle says `mode: fast`, answer from it. Run a command only when a named part of the question is visibly missing.

## Read before you answer

A `gist`, a survey line row, a `documents` row or a search snippet is never enough to state a fact. It only tells you where to look. Open the text first:

- `artmind query survey --shape brief --domain <d> --expand <doc_id> --compact "<question>"` opens a document.
- `artmind query chunks --domain <d> --idList <chunk ids> --shape brief --compact` reads chosen chunks.
- `artmind query page <handle> --list evidence --compact` reads the rest of a cut bundle.

For a count, an "every", an "each year" or an "all the" question, read every matching entry the bundle lists before you count. Page until `truncated` is false.

If you did not read something the question needs, read it now. Say what you did not read only when you cannot read it (a budget, a missing document). Then say exactly what was skipped and why, with numbers from `coverage`.

Why this rule exists: on the same build, the same agent stopped at search snippets on its second run and lost 6 of 14 questions that its first run answered.

## When the bundle escalates

`escalate` is null when the plan ran clean. Otherwise it has a `reason` and `suggested` steps.

- `reason: limit`: a step hit its row or step limit. Rows are missing. Run a suggested step, or page.
- `reason: deadline`: the call ran out of time. Later steps never ran. Run them yourself from `suggested`.
- `reason: step_error`: a step failed. `path` names it. Try the next suggested step. Do not retry the same one.
- `reason: guard`: the plan switched to another route because the first came back empty or cut. The bundle holds the second route's rows.
- Any other reason: the plan was unsure. Check the question against the route in `references/routes.md`.

Run suggested steps one at a time, and read each result before the next. When the plan's route fits poorly, a different command from `references/commands.md` is fine. Keep to read-only commands.

## A worked turn

The user asks: "Which projects did Maria lead, and when did each end?"

1. Run `artmind query run "Which projects did Maria lead, and when did each end?" --compact`.
2. Read `plan`, `path` and `coverage`. The route is an enumeration over one entity.
3. List the projects in `evidence` and `facts`. Count them. Look for an end date beside each one.
4. Two projects have no end date in the bundle. Open their documents with `artmind query survey --shape brief --domain <d> --expand <doc_id> --compact "<question>"`.
5. Answer with each project, its end date and the source. Say which end date came from a later document.

If `truncated` is true at step 3, page first. Do not count a cut list.

## Answer contract

- Lead with the latest applicable source. Older sources follow, marked as older.
- When the answer is incomplete, say so with the `coverage` numbers. Never write "at least".
- Keep what a document states apart from what you infer from it.
- Count a relation by its direction: who did it to whom.
- Answer at the widest granularity the evidence supports, then narrow.
- If the plan narrowed the scope (the `scope` key), say which scope you answered in.
- Do not cite a row whose `gist_source` is anything other than `passage`. Open the text and cite that.
- Do not cite `guides`.
- When your own commands changed the answer, ground it in what they returned, not in the bundle.
- When `escalate.reason` is `limit`, `deadline` or `step_error`, say what was not read.
- Do not add a Sources section. The UI adds provenance.

## References

Read a reference only when the question needs it.

- `references/routes.md`: what each route runs. Read it when a bundle misses part of the question.
- `references/commands.md`: every query command and its options. Read it before you run a command not listed here.
- `references/temporal.md`: "as of", "before", "changed since" and other time questions.
- `references/conflicts.md`: two sources that disagree.
- `references/hierarchy.md`: parents, children and org structure.
- `references/structured-store.md`: questions answered by tables, counts and sums.

## What not to do

- Do not run `artmind query plan`.
- Do not run shell loops over commands. For a batch of claims, `artmind query run --each` exists.
- Do not pass `--previous`.
- Do not answer a count or a list from `documents` rows alone.
- Do not run a command twice with the same arguments.
- Do not write to the graph, the vault or the store from a query turn.
