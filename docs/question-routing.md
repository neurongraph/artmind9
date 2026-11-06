# Question routing: intents, routes, slots and plan programs

How artmind decides what to do with a message before any retrieval runs. This page is the
architecture of record for the planner (`artmind query plan`, `artmind/query_plan/`). It
describes the code on `integration/p2b` (planner v2, P2a merged, P2b built). The labelling
cheat sheet is [`question-routing-labels.md`](question-routing-labels.md); the design history
is in the specs listed at the end.

Privacy rule as everywhere in this checkout: every example on this page is invented.

---

## 1. The problem it solves

An agent answering questions over a personal or project knowledge graph has many tools:
graph patterns, saved views, vector and keyword search over passages, a document survey,
history commands, a SQL store, insights. Left to choose for itself, it struggles
mechanically more often than it fails to understand the question. Trace studies (rounds 1–8,
`docs/query-routing-study/`) showed it composing command lines wrongly, looping one command
over a list, choosing the wrong search words, resolving "I" to the wrong node, and stopping
before evidence was complete. In about half of retrieval turns the struggle, not the
question, was the problem.

The planner moves those decisions out of the agent and into code that is deterministic,
testable and measured:

```
message ──► intent ──► domain ──► view? ──► route ──► slots ──► program choice ──► plan
             (8)        (scope)   (saved)    (18)    (filled    (by plan facts)    (+ guards,
                                                     in code)                       confidence)
```

The plan is data: a typed program of commands with guards, a confidence and the slot values
it used. Today plans are recorded in **shadow** beside every admin-console and benchmark turn
and graded by replay; the executor `artmind query run` (P3) will run them and hand the agent
one bounded evidence bundle.

## 2. Design principles

1. **Two decisions, not one.** First *what kind of turn is this* (intent), then *what kind of
   question* (route). Most retrieval routers assume every message is a question. In a
   conversation many are not: "make that a table", "they are the same person", "is it done?",
   "yes, go on".
2. **Routes name question shapes, not tools.** A route's description says what the user asks
   ("a complete list of every recorded item of some kind"), never which command to run. The
   description is exactly what a decision model reads, so the table is also the classifier's
   prompt.
3. **The routing table is reviewed data with evidence.** `routes.yaml` is the one place
   routing lives. Every route cites the scenarios (`S<n>`) and trace patterns (`P<n>`) it came
   from, and the test suite checks every command and option it names against the CLI.
4. **Slots are filled in code, and their quality counts.** Who "I" is, what to search for,
   which classes a class word means, which near-duplicate entities to include and which
   documents are in scope are decided by the planner, never improvised by the agent. Each
   slot records its source and a quality that lowers plan confidence.
5. **Plans are bounded programs.** A small grammar (run, par, map, pick, page, guards) with
   hard limits, read-only by construction. The same route can carry several programs chosen
   by plan-time facts, so the route decision stays stable while the method adapts.
6. **Confidence gates the fast path; abstention is a result.** A plan below the threshold is
   still a plan (suggested steps), but the agent must judge it. Labels say where the planner
   *should* be unsure, and an abstention there is graded correct.
7. **Measured before switched on.** Shadow plans, replay against private labels, route parity
   against the previous planner, and trace statistics decide each phase.

## 3. Layer 1: intents

The intent is decided first, from the message and, when the UI supplies it, the previous
turn (`--previous`). Only `retrieve` and `follow_up_why` lead to retrieval.

| Intent | The message is | Invented example | Plan |
|---|---|---|---|
| `retrieve` | a question about the corpus | "where did we stay on the coast trip?" | routes, slots, program |
| `follow_up_format` | reshape the previous answer | "make that a table", "shorter" | `program: []`, `answer_from: previous` |
| `follow_up_why` | challenge the previous answer | "why did you miss the second visit?" | the previous route re-planned with `must_find` |
| `curate` | a correction to the graph | "those two are the same person" | `action: artmind-curate` |
| `update` | a fact to store | "remember that the meeting moved" | `action: artmind-update` |
| `meta` | about the assistant | "which model are you?" | `program: []` |
| `status` | progress of a job | "is it done?" | `action: ingest job-status` (or `jobs`) |
| `ops` | a pipeline operation | "re-extract that note" | `action: artmind-ingestion-helper` |

**The previous turn.** `previous` is `{intent, route, question, offer, entities, documents,
open_job}`. Three rules sit on top of the classifier (`intents.decide_intent`):

| Rule | When | Result |
|---|---|---|
| open job | `open_job` is set and the message has a status cue | `status` |
| inherited offer | the message is context-only ("yes", "go ahead", "sounds good") and the previous turn offered something | `retrieve`, planning the previous question, `inherited_offer` recorded |
| inherited intent | context-only, no offer | the previous intent |

A pronoun follow-up with no named mention ("what did she say next?") takes the previous
turn's entities as its subjects.

**Classifier.** One batched decision call holds the domain, view, intent and route decisions.
Lexical rules are the default and always available (cues in `intents.yaml`, `^` anchors a cue
to the start); a decision model can replace them behind the same seam. When the intent is not
`retrieve`, the route decision is still taken and recorded, so replay can tell an intent
error from a route error.

## 4. Layer 2: routes

Eighteen routes, plus `view` when a saved view answers the question exactly. A route has a
description (the question shape), lexical triggers, `needs` (entity, two entities, document,
store), one or more programs, and evidence ids.

| Route | Question shape | Invented example | Needs | Default program | Variants (chosen by plan facts) |
|---|---|---|---|---|---|
| `existence` | did something happen, or is it mentioned at all | "did I visit the lake again?" | entity | exact-term entity search | `existence_concept` when a rare term sits beside a known entity; `absence` when the question expects a negative |
| `enumeration` | a complete list of every item of a kind | "list all the trips" | — | entity search paged to the total, with a text check, merged | `class_listing` when the question names one class of the scope and no other entity; `draw_connected` when an entity is named ("everyone linked to X") |
| `aggregate` | a count, total, average or frequency | "how many times did we eat out?" | — | generated graph query | `kinship_aggregate` when the person has twins |
| `superlative` | the most, least, top or favourite, inferred | "my favourite café" | — | insights, then generated graph query | — |
| `entity_profile` | who or what one named thing is | "tell me about Ann" | entity | entity context | `entity_visits` when the question counts occurrences |
| `entity_history` | how what is known about an entity changed | "how has the project changed?" | entity | entity history | — |
| `time_history` | which version of a document applied when | "what did the policy say in March?" | document | document at a date + version history | — |
| `conflicts` | whether sources disagree | "do the notes contradict each other?" | — | conflict list + text | — |
| `hierarchy` | who or what is above or below | "who reports to Ann?" | entity | hierarchy walk; entity context if empty | — |
| `path` | how two named things are connected | "how are Ann and Ben related?" | two entities | direct link; shortest path, then text if empty | — |
| `document` | what one named document says | "summarise the lease" | document | the document's facts; its history if empty | `latest` when the question asks for the current version |
| `inventory` | which documents of a kind exist | "what policies do we have?" | — | domain overview (+ class listing) | — |
| `procedure` | how to do something, from guides | "what are the steps to renew?" | — | text search, top 10 | — |
| `structured` | numbers held in tables | "the balance per account" | store | bridge + SQL (per store kind) | — |
| `insights` | what changed, who matters, who drifted | "who have I lost touch with?" | — | insights list | — |
| `filing` | notes under a tag, project or area | "my notes tagged garden" | — | filing listing | — |
| `survey` | what several documents say on one topic | "what do the contracts say about notice?" | — | needle search beside a document survey, merged | — |
| `search` | any other factual question | everything else | — | text search | the cross-document program in chunks-only domains |

**Planned routes.** Designed, not yet in `routes.yaml`. A label may already name one
(`labels.PLANNED_ROUTES`): replay counts it as `planned` and records what the planner chose,
so the shape is measured before it is built.

| Route | Question shape | Invented example | Needs | Program (when built) | Design |
|---|---|---|---|---|---|
| `episode` | when something happened, how long it lasted, what surrounded it | "when did Ann go into hospital, and for how many days?" | — | `episode_lookup` over assembled `:Episode` nodes; else `event_window`: find the anchor event, then read around it (adjacent chunks, documents in a date window, connected entities) | `docs/superpowers/specs/2026-11-06-assembly-pass-episodes-design.md` |

`episode` is not named `event` because `EVENT` is a schema class; the route answers from
derived episodes or from text, never from EVENT nodes alone, since first-pass extraction
splits one happening into several occurrent observations. `event_window` needs a later step
to read an earlier step's result, which waits for the P3 executor.

**A named class turns a search into a listing.** The question is matched against the scope's
classes before the route is settled. When it names a class by its name (plural or case aside)
and no other entity, and asks which items exist ("are there any", "list", "do I have", not a
count and not "which one"), a `search`, `existence` or `enumeration` route becomes
`enumeration` with the `class_listing` program, and the payload's `route.adjusted` records the
change. The same words over a noun that is not a class ("are there any recipes?") stay a search.

**Chunks-only domains** (`graph_extraction: false`) have no graph, so they plan only `survey`
or `search`, and `search` there uses the cross-document program.

**Views first.** When a saved view (a reviewed, parameterised Cypher query) answers the
question at or above the threshold, the plan is the view with its parameters filled; a count
question on a view adds a text check (`view_count`).

## 5. Layer 3: slots

A slot is a value a command template needs. Every slot carries `value`, `source` and
`quality`.

| Slot | Filled from | Sources |
|---|---|---|
| `narrator` | first-person words (I, me, my, we) and the narrator's aliases in plan config | `narrator`, `alias` |
| `search_term` | the user's mention spans; else the question minus filler and stopwords; category terms OR-ed in | `mention`, `filler_stripped`, `category`, `question` (empty) |
| `category` | a class word expanded to member classes and plain terms (plan config `categories:`, else the catalogue class it names) | `category`, `class_match` |
| `twins` (per entity) | same-class entities with the same name, kinship variants, first name vs full name only with a shared spouse, open same-as proposals | `name`, `kinship`, `shared_spouse`, `same_as` |
| `scope` | given domains, the chosen domain, `--exclude` documents, scope rules from plan config | `given`, `chosen`, `all` |
| `date_window` | an explicit date or year, a relative phrase, or a calendar event from plan config | `explicit_date`, `relative`, `calendar` |
| `must_find` | the items a `follow_up_why` names | `mention` |
| list slots | `twin_ids`, `phrasings` (term + synonyms + a paraphrase without the person), `years`, `family_documents` | per slot |

Private vocabulary (categories, kinship words, synonyms, scope rules, calendar events) lives
in each vault's `domains/plan/<domain>.yaml`, never in this checkout.

**Slot quality** is the product of four factors (named constants in `slots.py`):

| Factor | 1.0 when | Lower when |
|---|---|---|
| mention | every entity slot came from the user's words or the narrator | taken from a fuzzy top match (0.7) |
| twins | none, or the program unions them | found but one is taken (0.5) |
| search term | non-empty after filler removal | empty (0.6) |
| scope | domains given or chosen confidently | all domains searched (0.8) |

## 6. Layer 4: plan programs

### 6.1 Grammar

| Node | Meaning |
|---|---|
| `run` | one read-only `artmind query …` command; slots in `{}` |
| `par` | children run independently; result is the list |
| `map` | run a body once per item of a list slot, at most `max` (default 10, at most 40) |
| `pick` | combine a `par`/`map`: `union` (dedupe by id), `best_of`, `first_ok` |
| `page` | follow a cut result's handle until `rows_total` or k rows |
| `when` | on any node: run only if a step predicate holds |
| `optional` | on a run node: skip silently when a slot does not fill |

Limits checked at load: at most 12 written run nodes, at most 40 commands after map
expansion, at most 3 nesting levels; every command must pass `--compact`, the four retrieval
commands `--shape brief`, and no write command is reachable.

### 6.2 Guards and step predicates

`{on: node, if: predicate, then: action}`, evaluated after the node runs, first match wins.
Actions: `escalate`, `replace: <route>`, `retry_with: {slot: value}`, `widen_domains`, `page`.

Step predicates are computed from a step's JSON, never free expressions: `rows`,
`rows_total`, `truncated`, `distinct_documents`, `docs_cited`, `max_relevance`, `score_drop`,
`payload_chars`, `persisted`, `entity_docs`, `rows_below_k`, `twins`, `stale`, `view_status`,
`history_matches`; `concentration` and `level_agreement` are measured only. Named constants
(`NEEDLE_MARGIN` = 0.03) may stand on the right-hand side.

### 6.3 Program choice by plan facts

A route may carry `programs:`; the planner takes the first whose `when` holds and the last as
the default. Plan facts are a separate, fixed vocabulary computed at plan time:

| Fact | True or counted when |
|---|---|
| `twins` | the primary entity's twin count |
| `entities` | named entities resolved |
| `negative` | the question expects a negative ("ever", "any mention", "not recorded") |
| `latest` | latest, current, most recent, amended |
| `occurrence` | visits, times, occasions, every time |
| `rare_term` | a content word is in neither the catalogue's names nor its classes |
| `named_class` | the classes the question names by class name (or a configured category) when it names no other entity; 0 otherwise |
| `category`, `date_window`, `document`, `chunks_only`, `view_count` | the slot or scope exists |

### 6.4 Program kinds

| Kind | Shape | Why it exists (trace evidence) |
|---|---|---|
| `one_step` | one command, with guards | the simple case |
| `view_count` | view beside a text check, merged | counts from a view can miss what extraction missed |
| `enumeration` | page to the total, text check, union | a list from a truncated result is wrong (P21, P58) |
| `class_listing` | every entity of the named class beside a text check, merged | "are there any IDEAs" was planned as one text search although IDEA is a class |
| `draw_connected` | entity context + paged entity search | "graph of my friends" stopped at the first page (P58) |
| `kinship_aggregate` | map over twins, union | the same person split across nodes (P37) |
| `entity_visits` | entity context, page, text check, docs vs cited | occurrences missed by a single hub row (P50, P59) |
| `chunks_cross_document` | needle search beside a survey, merged | one ranking misses either the needle or the breadth (P43) |
| `latest` | map over the document family, newest first | a later restatement reversed an earlier change (lesson 30) |
| `history` | history commands only | generated Cypher could not read history (P12) |
| `absence` | one exhaustive exact-term sweep | repeated searches proved nothing (P25) |
| `existence_concept` | map over phrasings, union | a rare or informal word missed its concept (P55, P56) |
| `batch_claims` | per-claim program (P3, `--each`) | batch claim checking via sub-agents (P46, P62) |
| `sequence` | a v1 route read as sequential run nodes | compatibility |

## 7. Confidence, abstention and the fast path

Plan confidence is the weakest of the stages: domain, view, intent or route, and slot quality.
At or above the domain's `confidence_threshold` (default 0.7) the plan is `high` and its
commands are `steps`; below, they are `suggested_steps` and the agent reads the routing table
and judges. In P3 a high plan whose bundle does not escalate is the **fast path** (the agent
writes the answer from the bundle); anything else is the **reasoning path**.

Replay grades this explicitly. Each label says whether the planner should be confident
(`expect: high|low`):

| Plan | Label expects | Outcome |
|---|---|---|
| high, matches | high | correct |
| high, differs | high | wrong |
| high | low | wrong (overconfident) |
| low | high | abstained |
| low | low | correct abstain |

## 8. How it is measured

| Instrument | What it shows |
|---|---|
| **Shadow plans** | a v2 plan beside every admin-console and benchmark turn (`plan_shadow` events); `traces stats` counts by intent and program kind, and agreement of high plans with the agent's first call |
| **Replay** | `artmind plan replay --repeat 4` over private labels: route, view, domain, intent and slots graded separately; gate: no wrong view, route wrong under 10 %, every non-retrieval intent right |
| **Route parity** | the same recorded questions planned by the previous and the new planner; a phase may change programs, never routes, unless intended |
| **Trace studies** | rated rounds of real use (`docs/query-routing-study/`), each finding a numbered pattern that a route, slot or program then cites |

Replay and shadow plans carry the previous turn: a shadow plan is given the turn before it in the same
conversation, rebuilt from the trace store (its question, the intent, route and entities of its own shadow plan,
and any offer its answer ended with), and replay threads labels through `follows:` the same way. Replay compares
decisions (intent, domains, route, view and its parameter values, program kind, confidence level, step commands),
never probabilities, and names the fields that flipped between repeats.

## 9. Related work

Each layer has precedents; what we have not found is a system that combines them in this
form. Notably: an intent layer that admits non-question turns ahead of routing, a routing
table that is evidence-backed reviewed data tested against the tool surface, routes mapped to
per-store programs chosen by plan-time facts, slots filled in code with a quality that feeds
confidence, and labels that grade abstention.

| Area | Prior work | Relation |
|---|---|---|
| Routing by query type | Adaptive-RAG (Jeong et al., NAACL 2024) routes by complexity (none / single-step / multi-step); RAGRouter-Bench (2026) labels factual / reasoning / summarisation; GraphRAG's local vs global search | one axis (complexity or scope) where ours is a question-shape table over heterogeneous stores |
| Router choosing a representation | StructRAG (ICLR 2025): a router picks chunk, table, graph, catalogue or algorithm per question | closest in spirit: method chosen per question; it restructures text, ours picks programs over existing stores |
| Router abstractions | LlamaIndex `RouterQueryEngine`, LangChain4j `QueryRouter`, semantic routers | an LLM or embedding selector over tools; no slots, programs or abstention grading |
| Question taxonomies | CRAG (Meta, 2024): simple, condition, set, comparison, aggregation, multi-hop, post-processing, false premise | a benchmark taxonomy, close to our routes (set ≈ enumeration, aggregation ≈ aggregate, false premise ≈ absence) but not an executable routing table |
| Template KGQA | template classification then slot filling (LC-QuAD's 38 templates; TeBaQA; template-based QA with recursive networks) | the classic "classify, then fill slots" pattern our routes and slots follow, one level up |
| Compositional programs | KoPL / KQA Pro (ACL 2022): 27 functions composed into question programs | programs over one knowledge base; ours compose CLI commands across graph, text and SQL |
| Plan, then execute | ReWOO, LLMCompiler (task DAG), plan-and-execute agents | the planner is an LLM; ours is deterministic code with an optional decision model |
| Correction by evaluation | Corrective RAG (2024): a retrieval evaluator triggers a fallback | our guards, with fixed predicates over step results |
| Conversational intent | MSDialog intent taxonomy (Qu et al.), dialogue state | intents of information-seeking turns; ours adds curation, update, status and ops, and inherits offers |

## 10. Open questions

- How often span questions (route `episode`) occur: labels count them as `planned` now; the
  count decides when the assembly pass and the route are built.
- Whether `intents:` cues should be learned from labels rather than written (lexical stays the
  default until labels say otherwise).
- How `executed` vs draft is known per document for `latest`.
- Whether the chunks-only cross-document program should also run for graph domains' text
  questions.
- The numeric settings round 9 decides (spec §11): threshold, slot factors, needle margin,
  asset thresholds, evidence cap, `map.max`.

## Sources in this repository

- `docs/superpowers/specs/2026-10-22-query-plan-design.md` (v1 planner)
- `docs/superpowers/specs/2026-11-05-planner-v2-design.md` (intents, slots, programs, executor)
- `docs/superpowers/specs/2026-11-05-bounded-output-design.md` (brief shapes the programs use)
- `docs/query-routing-study/` (scenarios, trace patterns, round lessons)
- Code: `artmind/query_plan/{intents.yaml,intents.py,routes.yaml,routes.py,program.py,slots.py,planner.py,replay.py}`

## External references

- Adaptive-RAG: https://alphaxiv.org/overview/2403.14403v2
- RAGRouter-Bench: https://arxiv.org/abs/2602.00296 ; routing baselines: https://arxiv.org/html/2604.03455v1
- StructRAG: https://arxiv.org/html/2410.08815v1
- CRAG benchmark: https://arxiv.org/pdf/2406.04744
- KQA Pro / KoPL: https://arxiv.org/abs/2007.03875
- TeBaQA: https://arxiv.org/pdf/2103.06752 ; template QA with recursive networks: https://ar5iv.labs.arxiv.org/html/2004.13843
- Corrective RAG: https://www.alphaxiv.org/audio/2401.15884
- LlamaIndex routers: https://developers.llamaindex.ai/python/framework/module_guides/querying/router/
- LangChain planning agents (ReWOO, LLMCompiler): https://www.langchain.com/blog/planning-agents
- GraphRAG local vs global (via VLDB QA-GraphRAG): https://www.vldb.org/pvldb/vol19/p2224-sheng.pdf
- MSDialog user intent: https://arxiv.org/pdf/1901.03489
