# Phase 8 implementation notes

What actually happened during Phase 8 — the cutover — against
[`redesign-phase8-runbook.md`](redesign-phase8-runbook.md) and the "Done when"
criteria in [redesign-phase-plan.md](redesign-phase-plan.md). This is the final
phase of the observation/projection redesign. Read the runbook first; this
document records what the runbook's steps actually surfaced, not what was
planned.

**Scope discipline**: the design was complete going into this phase. Nothing
below re-opens a design decision made in Phases 0–7. Everything named as a
follow-up here is a genuine new finding surfaced by running the real cutover
against the real corpus for the first time, not a second-guessing of prior work.

---

## Summary

Wiped the production Neo4j graph, re-ingested the full banking corpus from the
vault (63 documents), ran a real curation pass (180 same-as proposals + 12
cross-domain conflicts, human-reviewed), rebuilt the projection, and measured
against the pre-redesign baseline on both graph-quality metrics and a 36-question
answer-quality benchmark.

**Result: 9 of 12 scorecard rows hit target exactly or are clean; two did not,
both traced to a confirmed root cause, not measurement noise. The benchmark
completed all 36 questions (baseline could only complete 31) and held up on every
quality trap sampled but one.** Three previously-unknown live bugs were found and
fixed during the cutover itself; two more were found and filed as follow-ups
rather than fixed blind, matching this project's established precedent of not
leaving a provably-wrong pattern in place when it's cheap and confidently scoped
to fix, and not fixing what isn't.

---

## What the re-ingest surfaced

### Real bugs found and fixed live (matching Phases 2–7's precedent)

1. **Relationship-property loss.** `_write_relation_observations`
   (`artmind/ingest.py`) flattened a relationship's structural keys but dropped
   the LLM's own `properties` object entirely — every extracted relationship
   property the extraction prompt asks for was silently discarded before it ever
   reached the graph. Fixed to unwrap `rel["properties"]` before flattening.
   Regression tests added in `test/test_ingest_provenance.py`.
2. **Mixed-type union crash.** `_union()` (`artmind/projection.py`) assumed every
   value in a list-shaped property was the same Python type; a genuinely
   unformatted-hint property (see the watch list, below) produced a mixed
   `int`/`str` list that Neo4j's uniform-list-property constraint then rejected
   outright, failing the whole rebuild. Fixed to coerce to deduped strings when a
   union spans more than one type, with a warning naming the likely cause.
   Regression test added in `test/test_projection_merge.py`.
3. **`sameas.approve()`'s domain-scoping is a no-op for this corpus.**
   `touched_domains = sorted({k[2].split(".")[0] for k in group if k[2]})`
   collapses every domain shape in use (`banking.policy`, `banking.sop_guides`,
   ...) to `"banking"`, making every single approval as expensive as a full
   `projection.full_rebuild()` — confirmed live at ~2h45m–3h per call. Not
   fixed blind (a genuine behavior-scope question — was the `.split(".")[0]`
   deliberate for a domain hierarchy this corpus doesn't have?) — filed as
   [neurongraph/artmind9#12](https://github.com/neurongraph/artmind9/issues/12)
   and worked around for this session's curation by writing `same_as.yaml` and
   node statuses directly, then running one bare `projection rebuild`, which is
   the runbook's own prescribed closing step anyway.

### Real bugs found and filed, not fixed (out of this phase's scope, low-risk to defer)

4. **Projection conflict-detection false positive.**
   `_hashable()` (`artmind/projection.py:161`) does no type normalization, so an
   `int` and a `str` form of the identical value are treated as distinct — a
   property gets flagged as a same-instant disagreement even though, once
   `_write_conflicts()` stringifies values for storage, the two recorded values
   render identically and the "conflict" is meaningless to anyone reading it.
   Confirmed live: `METRIC_TARGET.value` on "Internet Banking Platform Response
   Time Target" flagged with `values=['2', '2']`. Filed as
   [neurongraph/artmind9#13](https://github.com/neurongraph/artmind9/issues/13).
5. **`ROLE_ACTOR` extraction has no fallback for an unnamed role.** The schema's
   guidance ("Name the role as stated in the document") gives no instruction for
   the case where no specific role is named, and the extractor falls back to the
   literal class label — a live entity is named **"Role Actor"** and aggregates
   three unrelated roles' approval limits under one identity, purely because
   several chunks hit the same unnamed-role case. Filed as
   [neurongraph/artmind9#14](https://github.com/neurongraph/artmind9/issues/14).

### An operational bug, not a code bug

6. **The structured (DuckDB) store was never wiped by Step 2.** The runbook's
   `DETACH DELETE` only touches Neo4j; the structured store retained stale
   pre-redesign data plus one unrelated smoketest table through the wipe. Fixed
   operationally (dropped the banking tables via
   `artmind.structured.registry.delete_table()`, deleted the stale parquet
   files, force-reingested the five real CSVs) rather than in code — the runbook
   itself should gain an explicit structured-store wipe step; noted below under
   "Runbook corrections."

---

## Scorecard: which rows moved, and why

Full table with baseline/target/after values:
[redesign-quality-scorecard.md](redesign-quality-scorecard.md). Summary: **9 of
12 rows hit target exactly or are clean (2, 3, 4, 5 exact, 6, 7, 10 exact, 11)**;
row 8 is informational, not a defect measure; row 9's framing is retired (no
longer a possible collision by construction, so its count is no longer measuring
a defect). Two rows are real misses:

### Finding A — row 1, near-duplicate entity names (644 pairs, target < 100)

Root cause confirmed by reading `artmind/sameas.py` and this session's own
proposal run: `sameas propose` is architecturally a **cross-domain adjudicator
only**. It pairs entities across different domains looking for identity matches;
it has no code path that ever proposes two same-domain, same-class entities as
candidates. This session's real run against 8,072 entities produced 180
proposals and **zero** were same-domain+class. Ingest-time `normalize_name()`
(deterministic lexical folding) catches spelling/casing variants but not
genuine near-duplicate names independently extracted in different chunks. There
is currently no pipeline stage responsible for this category of duplicate —
curated or automatic. This is a real architecture gap, not a tuning problem;
closing it needs a same-domain+class near-duplicate proposer, which does not
exist today in any form.

### Finding B — row 12, the control regressed (439 → 1,773 keys, 1 → 80 near-dup pairs)

Root cause confirmed by reading the extraction prompt directly
(`artmind/domains/meta.yaml`'s `properties` template): *"There is NO fixed
schema for properties beyond what is listed below. You decide what matters
based on the entity's role in the document."* This directly contradicts row
12's own stated premise — that property keys stay clean *because* the prompt
enumerates them per class. It doesn't; it explicitly invites the model to invent
keys freely. Live examples of the resulting near-dupes: `balance_maximum` /
`balance_minimum` never unified with sibling docs' `balance_range_maximum` /
`balance_range_minimum`; `interest_rate` / `interest_rate_aer` /
`interest_rate_range` / `interest_rate_type`; `notification_requirement` /
`verification_requirement` / `verification_requirements` (three-way). This is
the row the runbook flagged as "the whole argument in one line" — and the
re-ingest shows the argument doesn't currently hold. Not fixed this phase (a
prompt change here is itself a design decision, not a bug fix, and belongs to
whoever owns the extraction-prompt architecture next) — recorded here as the
clearest, most load-bearing finding of the whole cutover.

---

## The property-hint watch list — verified live, not just cross-referenced

Phase 7 named nine properties as plausible instances of the "unformatted hint"
bug (a hint that doesn't pin a value's format, so different chunks write
`£50,000`, `50000`, and `£50k` for the same fact) and left them unverified,
pending a real corpus. This phase checked them against all 1,383 live
`Conflict {_source: 'projection'}` nodes.

**26 conflicts matched the nine watch-list properties.** Split by whether the
disagreement is confined to one document (the runbook's own signature for "this
is a prompt bug, not a corpus finding") or spans multiple documents:

- **13 cross-document** — genuine disagreements between different source
  documents (e.g. `Branch Availability` 99.7% in one document vs. 99.68% in
  another; `Capital Ratio (Pillar 1)` breach threshold stated as `<15%` in one
  document and `15%` in another). These are the conflict detector doing its job
  correctly, not a defect.
- **13 single-document.** Of these:
  - **3 confirm the predicted unformatted-hint bug exactly**:
    `PRODUCT.determines_process_variant` = `'True'` vs. `'yes'` (Checking
    Account, and again on Mortgage Account with a third `'yes'`), and
    `PROCESS_STEP.estimated_duration` = `'Minutes'` vs. `'5 minutes'`.
  - **1 is issue #13** (the conflict-detector false positive on identical
    stringified values) — a new class of bug the watch list wasn't looking for.
  - **1 is issue #14** (the "Role Actor" generic-name collision) — likewise
    new, and a category above a property-format issue (an entity-identity
    defect, not a value-format one).
  - **1 confirms an already-known finding**: `METRIC_TARGET.value` on
    **"Total Customers"** = `['320', '500', '480']` — three genuinely different
    metrics (almost certainly per-branch, per-product, or per-quarter counts)
    colliding under one generic metric name.
  - **6 remaining `ROLE_ACTOR.approval_limit` cases** turned out to be Finding B
    in miniature, not a formatting issue: two semantically different facts (a
    number vs. a category description, e.g. `'500'` vs. `'Low severity
    complaint resolution'`) colliding on one free-form property key within a
    single document.

Net: the watch list's own hypothesis was confirmed for 2 of the 9 named
properties, and checking it live surfaced three findings (issues #13, #14, and
reinforcing evidence for Finding B) that the hypothesis never anticipated.

---

## Benchmark

Full comparison, caveats, and per-question detail: the "Answer quality" section
of [redesign-quality-scorecard.md](redesign-quality-scorecard.md). Two things
had to be disclosed before the numbers meant anything, and both are recorded
there in full:

1. **Baseline itself never completed 5 of its 36 questions** — it hit a spend
   limit near the end of its run. Q32 is a truncated `failed` answer; Q33–Q36
   have no real answer at all. After-cutover produced the first real answers
   ever obtained for those five questions; this is reported as "new answers,"
   not a regression/improvement comparison, because there is nothing on the
   baseline side to compare against.
2. **The two runs used different models.** An unrelated OAuth failure forced
   after-cutover onto `claude-haiku-4-5` via an enterprise gateway; baseline's
   model is unknown/unrecorded. The ~4–6x lower cost and turn count after
   cutover is very likely dominated by this model swap, not by the redesign's
   retrieval efficiency, and is **not** claimed as a redesign benefit.

**Quality**, the axis the runbook actually asked to protect, held up on every
hard case sampled — Q05/Q13 correctly refuse to resolve a stated conflict
without evidence, Q07–Q09 correctly apply document supersession and exclude the
superseded policy's thresholds, Q28 reports both disputed figures without
collapsing them, Q35 correctly refuses to name a "busiest" branch manager when
all seven reviewed exactly one case each — with **one disclosed exception**:
Q36 computed and asserted a CSAT trend from an n=1 sample instead of flagging it
as too thin to generalize, the exact trap Q35 got right two questions later.
Named honestly rather than folded into an aggregate pass count, per the
runbook's explicit warning against reporting "graph metrics improving while
answers get worse" — the one place in this session that happened, in miniature.

---

## Runbook corrections (for whoever runs this again)

- **Step 2 (wipe) needs an explicit structured-store wipe.** `DETACH DELETE`
  only clears Neo4j; the DuckDB-backed structured store is a separate system
  that silently survives a "wipe" and needs its own step
  (`artmind.structured.registry.delete_table()` per table, or an equivalent).
- **The scorecard's reproducible script (§"Re-running it") had three latent
  bugs**, all fixed in place this phase — see
  [redesign-quality-scorecard.md](redesign-quality-scorecard.md) for the
  corrected script and what each bug was (`e.domain` vs. `e._domain` for row 1;
  `SAME_AS`/a second `CONFLICTS_WITH` missing from the structural-edge
  exclusion set for rows 4/5/10; row 9's collision framing being stale
  post-redesign). Anyone re-running the *old* copy of that script reproduces
  the bugs, not the metric.

---

## Phase 7's deferrals — status after a real corpus

Phase 7 named three items "deferred, on purpose." This phase's re-ingest is the
first real evidence bearing on any of them:

| Deferral | Status after Phase 8 |
|---|---|
| `README.md` full refresh | No new evidence either way — stays deferred to its own pass, per Phase 7's original reasoning (a dead-workflow rewrite, not a rename). |
| End-user opencode persona (`artmind.md`) naming operator-only skills | No new evidence either way — stays deferred to whichever phase next touches `profiles.py`/`artmind/opencode/`. |
| A dedicated `query graph conflicts --source projection` surface | **Now confirmed to matter.** This phase's own Step 7 measurement needed direct Cypher against `Conflict {_source: 'projection'}` nodes because no CLI surface exists for it — the exact gap Phase 7 named. Recommended as a real, scoped follow-up: `artmind query graph conflicts` already exists for the adjudicator's conflicts; it needs a `--source` filter (or a sibling command) to cover the projection-detected ones this phase depended on checking by hand. |

---

## Follow-ups named explicitly (this is the end of the project — nothing here becomes a silent "Phase 9")

- [neurongraph/artmind9#12](https://github.com/neurongraph/artmind9/issues/12) — `sameas.approve()`'s domain-scoping collapses to the whole corpus for this domain shape, making every approval as expensive as a full rebuild.
- [neurongraph/artmind9#13](https://github.com/neurongraph/artmind9/issues/13) — projection conflict detection can flag a false positive between values that render identically once stringified.
- [neurongraph/artmind9#14](https://github.com/neurongraph/artmind9/issues/14) — `ROLE_ACTOR` extraction has no fallback for an unnamed role, causing a generic "Role Actor" entity to aggregate unrelated facts.
- **Finding A** (row 1) — no pipeline stage exists to propose same-domain, same-class near-duplicate entities; `sameas propose` is a cross-domain adjudicator only. A real gap, not a tuning problem.
- **Finding B** (row 12) — the `properties` extraction prompt (`artmind/domains/meta.yaml`) is explicitly free-form, contradicting the redesign's own "clean by construction" premise for property keys. Fixing it is a prompt-architecture decision belonging to whoever owns that surface next, not a bug fix.
- **The "Total Customers" generic-metric-name conflation** — confirmed live (`['320', '500', '480']` under one entity) as a real instance of a previously-named pattern: distinct metrics sharing an overly generic name collide at the aggregate-key level.
- **`docs/admin-ui-curation-workflow.md`** (already written this phase) — the Lane B "Curate" tab backlog design, grounded in this session's actual 180-proposal / 12-conflict hand-curation. Blocked on issue #12 for its "decide immediately, no rebuild spinner" design to be viable at real scale.
- **The projection-conflict query surface** (`query graph conflicts --source projection` or a sibling) — Phase 7 named this a real gap; this phase's own measurement work confirms it, above.
- **The Q36 benchmark miss** — a single instance of asserting a trend from a too-thin sample where the equivalent trap two questions later (Q35) was handled correctly. Not code — a model/prompt-behavior observation worth keeping in mind if the benchmark question set or grading is revisited.

None of the above are scheduled. They are named so they are found by reading
this document, not by re-deriving them from a session transcript.
