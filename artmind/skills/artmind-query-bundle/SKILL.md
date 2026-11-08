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

- `evidence`: passages, each with a `doc`. This is the only thing you cite. Rows with `from: ["open"]` are text `query run` opened for you; they come first.
- `opened`: the documents `query run` read for you (`doc_id`, `title`, `why` rank or title, `passages` as evidence ids). Answer from this text.
- `documents`: one short line row per document found (`doc_id`, `title`, `date`, `opened`, `rank`), plus a `gist` on unopened rows when there was room. Use them for coverage and depth. A row is not evidence. `opened: false` means the document was listed, not read; it is a pointer, never a source.
- `open_next`: the next unopened documents by rank (`doc_ids`) and `command`, the exact call that opens them. Null when none remain.
- `facts`: rows from the graph or the SQL store. Each links to evidence by `evidence` ids.
- `guides`: topic pages and insights. They steer you to the right documents. Never cite them.
- `coverage`: counts of what ran and what was cut. Quote the numbers when the answer is incomplete.
- `path`: which steps ran, and which failed.
- `escalate`: why the bundle may not be enough, with `suggested` commands to run next.
- `truncated`, `cut`, `handle`, `next`: the printed bundle was cut. `cut` counts the rows left out per list. The whole is stored under the `handle`. Follow `next` only when it names a list; `next: null` with `truncated: true` means only gists were left out and paging adds nothing.

To page a cut list, run `artmind query page <handle> --list evidence --compact`. Use `--list documents` or `--list facts` for the others, and `--from <n>` to continue.

## Mode

Every bundle says `mode: reasoning` today. The fast path is off until a route passes its gate. Treat the bundle as your starting evidence, not as the answer.

- A bundle whose plan route is `search` or `survey` is usually close to complete. Check that every part of the question is covered, then answer.
- On other routes the bundle often misses documents. Before you answer, check each named part of the question against `evidence` and `documents`. A name, a date, a year or a count the bundle never mentions is a gap.
- Fill a gap with the commands in `escalate.suggested`, or with the route's commands in `references/routes.md`. The most common fill is `artmind query survey --shape brief --domain <d> --compact "<question>"`.
- If a later bundle says `mode: fast`, answer from it. Run a command only when a named part of the question is visibly missing.

## Read before you answer

`query run` has already opened the top documents (see `opened`). Answer from that text. Everything else in the bundle is a pointer. A `gist`, a survey line row, a `documents` row with `opened: false` or a search snippet is never enough to state a fact.

### Rule 1: a relevant pointer gets opened, never reported

If you are about to write "I only saw a gist", "from the snippet", "I did not open it" or "not read in full" about something the answer uses, stop and open it instead:

- one or more documents: `artmind query survey --shape brief --domain <d> --expand <doc_id> --expand <doc_id> --compact "<question>"` (up to 5 `--expand` per call);
- chosen chunks: `artmind query chunks --domain <d> --idList <chunk ids> --shape brief --compact`;
- the next unopened documents by rank: run `open_next.command` as it is;
- the rest of a cut list: `artmind query page <handle> --list evidence --compact` (or `documents`, `facts`) while `cut` shows rows left out of it.

A document is relevant when its title, date or gist names something the question asks about: a person, a place, an event, a year, a version. Open every relevant one, not only the top ones. Unopened documents that match nothing in the question can stay unopened; you do not need to mention them.

### Rule 2: a question names more than its words; search for the parts it implies

Before you answer, list what the question covers and check each part against the evidence. A broad subject has parts the question does not spell out:

- a festival or holiday has its days and rituals (Diwali: Dhanteras, Lakshmi Puja, Bhai Dooj; Christmas: Christmas Eve, Boxing Day);
- a trip has its legs, places and dates; a project has its phases and people; a year has its months; a family has its members;
- a policy has its versions, its exceptions and its approvers.

For each part the bundle does not cover, run a follow-up lookup with the part's own name before you answer:
- `artmind query vector-text --shape brief --domain <d> --topK 10 --compact "<part and subject, e.g. Lakshmi Puja 2024>"`;
- or `artmind query survey --shape brief --domain <d> --compact "<part>"` for a part that spans documents.

Open what they find (Rule 1). If you find nothing for a part, say so plainly, with what you searched for.

### Rule 3: counts, "every", lists over time and histories read the whole set

For these questions, read every matching entry before you count or list. Run `open_next.command` again with each new bundle's `open_next` until the matching documents are read or `open_next` is null. Never count from `documents` rows or gists.

### Rule 4: a gap in the answer is a last resort, with numbers

Report a gap only after you tried Rules 1–3, and only for what you could not read (a budget, a missing document, nothing found). Say exactly what was searched and what was not read, with numbers from `coverage`. "I did not open it" is never an acceptable gap for a document you could have opened.

Why these rules exist: the first bundle runs lost to the classic skill mainly because answers said "I only saw a gist" or "I did not open the other 40" about documents that held the answer, and did not search for the parts of a subject the question implied.

## When the bundle escalates

`escalate` is null when the plan ran clean. Otherwise it has a `reason` and `suggested` steps.

- `reason: limit`: the run hit its step limit. Later steps never ran. Run a suggested step, or page.
- `reason: deadline`: the call ran out of time. Later steps never ran. Run them yourself from `suggested`.
- `reason: step_error`: no step returned rows without an error. `path` names the failures. Try the next suggested step. Do not retry the same one.
- `reason: guard`: a check in the plan stopped it, for example no documents found or a list still cut after paging. `predicate` says which. Do not count or list from this bundle; run a suggested step.
- `reason: no_evidence`: every step ran and returned nothing. Try a suggested step, or a broader `artmind query survey`, before you say nothing was found.
- `reason: unfilled`: the plan could not fill a step (no entity or document matched the question's words). Find the right name with `artmind query entity-resolve --domain <d> --compact "<name>"`, then look it up.
- `reason: no_lookup`: the planner read the turn as needing no lookup (`answer_from: previous`, or an `action` for another skill). If you agree, answer from the conversation or hand off. If the user did ask something new, run `artmind query run` again with the question in plain words.

When the plan switched routes because its first step came back empty, `path` shows a `replace` entry and the bundle holds the second route's rows. That is not an escalation.

Run suggested steps one at a time, and read each result before the next. When the plan's route fits poorly, a different command from `references/commands.md` is fine. Keep to read-only commands.

## A worked turn

The user asks: "Which projects did Maria lead, and when did each end?"

1. Run `artmind query run "Which projects did Maria lead, and when did each end?" --compact`.
2. Read `plan`, `path`, `opened` and `coverage`. The route is an enumeration over one entity.
3. List the projects in the opened `evidence` and in `facts`. Two more appear only as `documents` rows with `opened: false` and gists naming a project: open them (Rule 1) in one call with two `--expand`.
4. This is a list over time (Rule 3): run `open_next.command` until no unopened row names a project of Maria's.
5. One project has no end date anywhere. Search for it by name (Rule 2): `artmind query vector-text --shape brief --domain <d> --topK 10 --compact "<project> end date"`, and open what it finds.
6. Answer with each project, its end date and the source. Say which end date came from a later document, and name the one still undated with what you searched.

If `truncated` is true at step 3, page first. Do not count a cut list.

## Answer contract

- Lead with the latest applicable source. Older sources follow, marked as older.
- When the answer is incomplete, say so with the `coverage` numbers. Never write "at least".
- Keep what a document states apart from what you infer from it.
- Count a relation by its direction: who did it to whom.
- Answer at the widest granularity the evidence supports, then narrow.
- If the plan narrowed the scope (the `scope` key), say which scope you answered in.
- Do not cite a row whose `gist_source` is anything other than `passage`, and do not cite a `documents` row with `opened: false`. Open the text and cite that.
- Do not cite `guides`.
- When your own commands changed the answer, ground it in what they returned, not in the bundle.
- When `escalate.reason` is `limit`, `deadline` or `step_error`, say what was not read.
- Never write "I only saw a gist", "from the snippet" or "I did not open it" about a document you could have opened. Open it, then answer.
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
- Do not follow `next` when it is null.
- Do not run a command twice with the same arguments.
- Do not write to the graph, the vault or the store from a query turn.
