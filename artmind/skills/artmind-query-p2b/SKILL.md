---
name: artmind-query-p2b
description: The artmind system stores information from ingested documents. Using this skill respond to natural-language questions from the user given a particular domain. These questions would be in the form of Q&A between the user and artmind. Every turn: orient first (`artmind query orient`), then write the question's parts in the corpus's terms (a `Parts:` list), then run one retrieval loop per part, never one search for the whole question.
---

# artmind Query

Use this skill to answer user questions over an artmind domain through the deterministic query CLI. The skill provides the reasoning layer; the CLI provides saved views, templated graph/vector retrieval and JSON output.

## Every turn, in this order

1. **Orient** — `artmind query orient --compact` (and `--domain` for the domains you pick). See below.
2. **Write the parts** — before any retrieval command, write a `Parts:` list in your reply: one
   line per thing the answer must state, each restated in the corpus's terms from orient, with
   its domains and first command. A one-part question gets one line. See "Plan the parts".
3. **One loop per part** — each part gets its own searches, with only that part's terms in the
   query text. Never put two parts, or a guessed answer, into one `survey` or `vector-text` call.
4. **Answer part by part** — every part from its own evidence; say which part has none.

## Orient — read the corpus first

`orient` describes the corpus in two peels. It never sees the question: you match the
question against what it says.

1. `artmind query orient --compact` once per conversation. Each card says what a domain
   is (`purpose`), what one document is (`unit`), whose voice it is (`voice`: "I" is the
   narrator), how documents are dated (`time`), its stores and its `vocabulary` (top classes).
   `tables` lists the structured tables registered at that domain (rows, bridge classes);
   a card with `tables_at: <domain>` has none of its own but that domain's tables apply to it.
   A question about specific records (a customer, a case, a count, "which/how many") goes to
   those tables (`references/structured-store.md`), even when the wording does not name a table.
2. Pick the domains the question touches from `purpose`, `vocabulary` and `family`. A
   collapsed family row opens with `--domain <family>`; in peel 2 open only the children the
   question needs (a whole family is cut hard). Policies and the SOPs about the same subject
   often sit in sibling domains: take both when the question spans them.
3. `artmind query orient --domain <d> [--domain <d2>] --compact` for those domains. It lists
   classes (definition, `types` with counts, `dated` share), relation shapes
   (`from -rel-> to`), structured tables, saved views, topic plans, insights and superseded
   documents.
4. A saved view whose `summary` or `examples` fit the question → `views show` / `views run`
   (the routing table below). Otherwise choose the command from the classes, types, relation shapes and stores,
   and spell `--entityClass`, type values and relation names exactly as orient listed them. A
   `chunks-only` card has documents and chunks but no entities: skip views, insights, entity and pattern commands there:
   - a fact → `artmind query vector-text --shape brief --domain <d> --topK 10 --compact "<question>"`;
   - a question across documents → `artmind query survey --shape brief --domain <d> --compact "<question>"`;
   - a whole document → `artmind query graph pattern10 --documentName "<name>"`; more context
     around a hit → `artmind query chunks --shape brief --idList <id> --expand 1`;
   - in a mixed set, leave chunks-only domains out of graph commands but keep them in the
     shared `vector-text` call. Tables listed under `structured` still apply.
5. Orient again only when the user moves to a new area. A part named in `warnings` was not
   read: never treat it as empty. `null` means declared absent (no narrator, no snapshot).
6. Every result's `next`, `cuts` and `warnings` matter as much as its rows: see "Read every
   result before you answer".

## Plan the parts — after orient, before any retrieval

Rewrite the question before the first retrieval command. Write the plan in your reply, one
line per part:

```
Parts:
1. <the part in the user's words> → <the same part in the corpus's terms> · <domains> · <first command>
2. ...
```

- **A part is one thing the answer must state:** one who / what / when / which, one side of a
  comparison, one condition. "Who owns the fix, who handles the customer, who must be told" is
  three parts. A one-part question gets a one-line plan; do not invent parts.
- **Restate each part in the corpus's terms.** Take class names, type values, definitions and
  `vocabulary` from the orient cards and peel 2. When the user's word is not the corpus's, use
  the corpus's ("online banking" → the platform or system the cards list; "kept informed" →
  notification, escalation, oversight). Keep names the user gave verbatim for `entity-resolve`.
- **Choose the command per part**, from orient rule 4 and the routing table: a record part goes
  to the tables, a fact part to the graph or `vector-text`, a part spread over documents to
  `survey`. Parts of one question often need different commands.
- **Run each part as its own loop:** a search whose question holds only that part's terms (never
  two parts, never a guessed answer), then read every result, follow `next`, rephrase once in the
  corpus's terms if the result is thin, and stop when the part has evidence. A result that also
  answers another part counts for that part too: note it and do not search that part again. A
  `survey` row whose `gist` speaks to an open part is the next document to open.
- **Before answering,** check that every part has its own evidence. Answer in the parts' order,
  and for a part with none, say what you searched.
- **Follow-up turns:** rewrite the new question with what the conversation already settled
  (resolved names, domains, dates) before planning its parts.

## Read every result before you answer

Every `artmind` command returns the same envelope: `command`, `warnings`, `next`, then the
data. The command knows things about its own result that you cannot see from the rows: what
it cut, what it could not read, what is incomplete. Treat those fields as part of the
evidence, after every call:

1. **Is the result whole?** Look for `complete: false`, `truncated: true`, `hub: true`,
   a `cut` block, `cuts[]`, or `rows_total` above the rows shown. Any of these means the
   rows are a sample, not the set.
2. **Does the question need the whole set?** "Which", "how many", "every", "all", "list",
   "each year", a total or a comparison across members all do. A single fact usually does not.
3. **If the set is partial and the question needs all of it, run the first `next` (or the
   cut's `rest`) before answering.** `next` holds runnable commands separated by `; `, the
   ones that act on this result first. Run them as written (copy ids, handles and flags
   verbatim; fill only a `<placeholder>`, from orient or the question). Do not count, rank
   or say "none" from a partial set.
4. **Empty or thin result:** run `next` when it names a different command for the same
   question; otherwise follow the Fallback Ladder.
5. **`warnings`:** a part named there was not read. Never treat it as empty, and say so in
   the answer if it limits what you can claim.
6. **Stop** when the result is whole for what the question needs, or `next` is null, or the
   same `next` comes back twice. Then say what you could not cover, and why.

`next` is a fact about this result, never a guess about your question: it is safe to follow
and safe to skip when the question does not need what it offers.

**`guide` is an instruction.** A result with `guide: ["hubs"]` (and a `guide:` line in `warnings`) means:
open `references/hubs.md` in this skill's folder with the Read tool now, before your next command, then
continue. Once per file per turn; never answer before reading a file a `guide` named this turn.

## Running commands

On the grounded Q&A surfaces a shell command is allowed only as a single `artmind ...` invocation with nothing before or after it: no `cd`, `&&`, `;`, pipes, redirects or `$(...)`. The working directory is already the vault, so never `cd` into it. A refused command is not a reason to stop: re-run it bare.

`pattern*`, `text2cypher`, `timeline`, `conflicts`, `hierarchy`, `metadata`, `structural-metadata` and
`entity-listing` sit under `artmind query graph`; every other query command under `artmind query`. Every option
of every command is in `references/commands.md`.

## Grounding Rule

Use only the structured KG data and chunk text returned by artmind query commands. If the data is insufficient, say so clearly. Do not invent entities, events, relationships, motivations, or source details not present in the returned data.

Any stderr line starting `artmind: ` about a store being behind, unreachable, not pulled, or unable to sync — e.g. `artmind: graph is N docs / M tables behind the vault`, `artmind: structured store is M tables behind the vault`, `artmind: the graph store's sync bookmark is not in this clone's history -- let Obsidian Git pull...`, or `artmind: the graph store's sync would fail -- ...` — is advisory, not an error and not query data: the answer may miss recently pulled documents, or the vault owner may need to pull or fix something before syncing. Tell the user once, in your own words; never run `vault sync` (or any of its flags) yourself.

## Required Inputs

- `domain`: Ask for it if the user did not provide one.

## Views and routing — question shape → command → reference

Match every question against the saved views `orient --domain` listed first: a view that fits answers in one call.
Otherwise apply the four rules, then pick a row for each part. Every command needs `--domain <d>` and `--compact`.

1. **Resolve first.** For every person, place, organisation or other named thing in a part, run
   `artmind query entity-resolve --domain <d> --topK 5 --compact "<name>"` before any other command for that part,
   and use the chosen `_id` from then on (`--entityId`). A question about one entity then goes to `entity-context`
   with that id and the question.
2. **Records go to `text2sql` first.** A part that counts, sums, groups, ranks or compares records in a domain whose
   orient card lists structured tables, or asks how many / which people, places, event types or periods in a domain
   whose card lists `mart`, starts with `artmind query text2sql "<part>" --domain <d> --compact`. Mart rows are a
   lower bound: run the survey its `next` gives and answer from both.
3. **Survey before `vector-text`.** A part whose answer can sit in more than one document (a policy or rule, how
   something works, every time, each year, what changed, across the notes) starts with
   `artmind query survey --shape brief --domain <d> --compact "<part>"`. Use `vector-text` for one passage in one known
   place, or to look closer after a survey.
4. **Read what you already have.** When a result gives chunk ids (`doc_sources`, `chunk_id`, evidence), read them with
   `artmind query chunks --shape brief --idList <id>[,<id>…] [--expand 1]` instead of searching again; when a result
   was cut, read the rest (`artmind query page <handle>`, or the cut's `rest`) before answering.

| Question shape | First command | Reference |
|---|---|---|
| A saved view's `summary` or `examples` describe it | `artmind query views show <name>`, then `artmind query views run <name> --param <p>=<value> --render markdown` | `views.md` |
| One entity: tell me about X, X's role, why X … | `artmind query entity-context --shape brief --entityId <id> --query "<question>"` | `hubs.md` |
| Text of chunk ids you already have | `artmind query chunks --shape brief --idList <id>` | `hubs.md` |
| Spans documents: every time, each year, a trend, across the notes | `artmind query survey --shape brief [--by year] "<part>"`, or `artmind query topics run <slug>` when orient lists a plan for it | `survey.md` |
| One passage in one known place: where, when or how something happened; quotes | `artmind query vector-text --shape brief --topK 10 "<part>"` | `hubs.md` |
| Counts, sums or groups over records, or a card lists `mart` | `artmind query text2sql "<question>"` | `structured-store.md`, `composites.md` |
| A code, id or key value held in a table | `artmind query resolve-key` | `structured-store.md` |
| Entities of a class; the main or top ones | `artmind query graph pattern1 --shape brief --entityClass <LABEL>`; `artmind query graph pattern9 --entityClass <LABEL> --topN 5` | `graph-model.md` |
| Which or how many X did Y … | `artmind query graph pattern8 --entityClass X --entityId <id> --via mentions --all` | `graph-model.md` |
| How X and Y are related | `artmind query graph pattern6 --entityId1 <id> --entityId2 <id>`, then `artmind query graph pattern5 --mode shortest` | `graph-model.md` |
| Entities by a name fragment | `artmind query graph pattern7 --shape brief --searchTerm "<fragment>"` | `graph-model.md` |
| All chunks of a document, or its summary | `artmind query graph pattern10 --documentName "<name>"` | `graph-model.md` |
| Aggregations or filters no row above covers | `artmind query graph text2cypher --dryRun "<question>"`, then without `--dryRun` | `graph-model.md` |
| Everyone under X, a reporting line, a chain of command | `artmind query graph hierarchy --entityId <id>` | `hierarchy.md` |
| X's value before it changed, or at a date | `artmind query entity-history --entityId <id> [--property P] [--asOf <date>]` | `temporal.md` |
| What changed when a document was edited; an earlier version | `artmind query document-history --documentName "<name>"` | `temporal.md` |
| A sequence of events across the domain, by date | `artmind query graph timeline` | `temporal.md` |
| Sources that may disagree; comparing claims | `artmind query graph conflicts` | `conflicts.md` |
| Broad or retrospective: what stands out, what changed over time | `artmind query insights list` | `insights.md` |
| What the corpus already answers | `artmind query assets`; `artmind query catalogue` | `insights.md` |
| Orient was cut and a `cuts` entry names a `rest` | the `rest` command | `graph-model.md` |

## Fallback Ladder

The ladder starts after the views check in the routing table: a view that fits is tried before any rung below.

1. Thin results → re-run with sibling domains from orient before concluding data is absent.
2. Pattern output empty or too thin → vector-text.
3. A question that spans documents, whose `vector-text` rows come from one or two documents → `query survey`.
4. vector-text sparse or weak → state that the available artmind data does not answer the question.

Judgement, whatever the command:

- A count, a list or a ranking needs the whole set: answer it from `complete: true`, or say N of M.
- Never say a thing is absent from a graph result alone: search the text (`vector-text`, `survey`) first.
- Retired documents never appear in search; a dated or 'what changed' question also reads `references/temporal.md`.
- Two sources that disagree are both stated, each with its document; never blend them.
- Say plainly which part the data does not answer, and why (empty, partial, not read).

## Answer Style

Answer directly and naturally. An event counts for a person only when the notes put them in it. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.

If an answer came from reading a note directly (possible on the admin console) rather than from
`artmind query` output, say plainly that the graph does not hold it, and suggest the operator check
`artmind ingest empty-extractions` and re-extract the note — a fact only the note holds is missed by every later query.

### Provenance in web UIs

Ground every claim in the query output and name the relevant document naturally in
the answer (for example, "per Delivery Leads.md"). Do **not** create a `### Sources`
section or calculate a link target: chat-ui and admin-ui append a deterministic,
passage-level source block from the final query trace after the turn completes.

Never write an `obsidian://` URI or invent a file path. If an interface that does not
have automatic provenance needs citations, its host owns that presentation policy.
