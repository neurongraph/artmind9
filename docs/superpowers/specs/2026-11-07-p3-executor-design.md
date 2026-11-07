# P3: the executor `artmind query run`, its evidence bundle, and the agent contract

Date: 2026-11-07. Status: **spec for review**; open questions decided with the user (§13). Phase P3 of
`2026-11-02-retrieval-curation-architecture-design.md` (§5.4, §5.5, §9), refining the P3 sketch in
`2026-11-05-planner-v2-design.md` (§5–§7, §12). P3a is the executor and the bundle; P3b is the agent
contract. Inputs: round 9 (`docs/query-routing-study/16-round9-shadow-collection.md`), the handover
(`15-handover-2026-11-07.md`), bounded output (`2026-11-05-bounded-output-design.md`), the B11 proposal
(`2026-11-07-b11-entity-routes-text-proposal.md`) and the assembly pass (`2026-11-06-assembly-pass-episodes-design.md`
§7), and the planner as built on master `6f66640` (`artmind/query_plan/`).

Privacy as in the study folder: brain01 and nbs_audit_2026 appear by counts and label ids only. Every
example in this spec is invented. The bundle holds corpus text, so it lives only where step payloads already
live (stdout, `logs/results/`, the trace store), never in this checkout.

This spec does not depend on B12's details (confidence guards built in parallel). It reads B12's outcome
only as replay numbers, in the fast-path gate (§9.3).

---

## 0. In one page

Planner v2 plans every question. The agent then runs each command itself. Round 9 measured what that
plan is worth: banking agreement is 28 of 36, but **high plans are rare** (3 of 36 in banking, 3 of 18 in
brain01). Of the decision model's few confident plans, three were wrong (brain01 L038, L047; banking L037).
Most of P3's value is therefore not a fast path that skips the agent. It is **giving the agent the
evidence its plan already names, collected in one call**, for low plans as much as high ones.

P3 delivers:

1. **`artmind query run "<question>"`.** It plans, executes the plan's program in process and returns
   one **evidence bundle** within the output budget. It is proxied through `serve`; the batch form `--each`
   always runs in-process.
2. **Step bindings.** A step may read an earlier step's output through a **named output**
   (`{anchor.top_chunk}`), drawn from a fixed vocabulary that code computes. This unblocks the assembly
   pass's `event_window` and B11's candidate C.
3. **A `subplan` node** (depth 1, at most 3 clauses). It plans each clause as its own question and
   merges the evidence. P3 builds the mechanism; B11 designs when to split.
4. **Nested trace spans.** The `run` span is the parent of one span per executed step.
5. **Shadow dry execution** behind `ARTMIND_PLAN_SHADOW_RUN`. It measures the bundle beside every traced
   turn without the agent seeing it.
6. **A bundle-first agent contract.** A new ~150-line skill, `artmind-query-bundle`, calls `query run`
   for every lookup turn and treats the bundle as its starting evidence. The **fast path** (answer with no
   further command) is **off by default** and is enabled per route only once measured. The contract is
   chosen per agent profile, so classic and bundle are compared on the same build through paired
   benchmark runs, and the loser is deleted in a fixed retire slice.

Decisions taken with the user for this spec (2026-11-07; do not reopen without evidence):

| # | Decision |
|---|---|
| D1 | **Bundle-first, fast path off.** Every lookup turn calls `query run`; every plan's program runs, low plans included; the agent may add commands. The fast path is a per-route setting, off until §9.3's gate passes for that route. |
| D2 | **Step bindings are named outputs** `{node.output}` from a code-defined vocabulary, checked when `routes.yaml` loads. There are no free expressions and no JSON paths. |
| D3 | **B11 gets a `subplan` node**, depth 1 and at most 3 clauses, built and tested with a given `clauses` list. The clause splitter (when to split) stays B11's to design and measure. |
| D4 | **Steps run through a capture mode in `_echo_json`**, invoked as real Click commands in the same thread: no nested `CliRunner` and no second `serve` lock. `par` stays sequential in P3. |
| D5 | **The contract is a profile setting.** `AgentProfile.contract` (`classic` \| `bundle`) picks the skill. The default comes from `ARTMIND_AGENT_CONTRACT` in the vault's config. A benchmark run carries its own `contract`. Every trace turn records it. One retire slice deletes the losing contract. Classic stays the default in every vault until a paired run passes in both vaults (§13 Q8). |

## 1. Scope

| In | Out |
|---|---|
| `query run` (single question, `--plan`, `--each`), the executor, step facts, guards, limits, deadline, errors | New routes or route changes (including converting `structured`, §13 Q1); B12's guards; B11's clause splitter and its cue |
| Step bindings (grammar, load checks, runtime binding) and the `subplan` node | The assembly pass (PA1–PA2, episodes for multi-day happenings; they run beside P3) and the `episode` route's programs, which are PA3's in the assembly-pass spec. P3 supplies the bindings that `event_window` and `episode_lookup` need (§4) |
| The bundle: adapters per command, evidence ranking, budget, handle, `query page --list` | The admin UI's plan card and evidence panel (P4) |
| Nested spans; `run` span fields; trace analysis for run turns | Gap classification and proposal files (P5); the bundle only emits `gaps` |
| Shadow dry execution behind its flag | Dry runs of curation/update actions (P5, §13 Q5) |
| `previous` resolution (flag, `ARTMIND_PREVIOUS_CONTEXT`, trace store) | A learned or LLM classifier |
| The `artmind-query-bundle` skill, profile `contract`, benchmark `contract`, paired comparison | Parallel `par` (§13 Q3) |
| Gates for P3a, P3b and the per-route fast path; the retire slice | |

## 2. As built: what P3 starts from

- **The plan payload** (`planner.plan`, `plan_version: 2`) already carries `program`: rendered node JSON
  (`_node_json`) whose `run` strings have every plan-time slot filled (`filled: true/false`). Its `map`s are
  expanded (`items`, `expanded`), a one-domain command over several domains becomes a `per_domain` `par`,
  and `when`, `optional` and `needs` are kept. It also carries `guards` (`_guard_json`), `confidence`,
  `scope`, `slots`, `gaps`, `action`, `answer_from`, `steps` and `fallbacks`. **The executor consumes this
  JSON.** It never re-reads `routes.yaml` and never renders plan-time slots: *the planner fills, the
  executor binds.*
- **Program grammar** (`program.py`): `run`, `par`, `map`, `pick`, `page`, `when`; guards with `escalate`,
  `replace`, `retry_with`, `widen_domains`, `page`, `run`; step predicates (`PREDICATE_TYPES`); limits
  (12 written run nodes, 40 executed calls, 3 levels).
- **Step facts are partly computed already.** `reach.step_documents` finds document dicts in any payload,
  and `reach.needle_answers_alone` evaluates the B2 needle rule. P3 moves both into the executor's facts
  module and makes reach a caller of it (S1).
- **`serve`** runs each request through `CliRunner` under `_invoke_lock`, a non-reentrant
  `threading.Lock`, because CliRunner redirects process-wide stdout. It applies the caller's `trace_id`
  and `output_budget` as contextvars and never reads its own environment.
- **Bounded output** is applied in `_echo_json`. `result_store.fit` cuts a payload's `LIST_KEY` list to
  the budget, stores the whole payload under `logs/results/<handle>.json`, and `query page <handle>` pages
  it.
- **Spans.** `TracedGroup.invoke` opens one span per `query` invocation and runs nested invocations
  inside the open span (`_active`). `trace_spans` has no parent column. `TracedDbGroup` traces the
  read-only `db` subcommands.
- **Previous turn.** `query plan --previous FILE|-` runs in-process (`_entry._proxiable`). Shadow plans
  and replay rebuild the previous turn from the trace store (`previous.previous_from_trace`). Round 9:
  `previous_used` was false on 0 of 4 brain01 follow-ups.
- **Guards actually used in `routes.yaml` today:** `escalate`, `replace: search`, guard `page`, and
  converted-v1 `run`. No route uses `retry_with` or `widen_domains`.

## 3. `artmind query run`

### 3.1 Command

```
artmind query run "<question>" [--domain D]… [--exclude DOC]… [--previous FILE|-]
                  [--classifier auto|lexical|decision] [--maxSteps 12] [--deadline 20]
                  [--compact]
artmind query run --plan FILE|-   [--maxSteps …] [--deadline …] [--compact]
artmind query run --each FILE     [--domain D]… [--maxItems 50] [--compact]
```

- Read-only. It is a `query` command, so it is proxied and traced like the rest. It does not belong in
  `WRITE_COMMANDS`: the contract test's read-only check must pass because no step can write (§3.3).
- `--plan` executes a given plan payload (a recorded shadow plan, a replay plan, a test fixture). Without
  it, `query run` calls `planner.plan(...)` with the same arguments `query plan` takes, then executes.
- `--maxSteps` caps **executed** run calls, including pages, guard runs and subplan steps (default 12,
  1–40 = `MAX_EXECUTED_RUN_CALLS`). `--deadline` is a wall-clock budget in seconds for the whole run (default
  20, 1–120, env `ARTMIND_RUN_DEADLINE_S`).
- `LIST_KEY["query run"] = "evidence"`.

### 3.2 Proxying and the serve lock

| Invocation | Where it runs | Why |
|---|---|---|
| `query run "<q>"` with no file arguments | proxied to `serve` when its vault matches (`_entry` rules) | warm imports; the plan and every step run inside one `serve` request |
| `--previous FILE\|-`, `--plan FILE\|-`, `--each FILE` | in-process (`_proxiable` gains `query run` with these flags) | the daemon cannot read the caller's files or stdin; `--each` would hold the lock for minutes |
| `ARTMIND_PREVIOUS_CONTEXT` set | proxied; `_entry` reads the file and sends its contents as `QueryRequest.previous` (≤ 16 KB, else dropped with an `artmind:` note) | same pattern as `trace_id` and `output_budget`: `serve` applies it as a contextvar and never reads its own env |

A proxied `query run` holds `_invoke_lock` for its whole program, and other queries to that vault's
`serve` wait. That is accepted: a run replaces the several agent calls that would have queued one by one,
and `--deadline` bounds it. Shadow dry runs (§7) never go through `serve`.

### 3.3 Running a step: capture mode (D4)

`artmind/query_run/capture.py` holds a contextvar `_capture`. With it set:

1. The executor calls `cli.main(args=argv, prog_name="artmind", standalone_mode=False)` in its own
   thread. No `CliRunner` is involved, so nothing is redirected and the `serve` lock is not taken again.
2. `_echo_json` checks the capture before budgeting. It builds the envelope as usual; it then hands the
   **full, uncut** payload to the capture, calls `observe_output` (so the child span gets its evidence,
   with `payload_chars` set and `shown_chars`/`handle` null) and **returns without echoing**.
3. A command that raises `click.ClickException` becomes a step error with its exit code and first line.
   A store failure is mapped by `exit_on_store_failure` to exit 3. `click.exceptions.Exit` and `SystemExit`
   are read for their code. Any other exception is a step error (exit 1) with its type name.
   `KeyboardInterrupt` propagates.
4. A command that finishes without calling `_echo_json` (text output) is a step error, `no_json`. The
   routes test already requires `--compact` on every template, so this only catches drift.

Step stderr is not captured: the domain guard and other notes already reach the payload's `warnings`.

**Allowed commands.** A program step must be `artmind query …` (any subcommand except `run`, `plan` and
`page`), or one of `TracedDbGroup.traced_commands` (the read-only `db` set: `db bridge` without `confirm`
or `clear`, `db list`, `db schema`, `db sql`, `db timeline`, `db review`). This is checked when `routes.yaml`
loads (a new routes test) and again at run time. A step outside the set is refused as `not_allowed`.

`StepResult = {id, argv, exit_code, error, payload, facts, ms, chars, skipped}`, where `skipped` is one
of `when`, `unbound`, `unfilled`, `needs`, `limit`, `deadline`, `short_circuit`, `llm_shadow`, `depth`,
or null.

### 3.4 Step facts

`artmind/query_run/facts.py` computes every name in `PREDICATE_TYPES` from a step's payload. Each fact
has one rule, keyed by command where the payload differs:

| Fact | Rule |
|---|---|
| `rows` | length of the command's main list (`LIST_KEY`, else `rows`/`results`/`documents`/`observations`) |
| `rows_total`, `truncated` | envelope fields when present, else `rows` / false |
| `distinct_documents` | distinct documents among `step_documents(payload)` (moved from `reach`) |
| `docs_cited` | same, restricted to rows that carry text (passages, chunks) |
| `max_relevance`, `score_drop` | `relevance` of ranks 1 and 2 of passage rows; `score_drop` null when fewer than two (the B2 rule reads null as −∞) |
| `payload_chars` | the captured payload's length |
| `entity_docs` | `entity-context` / `entity-history`: the entity's document count |
| `rows_below_k` | `rows < k` for the step's `--topK` / `--limit` |
| `twins`, `stale`, `view_status`, `history_matches` | as v1 |
| `persisted` | always false for executor steps |

For a `par` or `pick` node, facts are computed over the union of its children's rows. A fact that cannot
be computed reads 0 or false, as plan facts do. `facts.py` is the only place these rules live: replay
`--reach`, `traces.analysis` and the executor all call it.

### 3.5 Executing a program

The executor walks the plan's `program` in document order with a cursor and a step budget.

| Node | Execution |
|---|---|
| `run` | if `when` is false → `skipped: when`; if `filled` is false → `skipped: unfilled` (an `optional` node silently, otherwise it is recorded and may escalate, §3.8); if a binding does not fill → `skipped: unbound` (§4); else run the step (§3.3) |
| `per_domain` `par` | its children run in order, each a `run` |
| `par` | children in order (sequential in P3); result = list of child results |
| `map` | the plan's `expanded` items in order (`max` already applied by the planner); a map `over` a **bound list** (§4.2) expands at run time, up to `max` |
| `pick` | `union` (merge rows by evidence/entity/document id, keep the first rank, record count `discrepancies`); `best_of` (the child with the most rows that satisfy `score`); `first_ok` (the first child with `rows > 0`) |
| `page` | call `query page <handle> --from N` on the source step's stored result until `rows_total` or `k`; each page counts against `--maxSteps`. A captured payload is never cut by the budget, so page nodes act only on a step whose own payload says `truncated` (a command-side `--limit` cut): the executor re-runs it with a larger `--limit` up to the command's maximum, or else marks it non-exhaustive |
| `subplan` | §5 |

**The B2 short-circuit.** A `pick` whose `when` names facts of only some children of the preceding
`par`, and which already evaluates false once those children have run, means *the needle answers alone*.
The executor then skips the remaining children (`skipped: short_circuit`). In `search`, a clear needle
winner then saves the survey call. Reach parity (S2's gate) checks that this gives the same documents as
`reach.needle_answers_alone`.

**Guards** are evaluated after their `on` node, in order; the first match fires and each guard fires at
most once. Every firing goes into the bundle's `path`.

| Action | Executor | Planner change |
|---|---|---|
| `escalate` | stop; `escalate = {reason: "guard", node, predicate, suggested}` | none |
| `replace: <route>` | discard this program's results, run the replacement program once; a replacement cannot replace again | the guard JSON gains `then.program`: the replacement route's program **rendered with the same context** (only for routes named by guards on the chosen program) |
| `run` (converted v1 fallback) | run the pre-rendered command once | none (already rendered) |
| `page` | §3.5 `page` on the `on` node | none |
| `retry_with`, `widen_domains` | run the guard's pre-rendered `then.cmd` once | the planner renders it (slot changed / all domains); unused by any route today, so this is tested on a fixture only |

**Refinements made while planning S1–S4** (`docs/superpowers/plans/2026-11-07-p3a-executor-s1-s4.md`):

- The guard JSON stays as it is. The rendered replacement programs go into a top-level plan key, `replacements: {route: {program, guards}}`, which holds every route a guard on the chosen program names, plus `search`.
- A program with a non-optional unfilled node runs `replacements.search` instead (recorded in `path` as `replace`, reason `unfilled`). It escalates `unfilled` only when there is no replacement. This matches the planner, which gives a low unfilled plan the search steps.
- The guard actions `run`, `retry_with` and `widen_domains` escalate (`reason: guard`, `action`) until a route uses them; no packaged route does today.
- A run node whose `needs:` is unmet is marked `needs_unmet: true` in the node JSON and is skipped by the executor (`skipped: needs`).
- Evidence is ranked by RRF over every successful step (§6.3's "answer nodes" are the steps a `pick` kept, which are the same steps on every packaged program).

### 3.6 Limits and the deadline

- **Steps.** Before each executed call, `executed < --maxSteps`, else `skipped: limit` for this node and
  every later one, and `escalate.reason = "limit"`.
- **Deadline.** Checked before each call. Past it, the remaining nodes are `skipped: deadline`,
  `coverage.partial = true` and `escalate.reason = "deadline"`. A step already running is not interrupted:
  Neo4j queries and LLM calls keep their own timeouts (views 60 s, the decision model's
  `ARTMIND_DECISION_TIMEOUT`). §13 Q2 covers a per-step timeout.
- **Static limits** stay as P2 defined them and are checked at load (12 written run nodes, 40 executed
  calls, 3 levels), plus subplan depth 1 (§5).

### 3.7 Errors

- A failing step is recorded in `path` (`exit_code`, `error`), and the program continues. A later node
  that needs its output is `skipped: unbound`, a `pick` uses the children that succeeded, and a guard on it
  sees `rows == 0`.
- If no evidence-producing step succeeded, `escalate.reason = "step_error"`.
- **Exit code of `query run`**: 0 whenever a bundle was produced, even with failed steps. 3 only when
  every executed step failed on a store (CLI convention). 1 for bad input (an unreadable `--plan`, a plan
  of the wrong `plan_version`). The bundle, not the exit code, is what the agent reads.
- A plan with `program: []` (a non-retrieval intent, `answer_from: previous`, or an `action`) produces a
  bundle with no evidence, `escalate = {reason: "no_lookup", answer_from, action}` and exit 0.

### 3.8 Escalation reasons

`guard`, `limit`, `deadline`, `step_error`, `unfilled` (a non-optional node could not be filled, e.g. no
entity resolved), `unbound`, `no_evidence` (every answer node returned zero rows with no guard firing),
`no_lookup`. Each carries `suggested`: the plan's `suggested_steps` and `fallbacks`, rendered, so the
reasoning path starts from code's best guess rather than from a blank page.

## 4. Step bindings (D2)

### 4.1 Grammar

In a run template, a placeholder `{<node>.<output>}` names an output of an earlier node. Plan-time
placeholders (`{domains}`, `{search_term}`, …) are unchanged.

```yaml
# event_window (assembly pass §7), shapes only:
program:
  - {id: anchor, run: "artmind query vector-text --shape brief --domain {domains} --topK 5 {search_term} --compact"}
  - {id: around, run: "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"}
  - id: dated
    map:
      over: anchor.window_docs
      as: doc
      max: 6
      do: {id: day, run: "artmind query graph pattern10 --domain {domains} --documentName {doc} --compact"}
guards:
  - {"on": anchor, if: "rows == 0", then: {replace: search}}
```

The rules are checked when `routes.yaml` loads (`program.py`):

1. `<node>` is a `run` or `pick` node that comes **before** the binding node in document order and is
   not inside the same `par` (siblings have no order).
2. `<output>` is in `BIND_OUTPUTS` (below), and its type fits the use: a **scalar** output in a template,
   a **list** output in `map.over` or in an option documented as comma-split (`--idList`, `--domain`).
3. A binding placeholder is a **whole token** of the template (`--idList {anchor.top_chunk}`, never
   `"x{anchor.top_chunk}"`). The executor splits the rendered command with `shlex` and replaces whole argv
   elements, so a bound value becomes exactly one argument and is never re-parsed as shell text.
4. A binding inside a `map.do` may also use the map's `as` name, as today.

### 4.2 Output vocabulary

Computed in `facts.py` from the producing step's payload, like the predicates. A new output needs code,
a test and a line in this table.

| Output | Type | Value |
|---|---|---|
| `top_chunk` | scalar | chunk id of the best-ranked text row |
| `top_passage` | scalar | passage id of the best-ranked passage row |
| `top_doc` | scalar | document name of the best-ranked row (the form `--documentName` accepts) |
| `top_date` | scalar | that row's document date (ISO) |
| `top_entity` | scalar | entity id of the best-ranked entity row (`entity-resolve`, graph rows) |
| `chunk_ids` | list | chunk ids of text rows, ranked, de-duplicated |
| `doc_names` | list | document names, ranked, de-duplicated |
| `entity_ids` | list | entity ids, ranked, de-duplicated |
| `window_docs` | list | documents dated within `[top_date, top_date + window]`, where `window` is the plan's date-window slot length or 3 days by default (`BIND_WINDOW_DAYS`); read from the step's own rows, no extra query |

A binding that comes out empty (the producer failed, returned no rows, or has no such field) gives
`skipped: unbound`. A list binding feeding a map with no items skips the map. Neither is an error: a guard
on the producer (`rows == 0`) decides what happens next.

### 4.3 Planner changes

- `routes.render` treats `{x.y}` placeholders as **bound**: it leaves them in the command, and they never
  count as unfilled.
- `_node_json` adds `binds: ["anchor.top_chunk", …]` to a node whose command holds bindings, and
  `map.over_bound: "anchor.window_docs"` to a map over a bound list (with `items: []`).
- The flattened v1 `steps` list shows a bound command with its placeholder left in, so v1 readers and the
  agent's reasoning path see the shape.
- No route in `routes.yaml` uses a binding in P3. The first users are PA3 (`episode_lookup`'s `show` step,
  `event_window`) and B11. P3 tests bindings on fixture routes loaded by the test (§10).

## 5. The `subplan` node (D3)

```yaml
- id: parts
  map:
    over: clauses
    as: clause
    max: 3
    do: {id: part, subplan: "{clause}"}
- {id: all, pick: {from: parts, how: union}}
```

- `subplan: "<template>"` is a new node kind. Its template renders to a question. The executor calls
  `planner.plan(question, domains=<the parent plan's selected domains>, classifier=<the parent's backend>,
  previous=None)`, executes the sub-plan's program with the parent's remaining step budget and deadline,
  and returns its evidence as the node's rows.
- **Depth 1.** A sub-plan whose program contains a `subplan` node is refused at run time
  (`skipped: depth`), and a route whose `subplan` template could reach itself is refused at load. Each map
  has `max ≤ 3` (load rule for maps whose `do` is a subplan).
- **Clauses** is a new list slot (`LIST_SLOTS` gains `clauses`). In P3 it fills only from a given plan
  (`--plan`, tests). B11 adds the planner rule that fills it and the plan fact that selects a program using
  it.
- **Bundle.** Each sub-plan's evidence rows carry `clause: <i>`. `plan.subplans` lists each clause's
  `route`, `confidence` and `escalate` (one line each). A sub-plan's escalation does not escalate the
  parent unless every sub-plan escalated.
- **Cost.** Each sub-plan makes one classifier call (lexical: about 0 s; decision model: 0.5–1.3 s in
  round 9). With 3 clauses on the decision model the plan stage alone can exceed the speed gate. That is
  recorded as `timing.plan_ms` per sub-plan, and B11 decides whether its programs use lexical sub-plans.

## 6. The bundle

### 6.1 Shape

```json
{
  "command": "query run", "warnings": [], "next": null,
  "bundle_version": 1,
  "mode": "reasoning",
  "plan": {"intent": "retrieve", "route": "search", "program_kind": "cross_document",
           "confidence": {"level": "low", "score": 0.61, "threshold": 0.7, "weakest": "slot_quality"},
           "classifier": "decision", "subplans": []},
  "scope": {"domains": ["banking.products"], "excluded_documents": [], "rules": []},
  "path": [{"id": "needle", "cmd": "artmind query vector-text …", "exit_code": 0, "rows": 10,
            "ms": 410, "chars": 14800, "skipped": null, "facts": {"score_drop": 0.01, "distinct_documents": 6}},
           {"id": "wide", "cmd": "artmind query survey …", "exit_code": 0, "rows": 50, "ms": 920, "chars": 22100},
           {"id": "u", "kind": "pick", "rows": 54}],
  "evidence": [{"eid": "e1", "kind": "passage", "text": "…", "passage_id": "…", "chunk_id": "…",
                "rank": 1, "from": ["needle"], "clause": null,
                "doc": {"id": "…", "title": "…", "domain": "…", "date": "2026-02-01",
                        "status": "current", "executed": null},
                "suspect_conversion": false}],
  "documents": [{"rank": 1, "doc_id": "…", "title": "…", "date": "…", "status": "current",
                 "gist": "…", "gist_source": "passage", "evidence": ["e1"]}],
  "facts": [{"fid": "f1", "kind": "graph_row", "from": "s1", "row": {"name": "…", "class": "…"},
             "evidence": ["e3"]}],
  "guides": [{"kind": "topic_page", "slug": "…", "stale": false, "cites": ["e1"]}],
  "coverage": {"documents_read": 12, "documents_total": 26, "found": 7, "exhaustive": false,
               "partial": false, "probes": [], "depth": 10},
  "gaps": [],
  "escalate": {"reason": "no_evidence", "node": "u", "suggested": ["artmind query …"]},
  "offer": null,
  "timing": {"plan_ms": 640, "exec_ms": 1330},
  "rows_total": 54, "truncated": true, "handle": "a1b2c3d4e5f6"
}
```

Compared with the P2 sketch (planner v2 §6.2), this adds the CLI envelope, `mode` (§8.3), `path` with each
step's facts, `from`/`rank`/`clause` on evidence rows, `timing`, and the bounded-output fields at the top
level. The rest is unchanged.

### 6.2 Adapters

`artmind/query_run/adapters.py` maps each command's payload to bundle rows: `evidence` (text with a
document), `documents` (one row per document, brief), `facts` (graph or SQL rows), `guides` (topic pages,
insights, views as steering). There is one adapter per command path that `routes.yaml` can emit:

| Commands | Produce |
|---|---|
| `vector-text`, `chunks`, `survey --shape brief` passage rows | evidence (passage/chunk) |
| `survey` document rows (gist, line) | documents |
| `entity-context`, `entity-resolve`, `graph pattern1/2/5/6/7`, `graph hierarchy`, `graph conflicts`, `graph text2cypher`, `views run` | facts (brief: id, name, class, one-line description, evidence ids); their chunk rows → evidence |
| `entity-history`, `document-history`, `graph pattern10` | evidence (chunk text with `status` current/superseded) + facts (observations) |
| `text2sql`, `db sql` | facts (SQL rows, `source_tables`) |
| `db bridge`, `db schema`, `domains-overview` | facts (one row each; never evidence) |
| `insights list`, `topics show` | guides |

A routes test fails when a template's command has no adapter. An unknown command at run time (a `--plan`
from an older build) goes through a generic adapter that puts its capped rows into `facts` and adds a warning.

### 6.3 Ranking and assembly

Scores from different commands cannot be compared (vector relevance against survey RRF against graph
order), so evidence is ranked by **reciprocal-rank fusion over the answer nodes' own ranks** (k = 60). The
answer nodes are the children of the last executed `pick`, or else every run node. Duplicates (same passage
id, else same chunk id) merge, keep the best rank and list every `from`. Documents are ranked the same way
over `documents` rows and the documents of evidence rows.

The bundle is **assembled to a budget** after every step has run uncut (planner v2 §6.2, unchanged in
spirit):

1. `plan`, `scope`, `path` (each step's `cmd` shortened to 300 characters), `coverage`, `gaps`,
   `escalate` and `timing` are always placed.
2. Evidence: the top 10 by rank, then one per further document (the evidence cap of planner v2 §11), each
   passage text capped at 1,200 characters.
3. `documents` rows, then `facts` (brief rows; a hub entity's context never placed whole), then `guides`.
4. The budget is `ARTMIND_BUNDLE_BUDGET`, else the caller's output budget (`ARTMIND_OUTPUT_BUDGET`, as
   `serve` receives it), else **24,000**. A bundle is always bounded, even in a terminal with no output
   budget, because it is made for the agent.
5. What does not fit goes to `logs/results/<handle>.json` as the **full** bundle (every evidence,
   document and fact row). The printed bundle sets `truncated`, `rows_total` (evidence rows) and `handle`,
   and `next` is `artmind query page <handle> --list evidence --from <n>`. `warnings` says what was cut.
6. `query page` gains `--list NAME` (default the declared `LIST_KEY`), so the agent can also page
   `documents` or `facts` of a stored bundle. The cut is applied by the bundle assembler, not by
   `result_store.fit`; `_echo_json` then finds the bundle within budget.

### 6.4 Coverage, counts, latest

These follow planner v2 §6.4–§6.5 and are unchanged here. A program of kind enumeration/count that did
not page to `rows_total` sets `coverage.exhaustive = false`, and the bundle carries no count fact. `latest`
puts the newest family document first with `latest: true`. `coverage.probes` and `depth` are set by the
`existence_concept` and `absence` programs.

## 7. Nested spans and shadow dry execution

### 7.1 Spans

- `trace_spans` gains `parent_span_id TEXT` (migration like `payload_chars`). `TracedGroup.invoke`, when a
  span is already active **and** capture mode is on, opens a **child** span (`parent_span_id` = the active
  span) instead of running inside the parent. Without capture (a nested invocation for another reason),
  today's behaviour is kept. `TracedDbGroup` does the same for `db` steps.
- The `run` span gains columns `run_mode` (`fast` | `reasoning`), `escalate_reason` and `bundle_chars`.
  Its `evidence` is the bundle's evidence summary (documents, chunks), computed by
  `traces.evidence.summarize` from the bundle.
- **Analysis** (`artmind/traces/analysis.py`):
  - top-level spans are the agent's calls; child spans are the executor's;
  - `agreement` on a run turn is true by construction and is reported separately (`run_turns`);
  - new per-turn signals: `added_after_run` (top-level spans after the run span), `overridden` (on a
    fast-mode bundle, the agent still ran commands) and `skill_violation` (a `query plan` span on a
    bundle-contract turn, §13 Q9);
  - `traces show` indents child spans under their run.

### 7.2 Shadow dry execution

`ARTMIND_PLAN_SHADOW_RUN=0|1|all` (default 0):

- With `1`, after a shadow plan finishes, the shadow thread executes it (the executor with the plan,
  in-process in the UI process, trace override None so no spans are written). It records a `run_shadow`
  trace event with: `path` (ids, commands, rows, ms, skipped), `timing`, `escalate`, `bundle_chars`, the
  bundle's document ids (ranked), and `covers_agent`. `covers_agent` is the share of documents in the
  agent's own spans that the bundle's placed evidence also holds; it is computed when the turn finishes,
  from ids only.
- Steps that make an LLM call (`graph text2cypher`, `text2sql`) are `skipped: llm_shadow` under `1`;
  `all` runs them too (cost and privacy: the question goes to the extraction provider again).
- It shares the shadow limits (`MAX_RUNNING`, `LATE_WAIT_S`). A dry run still going when the late wait
  ends records `status: timeout`. It never delays a turn.
- `traces stats` gains a `run_shadow` block: count, p50/p90 `exec_ms` by program kind, escalation rate by
  route, and mean `covers_agent` by route and confidence level. That block is the evidence for P3a's speed
  gate on live turns and for §9.3's fast-path gate.

## 8. Agent contract (P3b)

### 8.1 Where the contract is chosen (D5)

- `AgentProfile` (`artmind/webui/profiles.py`) gains `contract: "classic" | "bundle"`. The profile builds
  its `skills` tuple and `system_append` from it: classic keeps `artmind-query` and today's text; bundle
  uses `artmind-query-bundle` and a short append that names `query run`. **Nothing in the CLI, planner or
  executor reads the contract.**
- The admin console and chat UI take their default from `ARTMIND_AGENT_CONTRACT` in the vault's
  `.artmind/config.env` (default `classic` until the P3b gate passes).
- A benchmark run carries `contract` (`benchmark_runs.contract`, a field in the admin benchmark form and
  in the driver), so the same question file runs twice on one build without a restart.
- `trace_turns.contract` records it on every turn.
- **Why a new skill folder rather than rewriting `artmind-query`:** vault skills are symlinks into this
  checkout (CLAUDE.md §2), so an edit to `artmind-query` is live in every vault at once. The classic skill
  stays where it is, untouched. The new skill reaches a vault after `artmind init` runs there (a step of S9).

### 8.2 The `artmind-query-bundle` skill (~150 lines)

| Section | Lines | Content |
|---|---|---|
| Frontmatter + purpose | 10 | when to use; grounding rule |
| Does this turn need a lookup? | 15 | architecture §5.5 4a: reshapes, deliverables, curation, status and ops are handled without `query run`; new subjects, "check again" and follow-ups call it |
| The one call | 15 | `artmind query run "<the user's question>" --compact` (add `--domain` only when the user named one; never `--previous`, which the UI passes) |
| Reading a bundle | 30 | `evidence` is the only thing to cite; `documents` for coverage; `facts` linked by `evidence`; `guides` steer and are never cited; `handle`/`next` to page; `coverage` numbers |
| Mode | 20 | `fast`: answer from the bundle; run a command only if the bundle visibly lacks a named part of the question (traced as `overridden`). `reasoning`: the bundle is the starting evidence; read `escalate.reason` and `suggested`, then the generated routing table, then run what is missing |
| Answer contract | 25 | §8.4 |
| References | 15 | `references/routes.md` (generated from `routes.yaml`: route, description, programs, commands), `references/commands.md` (generated from the Click tree for the query commands), and the existing temporal, conflicts, hierarchy and structured-store references, shared with the classic skill by symlink |
| What not to do | 10 | no `query plan`; no shell loops over commands (`--each` exists); no citing `gist_source` other than `passage` |

Tests check the skill against data, not phrases: every command it names exists in the Click tree, every
route in `routes.md` exists in `routes.yaml`, and the generated references match their generators (a test
fails when they are stale; `just dev-gen-query-refs` rewrites them).

### 8.3 Fast path versus reasoning path

`mode` is set by code in the bundle, never by the agent:

```
fast  iff  route ∈ ARTMIND_RUN_FAST_ROUTES  and  plan.confidence.level == "high"
           and escalate is null  and  no step failed  and  (exhaustive or not a count program)
reasoning  otherwise
```

`ARTMIND_RUN_FAST_ROUTES` (comma list in config.env, default empty) is the per-route switch of D1. Given
round 9 (high plans rare, three confident plans wrong), every bundle is `reasoning` until §9.3 enables a
route. Even then, the skill lets the agent override a fast bundle, and the override is traced.

### 8.4 Answer contract

Planner v2 §7.3, unchanged:

- latest applicable source first;
- incompleteness stated with `coverage` numbers, never "at least";
- stated and implied occurrences kept apart;
- relations counted by direction;
- widest granularity;
- scope narrowing stated.

Two additions:

- On the reasoning path, when the agent's added commands changed the answer, the answer is grounded in
  the evidence those commands returned. The bundle's evidence is not cited for what it does not say.
- When `escalate.reason` is `limit`, `deadline` or `step_error` and the agent did not complete the
  evidence, the answer says what was not read, with numbers.

Provenance stays the UI's job (the spans hold every document, child spans included).

### 8.5 `previous`

`query run` and `query plan` resolve the previous turn in this order:

1. `--previous FILE|-`;
2. `QueryRequest.previous` (proxied) or the file named by `ARTMIND_PREVIOUS_CONTEXT` (in-process);
3. the trace store: `previous.previous_from_trace(current trace id)`, now also reading the previous
   turn's `run` span for `documents` and resolved entities;
4. none.

The UIs write the per-session file from the conversation store (`artmind/conversations/`) at the start of
each turn: question, the previous plan's intent/route/entities from its run span, the documents its bundle
placed, and the offer (`previous.extract_offer` on the stored answer). This uses the same pattern and the
same lifetime as `ARTMIND_TRACE_CONTEXT`. Source 3 alone already covers admin and benchmark turns (both
traced), so the file mainly serves the chat UI and makes the follow-up case explicit. Round 9's 0 of 4
`previous_used` is the baseline that S8's gate measures against.

The bundle's `offer` stays null in P3. Offers keep coming from the answer text (§13 Q6).

### 8.6 `--each` (batch)

Planner v2 §7.5, unchanged in content. It always runs in-process (§3.2). It caps `--maxItems` (default 50)
and characters per item, opens one child span per item under the run span, writes one bundle per item as
JSONL to `logs/runs/<handle>.jsonl`, and prints a summary payload (items, escalations, `exec_ms` p50/p90,
the file's handle). The claim is used as written, because the catalogue has no `terms` yet. The executor
makes no LLM call.

## 9. Measurement and gates

### 9.1 P3a gate (executor)

Measured with `artmind plan replay --run` (new; S4). It replays labels, executes each label's first plan
in a warm process through the executor and reports per label: `plan_ms`, `exec_ms`, executed steps,
escalation, `bundle_reached` (expected documents in the placed bundle) and `steps_reached` (in any step,
today's `--reach`). Run inside banking_corpus (36 labels) and brain01 (49), lexical and decision.

| Gate | Threshold |
|---|---|
| Plan latency | p90 `plan_ms` ≤ 1.5 s with lexical; with the decision model, reported (round 9 decision latency alone is 0.5–1.3 s) |
| Execution latency | p90 `exec_ms` ≤ 3 s over plans with 1–4 executed steps; longer programs and `text2cypher`/`text2sql` steps reported apart (LLM latency) |
| Bundle keeps the evidence | `bundle_reached` ≥ 95 % of `steps_reached` on banking (B3–B9 baseline: 76 of 101 decision) |
| No regressions | `--reach` numbers unchanged after S1 (facts moved) and after the short-circuit (S2) |
| Live | one benchmark run per vault with `ARTMIND_PLAN_SHADOW_RUN=1`: every turn has a `run_shadow` event, none delayed, p90 `exec_ms` within the gate |

### 9.2 P3b gate (agent contract), through paired benchmark runs

Benchmark runs are the user's way of collecting now, so they are also the comparison. On one build, the
same question file runs twice, `contract: classic` and `contract: bundle`: banking_corpus's 36 questions and
brain01's benchmark file (10 questions). The user rates both runs in the admin UI as today.
`artmind traces stats --compareRuns A B` (new; S10) pairs turns by `benchmark_seq` and prints, per vault:

| Measure | Bundle must be |
|---|---|
| Rated good | ≥ classic − 1 per vault (no more than one question lost), and no question rated good in classic rated bad in bundle without a dossier explaining it |
| Answer reach (banking, labels with `expected_documents`) | documents cited by the answer's spans ≥ classic |
| Commands per turn | median top-level spans lower than classic |
| Turn latency | median `duration_s` lower than classic |
| Cost | median `cost_usd` not higher |

When both vaults pass in one paired run, `ARTMIND_AGENT_CONTRACT` defaults to `bundle` and the retire slice (S13) is due. A pass in one vault only counts as a fail: every vault stays `classic` (§13 Q8).
This **replaces** the architecture §9 P3 gate's "fast-path share ≥ 50 % of rated-good turns", which round 9
makes unreachable without risk. The fast path gets its own per-route gate (§9.3).

### 9.3 Fast-path gate (per route)

A route joins `ARTMIND_RUN_FAST_ROUTES` when all of these hold:

- at least 5 traced high-plan turns of that route have `run_shadow` events with mean `covers_agent`
  ≥ 0.9;
- replay shows no `wrong` at high for any label of that route, on both classifiers in use (this is where
  B12's effect is read, as numbers only);
- the paired benchmark shows no rated-good question of that route lost when it is fast.

`traces stats` prints the three per route, so the decision is a reading, not a study.

## 10. Testing

Hermetic, no Neo4j:

- **Capture:** a captured `_echo_json` returns the uncut payload, prints nothing, records a span with
  `payload_chars`; ClickException, store failure, Exit and text-only commands map as in §3.3; nested
  capture inside a `serve`-style `CliRunner` invoke works with the lock held once.
- **Facts:** every `PREDICATE_TYPES` name on recorded payloads of each adapter command; reach parity.
- **Executor:** each node kind on recorded step payloads (a step function injected, as `reach_label`
  does); guards in order and once each; replace runs the pre-rendered program; short-circuit; limits and
  deadline (a fake clock); errors continue and escalate as §3.7; `program: []`.
- **Bindings:** load rules (earlier node, not a sibling, known output, type fit, whole token); runtime
  binding with argv-level substitution (a value containing spaces, quotes and `;` stays one argument);
  unbound skips; map over a bound list.
- **Subplan:** depth refusal, max 3, evidence tagged by clause, partial escalation.
- **Bundle:** budget assembly keeps the top-ranked evidence; RRF over steps; handle stores the full bundle;
  `query page --list documents`; every routes.yaml command has an adapter; no write command reachable;
  `db` steps only from the read-only set.
- **Spans:** child spans have the run span as parent; analysis counts top-level vs child; `added_after_run`
  and `overridden`.
- **Contract plumbing:** a profile builds skills and append from `contract`; the benchmark run stores and
  passes it; `trace_turns.contract` recorded; `_entry` forwards `previous` (size cap); resolution order of
  §8.5.
- **Skill:** commands and routes named exist; generated references not stale.
- **Plugin fixtures:** `query run` is new and the plugin does not call it, so no fixture changes.
  `query page --list` keeps the default output unchanged.

Live (each gate): `ARTMIND_NO_PROXY=1` and a restarted `serve` per CLAUDE.md §1, from inside each vault.

## 11. Slices (build order)

Each slice is one worktree branch for a Sonnet implementer, cut from master (or the integration branch
the user names; agent worktrees are cut from master, so merge it first). Each lists what it touches, its
tests and its gate. The parent session reviews every diff.

| # | Slice | Touches | Tests | Gate |
|---|---|---|---|---|
| S1 | **Capture and facts.** `query_run/capture.py`; `_echo_json` capture branch; `invoke_step(argv) -> StepResult`; `query_run/facts.py` with every predicate and the moved `step_documents`/needle rule; `reach` calls it | `cli.py` (`_echo_json` only), `query_plan/reach.py`, new package | capture + facts (§10) | full suite green; `plan replay --reach` on banking unchanged (76 / 101 decision, 61 lexical) |
| S2 | **Executor core over a plan payload.** Node kinds, guards (escalate, run, page; replace from `then.program`), limits, deadline, errors, short-circuit; planner renders `then.program` / `then.cmd`; `query run --plan FILE\|-` in-process, output = `path` + step facts (no bundle yet) | `query_run/executor.py`, `query_plan/planner.py` (`_guard_json`), `cli.py` (command), `_entry._proxiable` | executor (§10) | hermetic; reach parity with the short-circuit |
| S3 | **Bundle.** Adapters, RRF ranking, assembly to budget, full bundle under a handle, `query page --list`, `LIST_KEY["query run"]` | `query_run/adapters.py`, `query_run/bundle.py`, `cli.py` (`query page`) | bundle (§10); routes test: adapter per command | hermetic |
| S4 | **`query run "<question>"`, proxying, spans, replay `--run`.** Plan then execute; proxied; `trace_spans.parent_span_id` and run columns; `TracedGroup` child spans; `traces show` nesting; analysis signals; `plan replay --run` | `cli.py`, `traces/span.py`, `traces/store.py`, `db.py`, `traces/analysis.py`, `query_plan/replay.py` | spans + analysis (§10) | **P3a latency and bundle-reach rows of §9.1** on banking and brain01 |
| S5 | **Step bindings.** `program.py` grammar and load rules, `BIND_OUTPUTS` in `facts.py`, `routes.render` leaves bound placeholders, `_node_json` `binds`/`over_bound`, executor binding | `query_plan/program.py`, `routes.py`, `planner.py`, `query_run/` | bindings (§10) on fixture routes | hermetic; route parity on all three vaults unchanged (no route uses bindings) |
| S6 | **Shadow dry execution.** `ARTMIND_PLAN_SHADOW_RUN`; `run_shadow` event; `covers_agent`; `traces stats` block | `query_plan/shadow.py`, `traces/analysis.py`, traces CLI | shadow (fake executor) | **P3a live row**: one benchmark run per vault with the flag on |
| S7 | **`--each`.** Batch program per item, in-process, JSONL under `logs/runs/`, child span per item | `query_run/each.py`, `cli.py`, `_entry` | batch (fixture claims file, invented) | hermetic; one live run on banking claims (public) within `--maxItems` |
| — | **P3a done** | | | §9.1 all rows |
| S8 | **`previous` end to end.** Resolution order; `QueryRequest.previous`; `_entry` forwarding; `previous_from_trace` reads run spans; UIs write the per-session file | `_entry.py`, `server.py`, `query_plan/previous.py`, `webui/` (sessions, backends) | §10 contract plumbing | replay follow-up labels: `previous_used` true where a label `follows:` another; brain01 follow-ups ≥ 3 of 4 in the next benchmark |
| S9 | **The bundle skill and the contract switch.** `artmind/skills/artmind-query-bundle/` (~150 lines), generated `references/routes.md` + `commands.md` and their test, `AgentProfile.contract`, `ARTMIND_AGENT_CONTRACT`, `benchmark_runs.contract` + form field, `trace_turns.contract`; `artmind init` inside each vault | `skills/`, `webui/profiles.py`, `benchmark.py`, `webui/benchmark_routes.py`, `db.py`, `justfile` | skill + contract plumbing (§10) | hermetic; a `contract: bundle` benchmark run completes on banking |
| S10 | **Paired comparison.** `traces stats --compareRuns A B` | `traces/analysis.py`, traces CLI | pairing by `benchmark_seq`, invented runs | **P3b gate** (§9.2) read by the user on banking and brain01 |
| S11 | **Subplan node.** Grammar, `clauses` list slot (given plans only), executor, bundle `clause` and `subplans` | `program.py`, `planner.py` (slot plumbing), `query_run/` | subplan (§10) | hermetic; `exec_ms` and `plan_ms` for a 3-clause fixture plan on banking reported for B11 |
| S12 | **Fast path switch.** `ARTMIND_RUN_FAST_ROUTES`, `mode`, `overridden` signal; per-route readings in `traces stats` | `query_run/bundle.py`, `traces/analysis.py` | mode rule (§8.3) | merged off; a route is enabled only by §9.3's reading |
| S13 | **Retire the losing contract.** Due at the first paired benchmark that passes §9.2 in both vaults, or when the user decides to drop the bundle contract (§13 Q8). Until then every vault stays `classic`. If bundle wins: its content moves into `artmind-query`, `artmind-query-bundle` and the classic text are deleted, profile `contract` and `ARTMIND_AGENT_CONTRACT` are removed, and `artmind init` drops symlinks to removed artmind skills. If classic wins: the bundle skill and the switch are deleted; the executor stays (used by `--each`, shadow runs, PA3, B11). The `trace_turns.contract` column stays for history | `skills/`, `webui/profiles.py`, `setup.py` (`_symlink_skills` pruning), `benchmark.py` | contract tests updated | full suite; one benchmark run per vault on the single contract |

S1–S4 are strictly sequential. S5, S6 and S7 each need only S4 and touch different files, so they can run
in parallel. S8 needs S4. S9 needs S3 and S8. S10 needs S9. S11 needs S5. S12 needs S9.

## 12. Changes to existing pieces

| Piece | Change |
|---|---|
| `cli._echo_json` | capture branch before budgeting (S1) |
| `query page` | `--list NAME` (S3) |
| `LIST_KEY` | `query run: evidence` |
| `_entry` | `_proxiable` for `query run --previous/--plan/--each`; forwards `ARTMIND_PREVIOUS_CONTEXT` contents |
| `server.QueryRequest` | `previous` field, applied as a contextvar |
| `query_plan/program.py` | binding placeholders and their load rules; `subplan` node; `clauses` list slot; allowed-command check |
| `query_plan/planner.py` | pre-rendered `then.program` / `then.cmd` on guards; `binds`, `over_bound` in node JSON |
| `query_plan/reach.py` | uses `query_run/facts.py` |
| `query_plan/shadow.py` | dry execution behind its flag |
| `trace_spans` | `parent_span_id`, `run_mode`, `escalate_reason`, `bundle_chars` |
| `trace_turns`, `benchmark_runs` | `contract` |
| `webui/profiles.py` | `contract` field; skills and append built from it |
| `artmind/skills/` | new `artmind-query-bundle`; generated references |
| `docs/question-routing.md`, `CLAUDE.md` repo-layout table | `query run`, `artmind/query_run/`, the contract switch (with S4 and S9) |

## 13. Decided questions

Each was put to the user with its background and options on 2026-11-07. The answer is recorded here and
applied in the sections named.

| # | Question | Decision |
|---|---|---|
| Q1 | Structured programs and `db` steps | **Allow the read-only `db` set; convert later.** The executor may run the read-only `db` set (§3.3). Converting `structured` to a program, and choosing `db schema` or `db bridge` as its opening step (round 9: the agent opens with `db schema`), is its own route change after S4, decided by replay. |
| Q2 | Per-step timeout | **Not now; measure first.** Only the between-step deadline (§3.6). If `run_shadow` (S6) shows steps over 10 s, add a Neo4j transaction timeout through a contextvar, as views do with `VIEW_QUERY_TIMEOUT_S`. |
| Q3 | Parallel `par` | **Sequential.** S4 and S6 measure `exec_ms` per program kind. Parallel children (per-thread capture, one Neo4j session per thread) come as their own slice only if `par` programs miss the 3 s gate. |
| Q4 | Agent-given clauses (`--clause`) | **No.** The agent may call `query run` once per part itself. B11 weighs code splitting against agent splitting, with the subplan node (S11) already built. |
| Q5 | Dry runs of `curate` / `update` actions | **Deferred to P5.** The bundle carries `action` and escalates `no_lookup` (§3.7). The skill runs the dry run with the user, as today. The executor stays strictly read-only. |
| Q6 | Structured offers | **Text extraction; revisit after S8.** Offers stay extracted from the stored answer (`previous.extract_offer`). A structured offer is added only if S8's follow-up measure stays low because of missed offers. |
| Q7 | Evidence cap | **Keep 10 passages + 1 per further document** (§6.3), as a named setting. S6's `covers_agent` and the `query page` calls per bundle turn decide whether it moves. |
| Q8 | Mixed outcome in the paired benchmark | **Classic until both vaults pass.** A mixed result counts as a fail: every vault stays `classic` until one paired run passes §9.2 in both banking_corpus and brain01. There is no time limit and no per-vault default. S13 runs at that double pass, or when the user decides to drop the bundle contract (§11). |
| Q9 | Does `query plan` stay agent-facing? | **No; operators and tooling only.** The bundle skill never calls it (§8.2). It stays for operators, replay, shadow and the classic contract. `traces stats` counts a `query plan` span on a bundle-contract turn as `skill_violation`. |

## 14. Risks

- **The bundle becomes the agent's ceiling.** An agent that trusts a low bundle may stop short. The
  mitigations: `mode: reasoning` on every bundle until a route is measured; the skill says the bundle is
  the starting evidence; `added_after_run` shows whether agents still dig; and the paired gate compares
  answers, not just call counts.
- **The serve lock under long runs.** A 20 s run blocks other proxied queries to that vault. The
  mitigations: the deadline, `--each` in-process, and S6's latency numbers. If it bites, §13 Q3.
- **Adapters drift from payloads.** A command's JSON changes and its adapter silently drops rows. The
  mitigations: adapter tests on recorded payloads, regenerated with the plugin fixtures, and the
  bundle-reach gate.
- **Bindings grow into a language.** The vocabulary is closed and each output needs code and a test.
  There are no paths and no expressions.
- **Privacy.** The bundle and `run_shadow` hold corpus-derived data: bundles go to stdout,
  `logs/results/` and `logs/runs/` (private, pruned), while events hold ids and counts only. Test fixtures
  are invented, and banking payloads only where its text is needed (public).
