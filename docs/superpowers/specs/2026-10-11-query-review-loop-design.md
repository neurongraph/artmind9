# Query review loop — traces, shape audit, improvement dossiers — design

Status: draft for review (2026-10-11). Branch `spec/query-review-loop`.

## 1. Problem

artmind answers questions over a knowledge graph, but nothing learns from those
answers. When an answer is wrong or thin, nothing records why. The fix could
belong to any layer: the answer synthesis, the query routing, a missing view,
entity identity, extraction, the schema, or the corpus itself.

- **No persisted traces.** The admin console streams agent events to the
  browser's trace drawer and discards them. Tool I/O is clipped to 600 chars
  (`webui/backends/base.py` `TRACE_CLIP`). `EventMapper` drops token usage and
  the model. The only stored trace is `benchmark_questions.trace_json`. It is a
  thin list of tool calls and results, with no ids, timings or tokens.
- **No link from the agent to the knowledge layer.** A trace shows `Bash`
  inputs and outputs. It does not show which view or pattern ran, which
  entities resolved, what Cypher text2cypher generated, or which chunks came
  back. `llm_calls.log` records extraction and text2cypher prompts with no
  link to the turn that caused them.
- **No feedback.** The operator cannot mark an answer as wrong or say what
  was expected.
- **No method for root cause.** Without evidence, every failure looks like
  "the graph is missing something". That pushes toward schema changes and
  re-ingests that are expensive and often unnecessary.
- **No review of graph shape.** Nothing compares the entities, relationships
  and properties extracted from a passage with the passage text, or with the
  questions people ask of it.

## 2. Goals and non-goals

Goals:

1. **Capture.** Persist every admin-console turn and every benchmark question
   as a structured trace. A trace holds agent events, plus CLI spans that
   record what the knowledge layer did, plus the LLM calls those spans made.
2. **Feedback.** The operator can rate an answer good / partial / wrong and
   say what was expected. Cheap automatic signals triage unrated turns.
3. **Diagnosis.** An `artmind-review` skill, run from Claude Code in the
   checkout, attributes each failure to one pipeline stage, with cited
   evidence.
4. **Shape audit.** A three-way comparison of source text, extracted
   subgraph and question, graded against a fixed checklist and tagged by
   schema element.
5. **Proposals with proof.** Each review produces a dossier with a concrete
   proposal: a view draft, a schema diff, a prompt diff, or a code-change
   spec. Graph-shape proposals carry a dry-run re-extraction shape diff as
   proof that they work.
6. **Verification.** Every dossier carries a regression question. Re-running
   it after the fix closes the loop.
7. **Privacy.** Review output never goes to the public code repo. Dossiers
   are versioned only with the vault, whose repo is private.

Non-goals (v1):

- Capture for the end-user chat UI, the Obsidian plugin, or external agents.
  Admin console and benchmark only. The CLI span layer is surface-agnostic,
  so adding other surfaces later only needs a trace id exported at their
  entry point.
- Auto-applying any change. The reviewer proposes and a human applies.
- GitHub issues or any outbound publication of review content.
- An LLM-as-judge in artmind's own code. The reviewer agent and the human
  grade.
- An admin "Reviews" tab. This is a later phase.
- External observability backends (OpenTelemetry, Langfuse). An exporter
  could sit on top of the tables later.

## 3. The loop

```
admin console turn ──► trace (turn + events + CLI spans + LLM calls)  ──┐
   operator rating: good / partial / wrong + "expected…" note           │
benchmark question ──► same trace schema ──────────────────────────────┤
                                                                        ▼
artmind traces triage   → ranked queue (rated-bad first, then auto-signals)
                                                                        ▼
artmind-review skill (Claude Code, in the checkout, ARTMIND_VAULT=<vault>)
   walk the evidence chain backwards; stop at the first failing stage
   shape audit for identity / extraction / schema verdicts
                                                                        ▼
dossier  <vault>/.artmind/reviews/<review_id>.md   (committed to the vault)
   diagnosis, evidence excerpts, shape audit, proposal, proof,
   regression question, agent prompt (with privacy preamble)
                                                                        ▼
human + coding agent apply the fix   (review leakcheck before committing code)
                                                                        ▼
artmind review verify <id>  → re-run the regression question → compare → verified
```

## 4. Trace data model

New tables in `document_registry.db`, created in `artmind/db.py` beside
`benchmark_*`. The DB is gitignored and local to the machine. Traces never
leave it.

| Table | Row per | Columns |
|---|---|---|
| `trace_turns` | question → answer turn | `trace_id` (uuid4 hex, PK), `source` (`admin` \| `benchmark`), `session_id`, `benchmark_run_id`, `benchmark_seq` (nullable), `question`, `answer`, `domains` (JSON), `profile`, `backend`, `model`, `turns`, `duration_s`, `cost_usd`, `input_tokens`, `output_tokens`, `graph_state` (JSON), `status` (`running` \| `done` \| `error`), `error_message`, `started_at`, `completed_at` |
| `trace_events` | agent event | `trace_id`, `seq`, `ts`, `type` (`tool_call` \| `tool_result` \| `thinking` \| `text` \| `error`), `tool_id`, `parent_tool_id` (subagent traffic), `name`, `payload` |
| `trace_spans` | `artmind query …` invocation | `span_id` (PK), `trace_id`, `tool_id` (nullable; matched afterwards, see §5.2), `command` (e.g. `query graph pattern5`), `args` (JSON), `exit_code`, `duration_ms`, `rows`, `truncated`, `evidence` (JSON), `started_at` |
| `trace_llm_calls` | `call_llm` / embedding call within a span | `span_id`, `seq`, `call_type`, `model`, `prompt`, `response`, `duration_ms` |
| `trace_feedback` | rating | `trace_id`, `rating` (`good` \| `partial` \| `wrong`), `expected`, `created_at` |
| `trace_reviews` | review (local index of dossiers) | `review_id`, `trace_id`, `stage`, `elements` (JSON), `status`, `dossier_path`, `updated_at` |

`payload`, `prompt` and `response` are stored unclipped up to 64 KB each. Longer
values are cut and end with the marker `…[truncated N bytes]`. `TRACE_CLIP`
remains a display-only limit.

`graph_state` records what the turn ran against:
`{"artmind_version", "schemas": {<domain>: <sha256 of schema file>}, "vault_head"}`.
A later review can then tell "the graph has changed since" from "this is still
broken".

`evidence` is written by the commands themselves (§5.3). Its keys:

| Key | Written by | Value |
|---|---|---|
| `view` | `query views run` | `{name, version, params}` |
| `pattern` | `query graph patternN` | `{name, params}` |
| `cypher` | text2cypher, views, patterns | executed Cypher text |
| `resolved` | entity-resolve, view entity params | `[{query, candidates:[{id, name, class, score}], chosen}]` |
| `chunks` | vector-text, chunks, entity-context | `[{chunk_id, doc_id, score}]` |
| `entities` | any command returning entities | `[entity _id]` (capped at 200) |

Retention: rows older than `ARTMIND_TRACE_RETENTION_DAYS` (default 90) are
pruned when `admin-ui` starts and when `artmind traces prune` runs. A turn
with feedback or a review is never pruned.

## 5. Capture points

The rule for every point: **capture never breaks or slows a query in a way
the user can notice.** Every trace write is wrapped and its failure swallowed,
which is the contract `_warn_if_vault_stale` already follows. With no
`ARTMIND_TRACE_ID` set, all capture code returns immediately.

### 5.1 Admin console turn (`artmind/webui/app.py`, `agent.py`, backends)

- `/api/chat` creates the `trace_id` and inserts the `trace_turns` row. It
  passes `ARTMIND_TRACE_ID` to the agent through the existing `env` hook
  (`agent_options(env=…)`), so every Bash subprocess inherits it. The
  `trace_id` is sent to the browser in the first SSE event, so the UI can
  attach feedback to it.
- Each mapped event goes to the SSE stream and also to `trace_events`
  (unclipped). Capture applies only to the admin profile. A
  `TraceRecorder` object does the writing, and app.py and the benchmark
  runner share it.
- `EventMapper` changes:
  - Keep `ResultMessage.usage` and the model in `turn_done`.
  - Emit `thinking` events for capture.
  - Emit subagent events (`parent_tool_use_id`) marked with
    `parent_tool_id`. The browser still filters them out.
- The ACP backend records what it has. Tokens, cost and model may be null.
- The session id is persistent per tab, so one trace is one turn and a
  multi-turn conversation is several traces sharing a `session_id`.

### 5.2 CLI span (`artmind/cli.py`, new `artmind/tracing.py`)

- The `query` group callback calls `tracing.start_span(ctx)` when
  `ARTMIND_TRACE_ID` is set, and registers `ctx.call_on_close(finish_span)`.
  `call_on_close` also runs when a command fails, which `result_callback`
  does not.
- Span state lives in a `contextvars.ContextVar`. `finish_span` records
  `exit_code` and `duration_ms`. Commands report `rows` and `truncated`
  through `tracing.note(rows=…, truncated=…)`.
- `tool_id` correlation: the CLI cannot see the agent's tool-use id.
  `finish_span` stores the span with `tool_id` null. When the turn ends,
  `TraceRecorder` assigns each span to the `Bash` `tool_call` whose time
  window contains the span's `started_at` and whose command text contains
  the span's command. Spans that match no call keep a null `tool_id`.
- `artmind traces`, `artmind review` and every non-`query` command are not
  spanned.

### 5.3 Evidence notes (query layer)

`tracing.note(key, value)` is a no-op without an active span. It is called at
these points only:

- `kg_views.runner.run_view`
- `graph_query` pattern entry points
- `text2cypher.execute_text2cypher`
- `vector_query.entity_resolve`
- the `vector-text`, `chunks` and `entity-context` commands

The notes are added inside the existing functions, with no signature
changes.

### 5.4 Proxy (`artmind/_entry.py`, `artmind/server.py`)

- `_entry._proxy` adds `"trace_id": os.environ.get("ARTMIND_TRACE_ID")` to
  the JSON payload. It stays stdlib-only.
- `server.QueryRequest` gains `trace_id: str | None`. `run_cli_args` sets
  `ARTMIND_TRACE_ID` in `os.environ` for the duration of the invoke and
  restores it afterwards. This is safe because invokes are already
  serialized by `_invoke_lock`. A daemon started before this ships records
  nothing, and `artmind traces show` says so when a turn has `Bash` calls to
  `artmind query` but no spans.

### 5.5 LLM calls (`utils/functions.py`)

`log_llm_call` also calls `tracing.record_llm_call(...)`, which writes to
`trace_llm_calls` when a span is active. `llm_calls.log` is unchanged.
Ingestion-time calls never have a span, so they are unaffected.

### 5.6 Benchmark (`artmind/benchmark.py`)

`_run_benchmark` uses `TraceRecorder` with `source=benchmark`. It sets
`ARTMIND_TRACE_ID` per question and records the same events and spans.
`benchmark_questions` gains a `trace_id` column. `trace_json` is still
written for the existing Benchmark tab.

### 5.7 Feedback (admin UI)

- A control under each assistant answer in `admin.html`: good / partial /
  wrong, plus an optional "What was expected?" text box.
- It posts to `POST /api/traces/{trace_id}/feedback`. A turn's feedback can
  be updated, and the latest row wins.
- The same is available from the CLI: `artmind traces feedback <id> --rating
  wrong --expected "…"`.

## 6. Trace CLI (`artmind traces …`, not proxied)

| Command | Does |
|---|---|
| `traces list [--source] [--rating] [--since] [--domain] [--limit]` | one line per turn: id, time, rating, question (truncated), turns, cost |
| `traces show <id> [--compact]` | full JSON evidence chain: turn, events in order, spans (with evidence and LLM calls) nested under the Bash call they belong to, feedback |
| `traces triage [--since] [--limit]` | the ranked queue (below), excluding turns that already have a review |
| `traces feedback <id> --rating … [--expected …]` | record feedback |
| `traces prune` | apply retention |

The `traces` and `review` groups go into `COMMAND_GROUPS` in `cli.py`
(`test/test_cli_guide.py` enforces this). Each group has its docstring and a
`justfile` recipe where one is useful.

**Triage order:** turns rated `wrong` first, then `partial`. Unrated turns
follow, ordered by a score that adds one point per signal:

- a span with `rows == 0`
- a `truncated` span
- text2cypher was used
- the agent ran 3 or more distinct retrieval commands, meaning it went far
  down the fallback ladder
- `turns` is above the 90th percentile for the period
- the answer matches a "couldn't find / no information / not in the graph"
  phrase list
- an entity resolved ambiguously (top two candidates within 0.05)
- a span ended with `exit_code != 0`

Turns rated `good` are excluded.

## 7. The reviewer

### 7.1 Where it runs

`artmind-review` is a new skill in `artmind/skills/`. It is absent from every
`webui/profiles.py` profile, so the chat UI and the admin console agent
never load it. It is run from Claude Code in the checkout with
`ARTMIND_VAULT=<vault>`, because it needs three things together: artmind's
source (to write code-change specs), the vault's data (chunks, staging,
schemas) and the local trace DB. It runs on demand. A scheduled task is
possible but is not part of v1.

### 7.2 Stage attribution

For one trace, the skill:

1. Loads it with `artmind traces show <id>`.
2. **Locates the ground truth.**
   - The chunks the answer used come from the spans' `chunks` and
     `entities` evidence.
   - The chunks it should have used come from searching the question and
     the operator's `expected` note with `query vector-text` / `query
     chunks`.
   - If no chunk supports the expected answer, the verdict is **corpus
     gap** and the review ends.
3. **Walks the stages in a fixed order** and stops at the first failure.
   Downstream stages come first because they are cheap to fix.
   1. **synthesis**: the right rows were returned, but the answer misstated
      them, left them out, or went beyond them.
   2. **routing**: a view, pattern or command that answers the question
      existed and was not used, or was used with the wrong parameters.
   3. **missing-view**: the question needs specific Cypher, and it recurs.
      Look for similar traces in `traces list`.
   4. **identity**: the right facts exist but are split across duplicate
      entities, or merged into the wrong one.
   5. **extraction**: the fact is in the chunk, the schema can represent it,
      and it is absent from (or wrong in) the staging JSON.
   6. **schema**: the schema has no class, property or relationship type
      that could hold the fact in the shape the question needs.
4. **Cites evidence for every verdict.** Each one needs at least one span
   id, row, chunk sentence or staging entry.

### 7.3 Shape audit

The shape audit runs for every identity, extraction or schema verdict. It is
optional for the other verdicts.

`artmind review shape <trace_id> [--chunk <id>]…` builds a bundle. The
default chunks are the ground-truth chunks from 7.2 step 2. The bundle has
three parts:

- **Text:** the evidence chunks, split into numbered sentences.
- **Graph:**
  - The entities with `EXTRACTED_FROM` to those chunks, plus one hop, with
    class, properties and relationships (type, direction, properties).
  - The staging JSON entries for the same chunks. Comparing the two shows
    whether something was lost at extraction or at merge/commit.
  - The schema section for each class involved.
- **Question:** the question and the operator's `expected` note.

The reviewer writes an **answerability pattern**: the traversal that would
answer the question, as a small JSON path pattern. For example:

```json
[{"class": "Case"}, {"rel": "ASSIGNED_TO", "dir": "out"}, {"class": "Person"},
 {"require": {"node": 0, "properties": ["status", "opened_on"]}}]
```

It then grades the bundle against the checklist. Each finding is tagged
`<domain>/<Class>[.<property>|-<REL>->]`.

| Check | Finding when |
|---|---|
| coverage | a fact the question needs is stated in the text and absent from the subgraph |
| granularity | a fact is flattened into a string property or `description` when the question needs it as an entity plus a relationship, or a trivial value is promoted to a node |
| specificity / direction | a generic relationship type is used where the question needs a typed one, or the edge points the wrong way |
| reification | an n-ary fact (actor, action, object, time, role) is collapsed into a binary edge and loses qualifiers |
| qualifiers | temporal, status or provenance qualifiers are missing, or sit on the wrong element |
| identity | one real-world thing is split across nodes, or distinct things are merged |
| noise | the subgraph contains entities or edges the text does not support |
| answerability | the answerability pattern has no match in the current subgraph |

### 7.4 Dry-run re-extraction (fix-stage proof)

`artmind review reextract <trace_id> (--schema <proposed.yaml> | --meta <proposed meta.yaml>) [--chunk <id>]… [--maxChunks 5]`

- Re-runs the entity, property and relationship prompts on just the evidence
  chunks. It uses `extraction.build_*_prompt` and `extract_with_retry` with
  the proposed schema or prompt templates.
- **Writes nothing:** no staging, no graph, no registry. It costs 3 LLM calls
  per chunk, bounded by `--maxChunks`.
- **Output:** a shape diff between the current staging entries and the
  proposed extraction for the same chunks (nodes, edges and properties
  added, removed or changed, per chunk). Both sides are compared before
  canonicalization, so the comparison is like for like.
- **Answerability:** it checks the reviewer's pattern against both sides,
  and reports `matched` / `not matched` for current and proposed.
- Identity effects (merges) are out of scope for the dry run. Identity
  proposals are proved with `artmind-curate` same-as evidence.

### 7.5 Aggregation

- `artmind review shape-report --domain X` groups the findings of open
  dossiers by schema element.
- When a new finding's element already has an open dossier, the reviewer
  appends the trace (symptom, evidence, finding) to that dossier and does
  not open a new one. One systemic schema problem gets one dossier listing
  every symptom.
- `--sample N` runs the shape audit on N random chunks of the domain with
  no failing question, as a proactive check. The answerability check is
  then skipped, and the domain's frequent questions from `traces list` can
  guide which elements matter.

## 8. Dossiers

### 8.1 Location and versioning

- In a vault: `<vault>/.artmind/reviews/<review_id>.md`. Each review is one
  file, so two machines never edit the same file. That is the same pattern
  as `.artmind/data/curation/<kind>/<id>.json`. The folder is versioned with
  the vault (not gitignored) and sits under `.artmind/`, so Obsidian does
  not show it and `vault sync` does not ingest it.
- Outside a vault: `~/.artmind/reviews/`, not versioned.
- The vault's repo is private. That is the privacy boundary.
  `vault doctor` gains a check: when `.artmind/reviews/` holds tracked files
  and the vault's `origin` is a GitHub repo, it runs `gh repo view --json
  visibility` and warns if the result is `PUBLIC`. The check is skipped when
  `gh` is absent or the remote is not on GitHub.
- A dossier is **self-contained**. The trace DB is local to one machine, so
  the dossier embeds every excerpt it relies on.
- Status lives in the dossier's frontmatter. `trace_reviews` is a local
  index rebuilt from frontmatter by `artmind review reindex`, which follows
  the model of `docs reindex`.

### 8.2 Format

```markdown
---
review_id: rv-2026-10-11-a1b2c3
status: open            # open | in-progress | fixed | verified | dismissed
stage: schema           # corpus-gap | synthesis | routing | missing-view | identity | extraction | schema
elements: [banking.cases/Case-ASSIGNED_TO->]
trace_ids: [<id>, …]
graph_state: {artmind_version: …, schemas: {…}, vault_head: …}
created: 2026-10-11
---
## Symptom          question, answer excerpt, rating + expected note, per trace
## Diagnosis        stage verdict; evidence chain (span → rows → chunk sentence → staging entry)
## Shape audit      findings table: element, check, source sentence, why the question needs it;
                    the answerability pattern
## Proposal         one of: view.yaml + SKILL.md draft | schema YAML diff | meta.yaml prompt diff |
                    code-change spec (files, functions, behaviour)
## Proof            `views test` output | `review reextract` shape diff + answerability result
## Synthetic example  the same failure with invented entities, for tests
## Regression question
### R-<review_id> — <title>
**Question:** …
**Evaluation comment:** …
## Agent prompt     privacy preamble (8.3) + self-contained task: context, files, acceptance criteria
                    (tests on the synthetic example; `review leakcheck` passes; regression question
                    passes after re-ingest where the schema changed)
## Log              dated status changes and appended symptoms
```

### 8.3 Code-checkout privacy

The vault's repo is private. The code checkout (`neurongraph/artmind9`) is
public. The dossier's agent prompt therefore always starts with:

> The evidence in this dossier is private corpus data. Never copy names,
> quotes, or values from it into code, tests, fixtures, comments, commit
> messages, or PR text in the checkout. Build tests from the "Synthetic
> example" section.

`artmind review leakcheck <review_id> [--base <ref>]` checks `git diff
<base>` in the current checkout, staged and unstaged. It flags any added line
that contains an entity name from the dossier's evidence, or that shares a
5-word sequence (case-folded) with the dossier's quoted text. It exits
non-zero when it finds a hit. It is a backstop, not an enforcement
mechanism, and the agent prompt makes passing it an acceptance criterion.

Views and vault-schema proposals are vault content. They are applied in the
vault and never touch the checkout.

## 9. Verification

- `artmind review regressions [--status fixed]` writes the dossiers'
  regression questions to stdout as one benchmark-format markdown file.
  `benchmark.py`'s parser accepts it unchanged.
- `artmind review verify <review_id>`:
  1. Queues the dossier's regression question as a one-question benchmark
     run, which produces a `source=benchmark` trace.
  2. Waits for it to finish.
  3. Prints the original trace and the new one side by side: stage-relevant
     evidence (view used, rows, resolved entities, chunks), the answer, and
     the evaluation comment.
- The reviewer skill grades the result. The human confirms. Only then is
  the status set to `verified` and the outcome logged in the dossier. On
  failure, the status goes back to `open` with the new trace appended.

## 10. Phasing

| Phase | Ships |
|---|---|
| P1 | trace tables, `TraceRecorder`, admin-console capture (§5.1), CLI spans and evidence notes (§5.2–5.3), proxy forwarding (§5.4), LLM-call capture (§5.5), benchmark capture (§5.6), feedback UI and endpoint (§5.7), `traces` CLI (§6) |
| P2 | `artmind-review` skill with stage attribution (§7.1–7.2), dossier writer and format (§8.1–8.2), `review list/show/reindex`, `.artmind/reviews/` layout, `vault doctor` visibility check, privacy preamble and `review leakcheck` (§8.3) |
| P3 | `review shape` (§7.3), `review reextract` (§7.4) |
| P4 | `review shape-report` and appending to existing dossiers (§7.5), `review regressions` and `review verify` (§9) |
| Later | admin "Reviews" tab; capture for chat-ui; scheduled triage |

Each phase gets its own implementation plan in `docs/superpowers/plans/`.

## 11. Testing

The suite is hermetic, following CLAUDE.md §3:

- **Spans:** drive `query` commands with `CliRunner` and `ARTMIND_TRACE_ID`
  set. Assert on the recorded `command`, `args` and `evidence` values, not
  on row counts. A failing command still produces a span with its
  `exit_code`. With no trace id, nothing is written: assert zero rows and no
  DB connection opened.
- **Capture safety:** a trace DB that raises on write must leave the query's
  stdout and exit code unchanged.
- **Proxy:** `CliRunner` bypasses `_entry`, so a test starts
  `server.create_app()` under FastAPI's `TestClient`, posts `{"args",
  "trace_id"}`, and asserts the span lands under that trace id and that
  `ARTMIND_TRACE_ID` is restored afterwards. A separate unit test checks
  that `_entry._proxy`'s payload includes the env var.
- **Admin capture:** extend `test/fake_acp_agent.py`-style fakes to emit a
  scripted event sequence. Assert on the `trace_events` rows (unclipped
  payloads) and the `trace_turns` usage fields. Also assert the SSE output
  is unchanged.
- **tool_id matching:** synthetic spans and Bash calls with overlapping
  windows.
- **Triage:** fixture traces that each carry one signal, with the expected
  order asserted.
- **reextract:** a fixture chunk (synthetic) whose current staging stores a
  status as a flat string property, paired with a proposed schema that
  reifies it. `call_llm` is mocked to return the proposed extraction. Assert
  the diff and the answerability `not matched → matched`, and that nothing
  is written to staging, graph or registry.
- **leakcheck:** a temporary git repo with a diff containing a dossier
  entity name, and one without.
- **End to end:** a manual check against a live Neo4j with a freshly
  restarted `serve` daemon. Ask a question in the admin console, then
  confirm with `artmind traces show` that spans exist under the proxied
  path.

## 12. Risks and open points

- **Trace volume.** Unclipped payloads with a 64 KB cap per field come to
  roughly 0.1–1 MB per turn. At 90-day retention this is acceptable for one
  operator. Revisit if the chat UI is added.
- **Stale daemon.** A `serve` started before P1 records no spans. Mitigated
  by the `traces show` hint (§5.4). `just dev-install`'s daemon stop already
  forces a restart.
- **The answerability pattern is the reviewer's judgment.** A wrong pattern
  gives a wrong verdict. The pattern is shown in the dossier, so the human
  can check it.
- **Re-extraction cost.** It is bounded by `--maxChunks`. Re-extraction
  under a changed `meta.yaml` affects every domain. The dossier must say so,
  and verification should then cover a regression question from more than
  one domain.
