# Planner v2: intents, slots, plan programs and the `query run` executor

Date: 2026-11-05. Status: **spec for review**, phase P2 of
`2026-11-02-retrieval-curation-architecture-design.md` (§5.3–5.5, revised from round 7), with
the executor (P3's `query run`) specified here too so the plan format and its consumer are
designed together. P2 ships the planner in shadow; P3 switches the executor on. Inputs: round-7
lessons (`docs/query-routing-study/12-round7-lessons.md`, answers N1–N9 in §3), the P2 evidence
needs (`11-p2-evidence-needs.md`), the bounded-output design (`2026-11-05-bounded-output-design.md`,
built as P1b), and v1 as built (`2026-10-22-query-plan-design.md`, `artmind/query_plan/`).

**Written before round 8, checked against it the same day.** Every number that round 8 was to
confirm is a named setting with a default, listed once in §11 with the measure that decides it.
Round 8 (`docs/query-routing-study/02-trace-evidence.md`, P55–P63) confirmed the bounded-output
base the bundle builds on and kept every §11 default (too few ratings per route to move them);
its design consequences are folded in below and marked R8.

Privacy rule as in the study folder: shapes, counts and invented examples only.

---

## 0. In one page

v1 answers "which route, which domain, which view" and prints command lines for the agent to
run. Round 7 showed the agent then struggles mechanically: it composes lines wrongly, loops one
command over a list, fills the search slot with the wrong words and resolves names to the wrong
node (P35–P39), and in about half of retrieval turns the struggle, not the question, is the
problem. v2 changes three things:

1. **Intent first, with the previous turn as input.** Eight intents (`retrieve`,
   `follow_up_format`, `follow_up_why`, `curate`, `update`, `meta`, `status`, `ops`). Only
   `retrieve` (and `follow_up_why`, as a re-run) leads to retrieval. A context-only reply
   ("yes", "go on") inherits the previous turn's offer.
2. **Slots in code.** The planner, not the agent, decides who "I" is, what the search term is,
   which category members a class word means, which twins an entity has, and which documents
   are in scope. Plan confidence includes how well the slots were filled.
3. **Plans are programs; code runs them.** A plan is a small typed program (`run`, `par`,
   `map`, `pick`, `page`, guards) built from templates in `routes.yaml`. `artmind query run`
   executes it in process and returns one **evidence bundle** within the output budget. The
   agent writes the answer from the bundle (fast path) or, when the bundle escalates, falls
   back to today's reasoning path with the bundle as context.

The contract is additive: `query plan` keeps every v1 field (`plan_version: 2` adds fields), so
shadow traces, replay and the admin console keep working while v2 is measured.

## 1. Scope

| In | Out |
|---|---|
| Intent classification and the previous-turn input | The admin UI's plan card and evidence panel (P4) |
| Slot filling: narrator, search term, category expansion, twins, document scope, date window | The gap classifier and proposal files (P5); P2 only emits `gaps` in the bundle |
| Asset match over views, topic pages and insights (thresholds from N2) | New retrievers or ranking changes |
| Plan programs: grammar, guard vocabulary, templates per route | The question bank merge (architecture §5.9); replay keeps reading `plan_labels.yaml` |
| `query run`, the evidence bundle, the batch form `--each` | A learned classifier; the decision model stays as built, in shadow |
| Domain scope rules and calendars in `domains/plan/<domain>.yaml` | Schema changes for relation direction (lesson 40) |
| Skill diet to the bundle contract (P3 part) | |

## 2. Where v1 stands (as built)

`artmind/query_plan/planner.py` `plan()` (723 lines): domain overview → catalogue (cached,
`artmind/catalogue/`) → mentions (first person, gazetteer) → one batched classifier call
(`domain?`, `view?`, `route`) → entity resolution by name evidence → view match with parameter
filling, or a route's templates → confidence = the weakest stage against
`confidence_threshold` (default 0.7) → `steps` (high) or `suggested_steps` (low) → optional
`--execute` of one view. 18 routes in `routes.yaml` (existence … search), each with triggers,
`needs`, steps, fallbacks (`zero_rows`, `truncated`, `no_document`), evidence ids. Shadow mode
plans every admin/benchmark turn in a background thread (`shadow.py`). Twins are detected for
view parameters only (`planner.py` `twins` in `view_match`); entity steps take one candidate.

What v2 keeps: the catalogue, mention finding, the classifier seam (`classify/`), the
decision batching, entity resolution by name evidence, view parameter filling, the routes file
as the one routing table, shadow mode, replay. What it replaces: the flat step list (with a
program), the weakest-stage confidence (with route × domain × slot quality), and the agent
composing commands (with `query run`).

## 3. Intent

### 3.1 The intents

| Intent | Shape (invented examples) | Plan | Retrieval? |
|---|---|---|---|
| `retrieve` | any question about the corpus | §4–§6 | yes |
| `follow_up_format` | "make that a table", "shorter", "add the dates" | `program: []`, `answer_from: previous` | no |
| `follow_up_why` | "why did you miss X", "are you sure", "what about Y" | the previous plan re-run with `must_find: [X]` (§6.6) | yes |
| `curate` | "X and Y are the same", "that is wrong", "dismiss these" | a curation action proposal (§8.3) | no |
| `update` | "remember that…", "add this fact" | route to artmind-update | no |
| `meta` | "which model are you", "what can you do" | `program: []` | no |
| `status` | "is it done", "how far is the ingest" | the job/status read (`ingest job-status`, `traces show`) | no (a read) |
| `ops` | "ingest this folder", "re-extract that document" | an ops proposal with the command's `--dryRun` effect | no |

**Revised 2026-11-06: the agent decides whether a turn needs a lookup.** The agent harness holds
the conversation and handles reshapes, deliverables, curation hand-offs, status checks and
pipeline runs itself; it calls `query run` only for turns that need a lookup, follow-ups
included (architecture §5.5, 4a). So the non-retrieval rows above are a safety net: such a turn,
if it reaches the planner, plans no lookup. Replay grades a non-retrieval label only on that
(no confident lookup); which non-retrieval intent was chosen is recorded, not graded.

### 3.2 Inputs

The intent decision reads the question **and** a `previous` block (new `--previous` option on
`query plan`/`query run`, a JSON file or `-`; the admin console and chat UI fill it from the
conversation):

```json
{"intent": "retrieve", "route": "survey", "question": "…", "offer": "run a full survey" | null,
 "entities": [{"id": "…", "name": "…"}], "documents": ["…"], "open_job": "…" | null}
```

- `offer` is what the previous answer proposed (the bundle's `escalate.suggested`, or an
  `offer` line the agent wrote, §7.4). A **context-only reply** (no content tokens after
  stopwords, or an affirmative from a fixed list: yes, ok, go on, do it, please) inherits the
  offer: intent `retrieve` with the offered program, or the previous intent if there was none.
- `open_job` is set when a background job started in the conversation is still running; a
  question with status cues then classifies as `status` before `retrieve`.
- `entities`/`documents` let a follow-up with a pronoun ("what did she say next") resolve to
  the previous turn's subjects (§4.6).

Without `previous` (CLI, scripts, the first turn), the intent is decided from the question
alone, as v1 decides everything today.

### 3.3 Classifier

A new `intent` decision in the existing batched call, ahead of `route` (one call, not two).
Lexical cues per intent live in `routes.yaml` under a new top-level `intents:` key, with the same
trigger syntax (anchors with `^`). The intent cues observed in round 7 catch about 15 of 17
non-retrieval turns in the chunks-only vault (N1); the list itself is drafted from the private
analysis into the vault's labels, not into this checkout. The decision model sees the intent
options with their descriptions, as for routes. Lexical stays the default classifier (N7).

When intent ≠ `retrieve`, the `route` decision is still taken (it costs nothing in the same
call) and recorded, so replay can tell an intent error from a route error.

## 4. Slots

A **slot** is a value a step template needs. v1 fills `{domains}`, `{question}`,
`{entity_name}`, `{keywords}`, `{document}` and view params; the agent fills the rest by
improvising. v2 fills every slot in code and scores how well it did.

### 4.1 Narrator (lesson 7, 28; P36)

`domains/plan/<domain>.yaml` `narrator: {entity_class, name}` (exists). v2 adds:

```yaml
narrator:
  entity_class: Person
  name: "<canonical name>"
  aliases: ["<the owner's own name as written in notes>", "<nickname>"]
```

First-person mentions ("I", "me", "my", "we" per `first_person`) and any alias resolve to the
narrator's entity, never to a name-matched stub. A stub entity named like the narrator but
distinct from it is reported in `gaps` as `identity` (curation merges it; P5). The agent never
resolves "I".

### 4.2 Search term (lesson 9; P39)

`{search_term}` replaces the agent's choice of words for `pattern7`, `vector-text` and survey's
keyword legs:

1. Start from the user's mention spans, not the top entity's canonical name.
2. Strip question filler: a fixed list (what, which, did, tell me about, all the, …) plus
   stopwords (`sanitize_lucene_query(drop_stopwords=True)`).
3. **Expand a class word to its members** (§4.3).
4. The guard `rows_below_k` (§5.3) retries with the expansion when the first search returns
   fewer than k rows.

The slot is empty when nothing survives step 2; an empty search term lowers slot quality (§4.8)
and the step falls back to `{question}`.

### 4.3 Category expansion (lesson 9; R7-2, R7-3)

A class word in the question ("doctors", "illnesses", "trips") is matched against the
catalogue's classes and, per domain, an optional `categories:` map in plan config:

```yaml
categories:
  doctor: [Doctor, Dentist, Physiotherapist, "assessment"]   # class names or plain terms
  illness: [Illness, Ailment, "headache", "dental"]
```

Expansion yields (a) the entity classes for graph steps (`--entityClass` repeated) and (b) the
terms OR-ed into `{search_term}`. Without a `categories` entry, a class word expands to the
class whose name or schema description it matches, and nothing more: schemas declare no class
hierarchy today, so sub-types come only from `categories` (or, later, a curation proposal that
writes it). Granularity default: the widest the question
allows (lesson 42); a question naming a level ("which towns") narrows to it.

### 4.4 Twins and identity (lesson 8; N4; P37)

For every resolved entity, `twins` = other entities of the same class whose name matches by:
casefold equality; with and without surname (a first name alone matches a full name **only**
when they share a linked spouse/partner, R7-4); kinship variants from `insights/aliases.yaml`
plus `domains/plan/<domain>.yaml` `kinship:` extension; an open same-as proposal touching it.
The plan's `entities[].twins` (empty in v1 for non-view steps) is filled, entity steps take the
`union` over the twin group (§5.1), the bundle reports the split in `warnings`, and `gaps`
files an identity item. The plan never asks the user to choose between twins.

### 4.5 Scope (lessons 35, 39)

`scope` is a plan input, visible in the plan and the bundle:

```json
{"domains": ["…"], "exclude_documents": ["<doc id>", …], "rules": ["we = family", "no work events"]}
```

- `--domain` as today; `--exclude <doc id|glob>` (new, repeatable) on `query run`.
- `rules` come from plan config:

```yaml
scope_rules:
  - {id: we_is_family, when: {mention: "we"}, set: {participants: family}}
  - {id: no_work_events, when: {not_cue: ["work", "office", "client"]}, exclude_classes: [WorkEvent]}
  - {id: third_party_friction, when: {route: relationship}, require_participant: narrator}
```

  Rules are filters the executor applies to graph steps (class filters, participant
  constraints) and states in the bundle; they are never prompt text. The rule kinds are a fixed
  set (`set`, `exclude_classes`, `require_participant`); a new kind is a code change.

### 4.6 Follow-up subjects

A follow-up with a pronoun or no mention, and intent `retrieve`, takes the previous turn's
`entities`/`documents` as its mentions (marked `via: previous`).

### 4.7 Date window (lesson 41)

A `{date_window}` slot from: an explicit date or year (exists), a relative date against today
or the document date (`temporal.relative_anchor` exists for retime), or a **calendar event**
from plan config:

```yaml
calendar:
  - {name: "<festival name>", month_day: "10-20", span_days: 5, recurring: yearly}
```

An event question gets the window, so a document dated inside it counts without the event's
word (R7 lesson 41). A partial current year is included and the bundle says so.

### 4.8 Slot quality and plan confidence (lesson 11; N6)

`slot_quality` ∈ [0, 1] = the product of per-slot factors:

| Factor | 1.0 when | Lower when |
|---|---|---|
| mention used | every template entity slot came from a user mention or the narrator | taken from the top entity of a fuzzy match (0.7) |
| twins handled | no twins, or the twin group is unioned | twins found but a step takes one (0.5; only views with a scalar param) |
| search term | non-empty after filler removal | empty (0.6) |
| scope | domains given or chosen at ≥ threshold | all domains searched (0.8) |

Plan confidence = `min(route, domain, view?, slot_quality)`. `confidence.weakest` names it.
The threshold stays per domain (`confidence_threshold`, default 0.7) until round 8 (§11).

## 5. Plan programs

### 5.1 Grammar

A program is a list of nodes. Each node has an `id` and exactly one of:

| Node | Meaning |
|---|---|
| `run: "<template>"` | one `artmind query …` command line; slots in `{}` |
| `par: [nodes]` | run children independently; result = the list of their results |
| `map: {over: <list slot>, as: <name>, do: node, max: N}` | run `do` once per item (twins, documents, years, phrasings, claims), at most `max` |
| `pick: {from: <node id>, how: best_of \| union \| first_ok, score: [predicates]}` | combine a `par`/`map` result |
| `page: {from: <node id>, until: rows_total \| k}` | follow `handle`/`next` (P1b `query page`) until exhaustive or k rows |
| `when: "<predicate>"` | on any node: run only if the predicate holds (§5.3) |

Plus a `guards:` list (§5.4). Limits: at most 12 run nodes after `map` expansion is bounded by
`max` (default 10, at most 40), at most 3 levels of nesting. These are checked when
`routes.yaml` loads; a template outside them fails the routes test.

`union` merges rows by their id (evidence id, entity id, document id), keeps the first
occurrence's rank, and records `discrepancies` when two sources disagree on a count
(graph count vs text check, lesson P29).

### 5.2 Templates in `routes.yaml`

`routes.yaml` gains `version: 2`; a route may carry `program:` instead of `steps:`/`fallbacks:`.
A route with only `steps:` is read as a program of `run` nodes in sequence with its fallbacks
as guards, so v1 routes keep working unchanged and are converted one at a time.

Example, the chunks-only cross-document program (lesson 13), shapes only:

```yaml
- id: survey
  program:
    - id: p
      par:
        - {id: needle, run: "artmind query vector-text --shape brief --domain {domains} --topK 10 {search_term} --compact"}
        - {id: wide, run: "artmind query survey --shape brief --domain {domains} {question} --compact"}
    - {id: u, pick: {from: p, how: union}, when: "not needle.score_drop >= NEEDLE_MARGIN"}
  guards:
    - {on: wide, if: "truncated", then: {page: {until: k, k: 50}}}
    - {on: u, if: "distinct_documents == 0", then: escalate}
  evidence: [P43, L13]
```

Every template is checked against the Click tree (existing test), must pass `--compact` and,
for the four retrieval commands, `--shape brief` (new test).

### 5.3 Predicate vocabulary

Computed by code from a step's JSON (the CLI envelope makes these uniform). No free
expressions: a predicate is `<name> <op> <number|bool>` joined by `and`/`not`.

| Predicate | From |
|---|---|
| `rows`, `rows_total`, `truncated` | envelope / payload |
| `distinct_documents`, `docs_cited` | evidence document ids |
| `max_relevance`, `score_drop` | `relevance` of ranks 1 and 2 (passage rows) |
| `payload_chars`, `persisted` | P1b span fields (`persisted` only for agent-run steps) |
| `entity_docs` | the resolved entity's document count (catalogue / `entity-context`) |
| `rows_below_k` | `rows < k` for the step's k |
| `twins` | count of twins of the step's entity |
| `stale`, `view_status`, `history_matches` | as v1 |
| `concentration`, `level_agreement` | measured only, not used by templates (lesson 13) |

### 5.4 Guards

`{on: <node>, if: <predicate>, then: <action>}`, evaluated after the node runs, in order, first
match wins. Actions: `escalate` (stop; bundle `escalate` set), `replace: <route id>` (run that
route's program instead, once), `retry_with: {slot: value}` (re-run the node with a slot
changed, once, e.g. the category expansion), `widen_domains` (re-run over all domains, once),
`page` (§5.1). Every guard firing is recorded in the bundle `path`.

### 5.5 Program kinds (N5)

| Kind | Routes | Shape |
|---|---|---|
| one step | entity_profile, existence, document | `run` (+ `retry_with` expansion) |
| view count | view-matched counts | `par(view, text check)` → `union` with discrepancies |
| kinship aggregate | aggregate over a person with twins | `map` over twins → `union` |
| enumeration / count | enumeration, aggregate | `run` → `page` until `rows_total` → `par` with text check → `union`; refuse a count from truncated evidence (§6.4) |
| entity visits | entity history / "all visits of X" | `run` → compare `entity_docs` with `docs_cited` → `page` |
| chunks-only cross-document | survey, search in chunks-only scopes | §5.2 example |
| latest | "the current / latest X", amendments | collect every document of the family → order by date and `SUPERSEDES` → newest first, executed/draft status per document |
| history | entity_history, time_history | the history commands (`entity-history`, `document-history`, `pattern10 --includeHistory`), never text2cypher |
| absence | "is X ever mentioned" with a negative expectation | one exhaustive exact-term sweep (`pattern7 --limit 500` + `vector-text` keyword leg), not repeated searches |
| existence of a concept (R8, P55–P56) | "was there a TERM with PERSON" where TERM is rare or informal | `map` over phrasings (the user's word, 2–3 common synonyms, a description-style paraphrase **without** the person's name) on `entity-resolve` at topK 25–30 → `union`; the person scopes the result afterwards; a negative carries the probes tried (`coverage.probes`) |
| draw / list all connected (R8, P58) | "graph of my friends", everyone linked to X | enumeration program paging to `rows_total` over brief graph rows; the diagram is drawn only from exhaustive rows or says how many were left out |
| batch claims | `query run --each` | §7.5 |

## 6. The executor: `artmind query run`

### 6.1 Command

```
artmind query run "<question>" [--domain D]… [--exclude DOC]… [--previous FILE|-]
                  [--plan FILE|-] [--maxSteps 12] [--each FILE] [--compact]
```

Proxied and traced (a `run` span with child spans for each step, through the existing
`TracedGroup`; nested spans are recorded with a `parent_span_id`, new column). Read-only: no
write command can appear in a program (routes test). `--plan` runs a given plan (replay,
tests); otherwise it plans first.

Steps run **in process**: the executor calls the Click commands through the same path `serve`
uses (`CliRunner` invoke with the budget override off, collecting the full payload), never a
shell. The tool gate therefore never sees planned steps (lesson 3). `par` runs sequentially in
P3 (the CLI path holds one lock in `serve`); true parallelism is a later optimisation measured
on latency.

### 6.2 The bundle

```json
{
  "bundle_version": 1,
  "plan": {"intent": "retrieve", "route": "survey", "asset": null,
           "confidence": {"score": 0.82, "weakest": "slot_quality", "threshold": 0.7}},
  "scope": {"domains": ["…"], "excluded_documents": [], "rules": ["we_is_family"]},
  "path": [{"id": "needle", "cmd": "…", "rows": 10, "ms": 410, "chars": 14800},
           {"id": "wide", "cmd": "…", "rows": 50, "ms": 920, "chars": 22100, "guard": null}],
  "evidence": [{"eid": "e1", "kind": "passage", "text": "…", "passage_id": "…", "chunk_id": "…",
                "relevance": 0.71, "doc": {"id": "…", "title": "…", "domain": "…",
                "date": "…", "status": "current", "executed": true | null}, "suspect_conversion": false}],
  "documents": [{"rank": 1, "doc_id": "…", "title": "…", "date": "…", "status": "…",
                 "gist": "…", "gist_source": "passage", "evidence": ["e1"]}],
  "facts": [{"fid": "f1", "kind": "graph_row", "row": {…}, "evidence": ["e3"]}],
  "guides": [{"kind": "topic_page", "slug": "…", "stale": false, "cites": ["e1"]}],
  "coverage": {"documents_read": 12, "documents_total": 26, "found": 7, "exhaustive": false,
               "probes": ["<phrasing>", "…"], "depth": 25},
  "warnings": ["twins: two entities named …; both included"],
  "gaps": [{"kind": "identity", "entities": ["…", "…"]}],
  "escalate": null,
  "offer": "run a full survey" | null,
  "handle": "…" | null
}
```

- `evidence` is the only thing an answer may cite (architecture principle 3). `documents` is
  the brief document list (title, date, gist) for coverage and depth; `guides` steer.
- **Budget:** the bundle uses `ARTMIND_BUNDLE_BUDGET` (default the step budget, 24,000). Steps
  are run with no budget (the executor needs full results for guards); the bundle is then
  **assembled** to fit: evidence first (top passages by relevance, at most 10, then one per
  further document), then `documents` rows, then `facts`, then `guides`; what does not fit is
  stored under a `handle` (`query page` reads it). Ordering by relevance, not by step, is what
  keeps the answer-bearing text in.
- **A negative names its probes (R8, P56).** When the evidence for a requested item is empty,
  `coverage.probes` lists the phrasings and `depth` the topK tried, and the answer contract says
  "not found under these probes", never "not extracted".
- **Graph rows are budgeted too (R8, P58–P59).** The bundle assembles graph facts as brief rows
  (id, name, class, one-line description, evidence ids); a hub entity's full `entity-context`
  row (about 25,000 characters in round 8) is never placed whole.
- `suspect_conversion` from the page check (P1b `reversed_numeric`, existing findings) by
  document page.
- `executed` from document metadata when the family has executed/draft versions (lesson 33);
  null when unknown.

### 6.3 Fast path and reasoning path

`escalate` null and plan confidence ≥ threshold → **fast path**: the agent writes the answer from
the bundle, no further command. `escalate` set or confidence below → **reasoning path**: the
agent gets the bundle (as context, not as the answer) plus `escalate.reason` and
`escalate.suggested`, and may run commands itself as today. Both are traced; the `run` span
records `path: fast | reasoning`. The agent may still deviate on the fast path; that is traced as
`overridden` and is a review signal (architecture §5.4).

### 6.4 Counts and enumerations (lesson 26; principle 9)

A program of kind enumeration/count runs `page` to `rows_total`. If the guard budget stops it
first, `coverage.exhaustive = false` and the bundle carries no `count` fact; the answer contract
(§7.3) then writes "N found in M of K documents read". The executor never emits a count from
truncated evidence.

### 6.5 Latest (lesson 30)

`latest` collects the target's family (documents linked by `SUPERSEDES`, or sharing the
document entity's reference, `artmind/document_entity.py`), orders by `date` then chain, and
puts the newest first in `evidence` with a `latest: true` marker. A later restatement that
reverses an earlier change is therefore first, not missed (the round-7 batch error).

### 6.6 `follow_up_why`: must-find re-run (lesson 22; N9)

The previous plan is re-run with `must_find: [<named item>]`: after the program, the executor
checks the item appears in evidence (name or alias match in passage text, or entity id in facts).
If not, it runs once more with `retry_with` the item as `{search_term}`; if still absent, the
bundle's `gaps` gets `identity` or `alias` (the item resolves to an entity with another name →
identity; it matches no entity and no text → alias) and `escalate` explains. Round 7 estimate:
6 of 8 brain01 and 2 of 3 chunks-only corrections close on the re-run.

### 6.7 Errors

A failing step is recorded in `path` with its exit code and the first line of its error; the
program continues where a guard or `par` allows, otherwise it escalates. Exit codes follow the
CLI conventions (store failure = 3 for the whole run only when every step failed on the store).

## 7. Agent contract (P3)

### 7.1 Skill diet

`artmind-query` SKILL.md to about 150 lines: call `artmind query run` for every new question and
every follow-up (pass nothing else; the UI passes `--previous`); read a bundle; evidence vs
guides; fast vs reasoning path; when `escalate` is set, the generated routing table (from
`routes.yaml`) and the references; the answer contract (§7.3). References keep the deep
material. Tests check the skill against data (routes, Click tree), not phrases.

### 7.2 Surfaces

The admin console and chat UI build `previous` from the conversation store
(`artmind/conversations/`) and pass it through a per-session file named by a new env var,
`ARTMIND_PREVIOUS_CONTEXT`, the same way the trace id travels (`ARTMIND_TRACE_CONTEXT`), so the
agent's `artmind query run` needs no extra argument.

### 7.3 Answer contract (lessons 19, 40, 42; R7-1)

- Latest applicable source first when sources are dated or superseding.
- Incompleteness stated with numbers from `coverage`, never "at least".
- Stated vs implied occurrences separated (an implied event counts and is labelled).
- A relation counted by direction: check who each episode was directed at.
- Widest granularity, grouped by level.
- Scope narrowing (`scope.rules`, `excluded_documents`) stated when it changed the result.
- `gist_source` other than `passage`, and `guides`, are never cited as the source.

### 7.4 Offers

When the agent offers a next step ("I can run a full survey"), it writes it as the bundle's
`offer` (the fast path copies `offer` from `escalate.suggested`), and the UI stores it in the
conversation turn, so the next turn's `previous.offer` has it (§3.2).

### 7.5 Batch: `query run --each FILE` (lessons 23, 32–37)

`FILE` is JSONL or YAML: `[{id, claim, source_doc?}]`. Per item, a fixed program: rewrite the
claim to corpus wording (catalogue `terms` and aliases, when built; otherwise the claim), exclude
`source_doc` and any `scope.exclude_documents`, `par(vector-text brief top 10, survey brief top
10)`, scan inside the top 3 documents (`survey --expand`), `latest` per family. A cap on items
(`--maxItems`, default 50) and on characters per item; one span per item; output is one bundle
per item in a JSONL file under `logs/runs/` plus a summary payload. No LLM call in the executor;
the judging stays with the agent or a later grader.

## 8. Asset match, gaps and proposals

### 8.1 Asset match (N2; lesson 17)

Before the route: `assets.like(question)` over views, topic pages and insights. ≥
`ASSET_MATCH` (0.80) → the asset's program (view: as v1; topic page: `topics show` + `chunks
--shape brief` for its cited passages, the page in `guides`; insight: `insights show` +
`entity-context` per subject). `ASSET_OFFER` (0.70) to 0.80 → the route's program runs and the
page is an `offer`. Below 0.70 → none. Survey's `topics` overlap is normalised by page size.

### 8.2 Gaps

The bundle's `gaps` (kinds `identity`, `alias`, `extraction`, `coverage`, `data_health`) are
written into the trace (`trace_events`, kind `gap`). P5's gap classifier turns them into
proposal files; P2/P3 do not write proposals.

### 8.3 Non-retrieval intents

`curate`, `update` and `ops` produce a plan with `program: []` and an `action`:
`{skill: "artmind-curate" | "artmind-update", draft: "<command line with --dryRun>"}`. The
executor runs the `--dryRun` (read-only by contract) and puts its `effect` block in the bundle;
the write itself stays with the skill and the user's confirmation, as today.

## 9. Changes to existing pieces

| Piece | Change |
|---|---|
| `query plan` payload | `plan_version: 2`; adds `intent`, `previous_used`, `slots` (name → value, source, quality), `slot_quality`, `scope`, `program`, `guards`, `asset_match`; keeps every v1 field (`steps` = the program flattened to its run nodes, for v1 readers) |
| `routes.yaml` | `version: 2`, top-level `intents:`, per-route `program:` (optional); loader reads v1 routes as programs |
| `domains/plan/<domain>.yaml` | new keys `categories`, `kinship`, `scope_rules`, `calendar`, `narrator.aliases`; `artmind plan config check` validates them |
| `plan_labels.yaml` | optional `intent`, `slots` (expected values), `program_kind`; `plan replay` grades intent, route and slots separately |
| `trace_spans` | `parent_span_id`; `run` spans carry `path`, `escalate_reason` |
| background agents (R8, P62) | a sub-agent the agent starts inherits the starting turn's trace id (written into its environment, not read from the session's context file, which is cleared when the turn ends), so its calls are spans of the turn that asked for them; `query run --each` makes this rare |
| `query chunks` (R8, P61) | `--domain` optional: the lookup is by id; the domain is derived from the chunks |
| graph commands (R8, P58) | a `--shape brief` for `pattern1/2/7`, `entity-context` connections and `more_chunks`, and `insights show`, through the shared `shape_option` |
| shadow | plans v2 in the background as today; additionally records `query run --plan` **dry execution** (the program with step results) when `ARTMIND_PLAN_SHADOW_RUN=1`, off by default (cost: the retrieval calls) |
| `traces stats` | fast-path share, escalation rate per route, `overridden` count, `must_find` closure rate |

## 10. Testing

Hermetic:
- Grammar: routes load; every program within limits; every template in the Click tree with
  `--compact` and `--shape brief` where required; no write command reachable.
- Intent: labelled invented questions per intent, context-only replies with and without an
  offer, status with and without an open job.
- Slots: narrator and aliases; filler stripping; category expansion; twins incl. the
  shared-spouse rule; scope rules applied as filters (assert the Cypher params sent, CLAUDE.md
  §3); calendar windows.
- Executor: each node kind on recorded step payloads (no Neo4j); guards in order; `page` to
  `rows_total`; refusal to count from truncated evidence; bundle assembly within budget keeps
  the highest-relevance evidence; must-find re-run.
- Replay: `plan replay` on v1 labels shows no route regression with v2 on lexical.

Live (each phase gate, §12): `query run` on both vaults with `ARTMIND_NO_PROXY=1`; latency per
program kind; the visibility gate (`retrieval eval --compareShapes`) unchanged.

## 11. Settings round 8 decides

Each is a named constant or config key with the default below; the spec does not change when
they move.

| Setting | Default | Round-8 measure that decides it |
|---|---|---|
| `confidence_threshold` (per domain) | 0.7 | wrong-at-high vs abstained on rated turns, with slot quality in the score (N6) |
| slot-quality factors (§4.8) | 0.7 / 0.5 / 0.6 / 0.8 | rated turns where the first step was inadequate though the route was right (lesson 11) |
| `NEEDLE_MARGIN` | 0.03 | needle vs survey labels, both vaults, more than 10 + 10 (lesson 13) |
| `ASSET_MATCH` / `ASSET_OFFER` | 0.80 / 0.70 | rated turns where a page or view was or should have been used (N2) |
| bundle evidence cap | 10 passages + 1 per further document | `page_calls` and `expand_calls` per turn after a brief survey (P1b instrumentation); many means the cap is too low |
| classifier default per vault | lexical | `plan replay --classifier both` on confirmed round-8 labels (N7) |
| `map.max` default | 10 | loop lengths in round-8 struggle turns, if any remain |
| whether the executor pre-fetches `--expand` for line-row documents | no | whether the agent opens line-row documents and then cites them (P1b `expand_calls`) |

**Round 8 reading (2026-11-05):** 47 turns, 22 rated, every rated nbs turn good with no expected answers, brain01 5 good and 5 partial. No setting above moves: the threshold and slot factors need wrong-at-high cases (none rated), the needle margin new labels (none), the asset thresholds rated topic-page turns (none), and the evidence cap more than 6 `query page` and 21 `--expand` calls. The agent paged and expanded when told to, so the cap's default stands. Round 9, after P2a, is the next chance.

## 12. Phasing and gates

| Step | Contents | Gate |
|---|---|---|
| P2a | intents, `previous`, slots, slot quality, plan v2 payload, routes v2 loader (v1 routes as programs), plan config keys, replay grading | hermetic suite; replay on existing labels: no route regression |
| P2b | programs for the kinds in §5.5, converted route by route with evidence ids; shadow records v2 plans | replay on round-8 labels: zero `wrong` on view/asset labels, route `wrong` < 10 %, no non-retrieval label planned as a confident lookup (architecture §9, §5.5 4a) |
| P3a | `query run`, bundle, nested spans; shadow dry execution behind its flag | latency: plan ≤ 1.5 s (lexical), execution ≤ 3 s for one to four steps on both vaults |
| P3b | skill diet, `previous` from the UIs, answer contract; on in the admin console | targeted agent runs no worse, fewer calls; fast-path share ≥ 50 % of rated-good turns; round-9 collection |

Round 8 (P1b's measurement) gates **P2b's merge**; P2a can be built on round-7 evidence.

## 13. Risks

- **Programs become the new long skill.** One template per route, each with evidence ids, the
  12-node and 3-level limits, and no route without a scenario.
- **Slot rules hide errors.** Every slot carries its source and quality in the plan, so replay
  grades slots separately and a wrong expansion is visible.
- **Scope rules over-filter.** Rules are stated in the bundle; a `follow_up_why` naming an
  excluded item lifts the rule for the re-run and records a `gaps` item.
- **In-process execution and the serve lock.** `query run` holds the lock for its whole program;
  a long batch blocks other queries on that vault's serve. `--each` therefore runs in-process in
  the caller (not proxied) and is capped.
- **Private config.** `categories`, `kinship`, `scope_rules` and `calendar` hold private terms;
  they live in the vault's `domains/plan/`, never seeded, never in this checkout or its tests.

## 14. Open for the plan

1. Whether `intents:` cues belong in `routes.yaml` or a sibling `intents.yaml` (same loader).
2. The exact affirmative list for context-only replies, from round-8 follow-ups.
3. How `executed` vs draft is known per document: a schema `document_entity` property, a title
   pattern, or a plan-config rule per domain.
4. Whether the chunks-only `par` program should also run for graph domains' text questions.
