# 08 — Fix and redesign plan

Written 2026-10-22 from rounds 1–5, after re-checking the dossier's code claims against
`master` (`200448f`) and auditing every recorded turn (round 5). Revised the same day:
**`query plan` is built first**, and the rest of the plan is arranged around it. This is the
"move to specs" input; each workstream becomes one spec. Same privacy rule as the rest of
the folder.

## 1. Verdict on the dossier

**Coverage (round 5):** every Q&A turn recorded in `brain01` and `banking_corpus` has now been
analysed; the unanalysed batches were operator turns (P7) plus the four 10-17 benchmark runs
(P26). `my_work` has no traces. Unseen shapes: hierarchy, path, filing, unconfirmed-mapping SQL.

**What holds (verified in code):**

- Supersession tie-break: `conflicts.py:267`, `(bf or "") >= (af or "")`, scope hardcoded to
  `document`; two empty `valid_from` values always pick side B.
- `pattern10 --asOf` is a presence flag that adds `:DocumentHistory`/`:DocChunkHistory`
  (`graph_query.py:986-1030`); the same flag means a floor filter on `vector-text`/`chunks`.
- `pattern7` caps at `--limit` 10 with no `rows_total`/`truncated` (`graph_query.py:906-924`);
  `pattern10` already has both (`:1091-1093`).
- `domains-overview` has no view count; the skill is 463 lines / 5,031 words (8,556 with
  references); `BENCHMARK_SYSTEM_APPEND` and `traces/path.py` restate the protocol.

**Corrections to the dossier:**

1. **Q10 has two causes, not one.** `document-history` matches the name exactly
   (`graph_query.py:1983`), so the missing `.md` matters there. `pattern10` matches by substring,
   so its zero rows came from calling it without `--asOf` on a retired document.
2. **The history design covers supersession between files only.** Chunk text is not versioned
   across re-ingests of the same file (`document-history` help), so "which version applied"
   for a re-ingested file is not answerable from the graph. Say so in the spec.
3. **A date-honest `--asOf` is blocked by data:** `valid_from` is empty on the banking
   supersession pairs (open question 20). Interim: an explicit `--includeHistory`.
4. **Measurement is thin:** one model (Haiku 4.5), one run per question, one grading pass,
   open question 17 unresolved. Gate on route assertions, not on the 9/24/3 split.
5. **Two surfaces, two failure families.** Benchmark turns fail on completeness, time and
   conflicts; chat turns fail on entry (skill skipped) and resolution (wrong narrator, twins).

## 2. Why `query plan` first

The first draft of this plan put CLI hints first and held `query plan` back. Round 5 changes
that, and so does the dependency structure:

- **The decisive failure is matching, not knowing.** P26: in two runs the view catalogue was
  already in the agent's context, on a profile that says "follow the protocol exactly", and the
  agent did not match the question to the view; the hand-built routes undercounted. P1 shows
  the same in chat (2 of 5). An entry call that only *lists* views (the first draft's W3.1)
  would not have fixed either; deterministic view matching and param filling would.
- **The same question takes different routes** (P1: five routes; P26: four routes, three
  answers). Only a deterministic step makes the route reproducible, and therefore testable.
- **It sets the shape of everything else.** The skill shrinks to "run the plan, follow its
  steps, use the table when confidence is low"; the routing table becomes plan data; traces
  measure "plan followed / overridden" instead of views-first; `next` hints are only needed
  for what the plan cannot decide. Doing the skill diet or the hints first would mean doing
  them twice.

What stays independent of the plan and should not wait for it:

- **Data correctness (W1)** is ingest-side; no route can recover from a supersession recorded the
  wrong way (Q21, Q25). Run it in parallel, in its own session.
- **Honest commands** (part of W2): the plan can only emit commands that report truthfully.
  `--dry-run` that looks like zero rows, a `pattern7` top-10 with no `truncated`, and
  `pattern10` history behind an undocumented flag would make the plan's steps fail in the
  same ways the agent's do. These are built with the plan, not after.

## 3. Workstreams

### W0 — Labelled set (small; the plan's test fixtures and the gate)

- One labelled set: question shape → domains, intent, view (or none) with params, ideal steps,
  expected documents. Built from `04-scenarios.md`, the 36 benchmark questions and the
  recorded brain01 turns. **Real questions stay in the vault** (e.g.
  `<vault>/.artmind/reviews/` or `benchmarking/`); this folder holds only shapes.
- The same set drives three checks: the plan's unit/replay tests (W2), route assertions on
  agent traces (did the turn run the plan's steps?), and the final benchmark.
- Spot-check 3 round-3 grades by hand (open question 17) before using them as a baseline.

### W1 — Data correctness (parallel; ingest side)

1. Diagnose why `valid_from` is empty (open question 20): labels absent, a missing
   `temporal.document_patterns` rule, or `ingest retime` never run. Read-only first.
2. Adjudicator: no supersession when both dates are empty; queue for review. Filename-version
   and "Supersedes Version N" as reviewed tie-breakers. Test the arguments
   `apply_supersession` receives, and that it is not called on a tie.
3. Repair the banking vault's four records and check the two unverified ones; re-run Q21/Q25.
4. Out of scope: sub-document supersession (open question 18).

### W2 — `artmind query plan "<question>" [--domain …] [--execute]`

**Contract.** Read-only, deterministic, no LLM in v1. Output:
`{domains, intent, confidence, entities, view_match, steps:[{cmd, why}], read:[reference],
fallbacks:[…], warnings}`. Recorded as a trace span. Proxied by `serve` like other `query`
commands (so the stale-daemon rule applies to every test).

**Routes as data.** `artmind/query_routes.yaml`: intent → triggers → preconditions → steps →
fallbacks → reference. Read by the plan, by `traces/path.py`, and rendered into (or tested
against) the skill's routing table. This is the single routing place the dossier asks for (B).

**Stages**, each a pure function with its own tests, reusing existing modules:

| Stage | Does | Reuses |
|---|---|---|
| 1. Domains | `--domain` as given (guard warnings carried through), else matched against overview document/class names; a parent name scopes all siblings (P8, P11, P14) | `domain_guard`, `graph_query.domains_overview` |
| 2. Views | Skipped when the domain has none (P10). Otherwise score the question against each view's `summary` + `examples`; fill params: entity params by resolving noun phrases filtered on the param's `entity_class`, int/date params by pattern ("in 2025"). Strong match → the full `views run … --param p=@<id>` line, or the result with `--execute` (P1, P26) | `kg_views.store.list_views`, `kg_views.params.resolve_params` |
| 3. Entities | Resolve mentions; "I/my" → the domain's narrator entity (P19); twins of the right class all listed, and a view step emitted per twin, never a degree tie-break | `vector_query.entity_resolve` |
| 4. Intent | Lexical rules from the routes file: aggregate, time/history, conflicts, hierarchy, path, document, insights, superlative (inferred, say so), enumeration (raise `--limit` until `truncated` is false) | `query_routes.yaml` |
| 5. Store | Resolved classes ∩ `db bridge` classes; question terms ∩ bridge values → graph / analytical / hybrid / records-plus-guidance (S7–S9) | `db` bridge readers |
| 6. Assemble | Steps, fallbacks, references, confidence; low confidence → facts only plus "use the routing table" | — |

**View scoring in v1** is lexical (token overlap with summary/examples, plus params that
resolved), not embeddings: there are two views in one vault, and open question 4 needs the
labelled set before an embedding model earns its place. The interface allows a scorer swap.

**Honest commands, built with the plan** (the plan emits them, so they must not mislead):

| Change | Patterns |
|---|---|
| `--dry-run` payload has no `rows`, carries `next`; traces stop counting it as an unproductive graph attempt; cache the `temperature` rejection per model | P20 |
| `pattern7`: `rows_total`, `truncated`, `next`; bounded source `documents` per entity row | P21, P24 |
| `pattern10 --includeHistory` (explicit; `--asOf` kept as alias, documented); `supersedes` beside `superseded_by` on document rows; retired-only match returns `next`; `document-history` zero rows → closest names, suffix tolerated when unambiguous | P12 |

**Validation before any agent uses it:**

1. Unit tests per stage; assert the queries and parameters sent, never counts alone.
2. **Offline replay** (open question 28): run `query plan` on every labelled question against
   the live graph with `ARTMIND_NO_PROXY=1`; compare its steps with the ideal route and with what
   the recorded turn did. Report per intent: right / wrong / low-confidence. This answers open
   questions 3–5 (lexical rules, view matching, param filling) with no agent cost.
3. **Shadow mode** in the chat and admin UIs: the backend computes the plan for each user
   question and records it as a span, without showing it to the agent. A week of shadow
   traces shows how often the agent's route agreed, and which answers were right when it did not.

Gate: replay is right on every view-answerable question in the set (S1, S2, P26), and
low-confidence rather than wrong on the shapes it cannot classify.

### W3 — Wire the plan in; shrink the skill around it

1. Replace prose-pinning tests (`test_query_skill_views_step.py`) with data checks: every
   command and option the routing table names exists in the Click tree; the table matches
   `query_routes.yaml`.
2. Entry call becomes `artmind query plan` (it already carries what `domains-overview` +
   `views list` gave). Follow-up turns call it again per new question; that answers P4
   without a reload of the skill.
3. `SKILL.md` to about 120–150 lines: grounding contract, the plan call and how to read it,
   the rendered routing table for low confidence, answer style and Sources. Everything else in
   references read on demand (`views.md`, `entity-and-hubs.md`, `insights.md`, generated
   `commands.md`, and the existing four). Drop the routing sub-agent (never used) and the
   Fallback ladder (now per-route `fallbacks`).
4. `traces/path.py`: "plan followed / overridden" from the plan span, replacing
   `_BEFORE_VIEWS_OK`; views-first stats only where views exist (open question 16).
   `BENCHMARK_SYSTEM_APPEND` points at the skill instead of restating steps.
5. Chat entry framing (P18): `ADMIN_SYSTEM_APPEND` and the skill `description` say that
   questions about the user's own life, work and notes are answered from artmind, never from
   memory; check in `artmind/webui/agent.py` whether the agent memory directory is the cause
   and remove it for chat profiles if so. Test on 10–20 first-person first turns.

### W4 — Remaining self-correcting hints (only what the plan does not cover)

Output-size guard on `graph metadata` / large `pattern1` (P23); invalid-form hints
(`query db sql`, `query graph entity-resolve`; P15); `vector-text` zero-hit domains (P14);
wikilink hint (P14; keep only if measured to help); `next` on an empty view run naming
twins (P3, P19). Each is one small PR, cut down to what shadow and replay show the plan
still misses.

### W5 — Deferred until measured

- An LLM intent classifier, only if replay shows lexical rules misclassify a large share.
- Embedding view matching, once there are enough views and the labelled set to tune it.
- Command consolidation (patterns 2/3/4, 6→5, resolvers): the plan already hides the
  choice; consolidate only pairs it gets wrong.
- Composite commands and views proposed from traces; twin-node curation (open question 23);
  a ready `sources` block for synthesis embellishment (P16).

## 4. Phasing and gates

| Phase | Contents | Gate |
|---|---|---|
| 0 | W0 labelled set; spot-check grades. W1 starts in parallel | Set covers S1–S25 that have evidence |
| 1 | W2 plan v1 + routes file + honest commands | Unit tests; offline replay gate above |
| 2 | W2 shadow mode (one week of real use) | Plan agrees with correct agent routes; disagreements explained |
| 3 | W3 wire-in and skill diet | Targeted agent runs below no worse than baseline, fewer calls |
| 4 | W4 hints, cut to the gaps phases 2–3 show | Per-hint probe |
| 5 | Full benchmark once; W5 decisions | Compared with `07-benchmark-round3.md` |

**Targeted agent runs** (the full benchmark costs about 94 min and $3.67): the P26 question
four times (the variance test: four identical routes is the pass); Q07, Q09–Q11 (time);
Q13 (conflicts, after open question 27); Q04, Q05, Q18 (parent domain); the R4 enumeration
and superlative shapes; the chat first-person set. One run on a larger model before calling
the skill diet model-independent.

**Targets:** the P26 question gives one route and the view's answer every time; `views list`
calls in a view-less vault 0; expected documents cited up from 49%; first-person first turns
load the skill at least 9 of 10; skill under 150 lines with no loss on route assertions.

## 5. Risks

- **The plan becomes the new long skill.** Mitigation: routes live in one data file with tests;
  every intent row cites the scenario it came from; no row without evidence.
- **Wrong plans followed confidently.** Mitigation: `confidence`, low-confidence fallback to
  the table, shadow mode before the agent sees plans, and "plan overridden and answer right"
  traces fed to `artmind-review` as route fixes.
- **Lexical rules overfit two vaults.** Mitigation: replay on `my_work` once it has traces
  and on any new vault before wiring in; the LLM classifier stays an option (W5).
- **Corrupted corpus hides plan quality.** W1 runs in parallel; banking time and conflict
  questions are judged only after W1.3.
- **Stale daemon.** `query plan` is proxied: every probe uses `ARTMIND_NO_PROXY=1` or a restart;
  restart `chat-ui`/`admin-ui` before shadow mode.
- **Privacy.** The labelled set and replay output hold real questions: vault only.

## 6. Traceability

| Pattern | Fix | Where |
|---|---|---|
| P1, P4, P26 (view not matched, routes vary) | View match + param fill; plan per turn | W2 stage 2, W3.2 |
| P2, P3 (long tails, empty view) | Plan steps + fallbacks; twins; empty-view `next` | W2 stage 3, W4 |
| P5 (history to text2cypher) | History intent row | W2 stage 4 |
| P8, P9, P11, P14 (domains) | Domain stage, parent scope; guard kept | W2 stage 1 |
| P10 (views list without views) | View stage skipped when none | W2 stage 2 |
| P12 (time) | `--includeHistory`, `supersedes`, name hints, time row | W2 honest commands; W1.1 for dates |
| P13 (conflicts unused) | Conflicts row; open question 27 first | W2 stage 4 |
| P15 (SQL command forms) | Store stage emits exact commands; invalid-form hints | W2 stage 5, W4 |
| P16 (embellishment) | Not routing | W5 |
| P18 (first turn skips skill) | Framing, description, memory dir | W3.5 |
| P19 (narrator, twins) | Entity stage | W2 stage 3 |
| P20 (dry-run as zero rows) | Honest payload | W2 honest commands |
| P21, P24 (top-10, no provenance) | `truncated`, `documents` | W2 honest commands |
| P22 (superlative) | Superlative row, inferred | W2 stage 4 |
| P23 (huge outputs) | Size guard | W4 |
| Data: supersession direction | Tie-break, repair | W1 |

## 7. Decisions to make before specs

1. **`query plan --execute` in v1, or plan only?** Recommend plan only for v1 plus `--execute`
   for a strong view match (the P26 case), since that path is already one deterministic call.
2. **Where the labelled set lives.** Recommend the vault's `benchmarking/` folder beside the
   benchmark questions, so `artmind/benchmark.py` can read both.
3. **Shadow mode for how long, on which surfaces.** Recommend the admin console for one week,
   then the chat UI.
4. **`--includeHistory` now, a date-honest `--asOf` after W1.1.** Recommend yes.
5. **Unknown domain stays a warning** (decided 2026-10-20).
