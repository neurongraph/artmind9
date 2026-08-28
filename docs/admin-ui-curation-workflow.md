# artmind admin UI — curation workflow (same-as + conflicts + rebuild)

**Status: backlog — not scheduled.** Written after actually running Step 6 of
the [Phase 8 cutover](redesign-phase8-runbook.md) by hand (CLI + a
hand-rolled Artifact review console) against the real banking corpus: 180
same-as proposals, 12 cross-domain conflicts, 8072 entities. Everything below
is grounded in what that session actually hit, not speculation. Companion to
[admin-ui-plan.md](admin-ui-plan.md) / [admin-ui-spec.md](admin-ui-spec.md) —
follows their Goal/Non-goals/Lane structure; would land as a new Lane B tab
("Curate") once scheduled.

## Goal

Give an operator one page to run a same-as/conflict curation pass end to end —
review proposals, decide, execute, rebuild — without leaving the browser or
hand-writing scripts. Everything this document proposes already has a CLI
equivalent (`sameas propose/list/approve/reject`, `ingest resolve-conflict`,
`projection rebuild/status`); the UI wraps it, it doesn't replace it.

## Non-goals

- Not a general graph editor. Curation only: same-as identity, conflict
  resolution, the rebuild that applies them.
- Not touching `sameas propose`'s adjudication model or the merge-vs-link
  decision in `_plan_groups` (see "A defect this workflow should also fix,"
  below, for the one exception that's a real bug, not scope creep).
- Not real-time collaborative editing — one operator's session at a time is
  fine, matching every other Lane B widget.

## What actually happened, and where it hurt

1. **`sameas propose` and `sameas list`'s JSON output doesn't scale to human
   review.** 180 proposals as raw JSON (with full evidence chunks in the
   `propose` output — tens of KB) is not something a person reviews in a
   terminal or a chat window. We ended up hand-building an HTML review
   console as a Claude Artifact — grouped by entity class, searchable,
   filterable, with Approve/Reject buttons — because nothing in artmind
   itself renders this queue for a human.

2. **Same-as and conflicts are two different node types
   (`:SameAsProposal` vs `:Conflict {_source: 'adjudicator'}`) with two
   different CLI command groups (`sameas` vs `ingest resolve-conflict`), but
   conceptually one curation queue.** The runbook itself says the proposer
   "emits two outcomes... both land in the same queue" — they don't,
   mechanically, today. An operator has to know both commands exist and
   query both.

3. **`sameas approve`'s domain-scoping is silently useless for a
   single-top-level-domain corpus.** [`artmind/sameas.py:149`](../artmind/sameas.py#L149)
   does `k[2].split(".")[0]` to compute "touched domain families" —
   for `banking.policy`, `banking.sop_guides`, etc. this always yields
   `["banking"]`, so **every single `approve()` call reruns a full
   `projection.full_rebuild()` over the entire corpus** (in this session,
   ~2h51m each). Calling it 180 times was never viable. We worked around
   it by writing directly to `same_as.yaml` and the `SameAsProposal` nodes'
   status, skipping the redundant rebuilds, and running one bare
   `projection rebuild` at the end — which is the runbook's own prescribed
   closing step anyway, just done once instead of interleaved 180 times.
   **This is a real defect** (see below), not just a UX gap.

4. **A full rebuild takes hours with no progress signal beyond log lines.**
   We watched `2026-08-28 20:42:19 [INFO] Full projection rebuild over 8072
   key(s)` for three hours with nothing but occasional warning lines and a
   process-alive check. No ETA, no "N of 8072 done," no way to tell a stuck
   run from a slow one short of `ps aux`.

5. **A same-as "approval" can mean two different things, and nothing tells
   you which one you're about to get.** [`_plan_groups`](../artmind/projection.py#L784)
   only actually merges two entities into one node when they share the
   *same domain and class* as the canonical; a cross-domain pair (the
   overwhelming majority of what the adjudicator proposes — banking corpus
   documents describe the same concept once per domain by design) becomes
   a **link** instead — a `SAME_AS` edge, both entities kept distinct. In
   this session all 126 approved groups turned out to be links, zero were
   merges, and that wasn't obvious until we went looking afterward. An
   operator approving "Identity Verification [organization] = Identity
   Verification [communications]" almost certainly expects one entity to
   result, not two entities plus an edge.

6. **Decision continuity across a long review session had no real home.**
   We used the Artifact's `localStorage` as decision scratch state, which
   works for one browser but isn't the graph, isn't shared, and isn't
   durable in the way an operator's actual curation call should be —
   there's no record in artmind itself of "which proposals has anyone
   looked at" until the moment `approve`/`reject` actually runs.

7. **Batch decision-making by pattern is a real, recurring need with no
   tool support.** Once a handful of decisions establish a taxonomy (e.g.
   "identity verification" is narrower than generic "KYC verification,"
   never merge across that boundary), the remaining candidates in the same
   entity class overwhelmingly follow the same rule. We did this by hand
   (an LLM reading the decided set, inferring the pattern, applying it to
   the rest, flagging genuine judgment calls) — a workflow could offer this
   as a first-class "suggest the rest" action instead of requiring a
   side-channel conversation with an agent.

## A defect this workflow should also fix

[`sameas.approve()`](../artmind/sameas.py#L97)'s `touched_domains` computation
(item 3 above) should scope by the **full** domain string (`banking.policy`,
not `banking`), not its first dot-segment. Whatever motivated splitting on
`.` in the first place (compatibility with a coarser "domain family" concept
elsewhere?) needs to be checked before changing it — flagging here rather
than fixing blind, since it's outside a curation-UI backlog item's own scope,
but any curation workflow built on top of the current behavior inherits a
rebuild cost that makes per-decision approval (rather than batching)
unusable at real corpus scale.

## Proposed workflow

**One Lane B tab, "Curate."** Three stages, each a clear step the operator
moves through — not three separate pages:

### Stage 1 — Generate

- A "Propose" panel: domain picker (defaults to every domain), a button that
  calls `sameas propose` (this already includes conflict detection — no
  separate trigger needed). Shows a spinner/progress state while the
  adjudicator runs, since this itself takes real time over a large corpus.
- Surfaces the run's own summary (candidates found, by kind) when done.

### Stage 2 — Review

- **One unified queue**, same-as proposals and conflicts together, backed by
  a new endpoint that merges both node types server-side (see "New JSON
  endpoints," below) rather than making the frontend reconcile two shapes.
- Grouped by entity class (same-as) / aspect (conflicts), collapsible,
  counts per group, search box, domain filter, status filter — everything
  the hand-rolled Artifact console already proved useful, just server-backed
  instead of `localStorage`-backed.
- **Every same-as row shows a computed Merge/Link badge up front** — run
  `_plan_groups`' own same-domain-and-class check against the proposal's
  members before rendering, so "these become one entity" vs "these become
  two entities joined by an edge" is never a surprise discovered after the
  fact.
- Decision buttons (Approve/Reject, Resolved/Dismissed) write to the graph
  **immediately** on click — a lightweight status-only update
  (`SameAsProposal.status` / `Conflict.status`), no rebuild — so decisions
  are durable in artmind itself as the operator works, not stuck in browser
  storage. This is exactly `sameas reject`'s existing cost profile; making
  `approve` this cheap too is what the defect fix above unlocks.
- **"Suggest the rest" action**, scoped to one entity class or the whole
  queue: sends the already-decided items in scope plus the undecided ones to
  an LLM call, asking it to infer the operator's own pattern and propose
  decisions for the rest, each with a stated rationale and a confidence
  level. Renders as pre-filled but **not yet committed** decisions,
  visually distinct from human-made ones, individually editable before
  the operator confirms the batch. Low-confidence suggestions are called
  out, not buried — mirroring what the agent did by hand this session.

### Stage 3 — Apply

- A single "Apply & Rebuild" action once the operator is satisfied with the
  queue's decisions (not required to be 100% decided — partial batches are
  fine, matching how this session actually worked: KYC_VERIFICATION got
  fully decided and applied conceptually before other classes were even
  reviewed).
- Runs as a background job (Lane B already has a job/polling pattern from
  the ingest dashboard — reuse it): batch-writes `same_as.yaml` +
  proposal/conflict statuses (cheap), then one `projection rebuild`
  (expensive, the real cost).
- **Progress surfaced during the rebuild**, not just a spinner: `all_keys()`'s
  total is known before the loop starts, so the backend can report "N of
  8072 keys" as `rebuild()` progresses (would need a lightweight progress
  callback threaded through `rebuild()`/`rebuild_key()` — currently there
  isn't one) plus elapsed time, so an operator can tell a 3-hour rebuild is
  healthy rather than watching a static screen.
- Ends by surfacing `projection status` (drift cleared, both hashes) and a
  link to the updated entity counts — closing the loop the operator can see.

## New JSON endpoints (mirrors the existing Lane B pattern — GET unless noted)

- `GET /api/curate/queue` — merged same-as + conflict proposals, each row
  carrying the computed merge/link classification for same-as rows.
- `POST /api/curate/decide` — `{id, kind: sameas|conflict, decision}`, writes
  status immediately (no rebuild).
- `POST /api/curate/suggest` — `{scope}`, returns LLM-suggested decisions for
  undecided items in scope, uncommitted.
- `POST /api/curate/apply` — kicks off the background apply-and-rebuild job;
  returns a job id the frontend polls, same shape as ingest dashboard jobs.
- `GET /api/curate/apply/{job_id}` — progress (`keys_done`/`keys_total`,
  elapsed, current phase: writing groups → rebuilding → recording status).

## Open questions

1. Should "Suggest the rest" require a same LLM call shape/cost accounting
   as `projection synthesize` (explicit, budgeted, never automatic)? Almost
   certainly yes — it spends model budget the same way.
2. Does the rebuild progress callback belong in `projection.rebuild()` itself
   (useful for every caller, not just this UI) or stay UI-layer-only? Leaning
   toward the former, since `sameas approve`'s own long-running case would
   benefit too, once its domain-scoping is fixed.
3. Multi-operator conflict: two people curating the same queue at once isn't
   handled by "write status immediately" alone (a lost-update race on the
   same proposal). Low priority — Lane B has never needed to handle this
   elsewhere — but worth a one-line note if it ever does.

## Acceptance (once scheduled)

- An operator can run propose → review (with merge/link badges visible) →
  decide (individually and via "suggest the rest") → apply, entirely in the
  browser, for a queue the size of this session's (180 + 12) without writing
  a script or leaving the tab.
- `sameas approve`'s per-decision cost is O(decision), not O(corpus) — the
  defect fix is a prerequisite, not a nice-to-have, since without it Stage 2's
  "decide immediately" design either reruns a full rebuild per click (the
  current CLI behavior) or has to fake cheapness by deferring writes, which
  is exactly the workaround this session had to build by hand.
