# Phase 7 implementation notes

What actually landed for Phase 7 (surfaces: skills, docs, prompts, UI), against the
scope given at the start of the phase and the plan's bullets in
[redesign-phase-plan.md](./redesign-phase-plan.md). Read
[CONTEXT.md](../CONTEXT.md), [projection-pipeline.md](./projection-pipeline.md),
[document-identity.md](./document-identity.md), [stores-and-repos.md](./stores-and-repos.md),
and Phases 2–6's own implementation notes first — this phase touched no runtime
mechanism (with the two exceptions under "Real bugs found and fixed," below); it
brought every user-facing and agent-facing surface into agreement with what those
five phases actually built.

This phase had an unusual shape: almost no new logic, a very large surface, and the
stated risk was omission, not difficulty. That held. Every real finding below came
from cross-checking a claim against the live code (`grep`, direct introspection of
the Click tree, or the admin-ui loaded against real AuraDB) rather than from reading
prose in isolation — several things the phase brief expected to be broken were
already fixed by earlier phases, and several things assumed fine were not.

---

## What changed, by workstream

### 0. The structural-schema drift test (built first, everything else graded against it)

- **`artmind/structural_schema.py`** (new) — the canonical facts about the fixed
  structural graph (node labels + properties, relationship types + endpoints,
  History label pairs, retired names) as plain data, not prose. Both consumers read
  it: `text2cypher.STRUCTURAL_SCHEMA` is now `render_prompt_block()` output instead
  of a hand-maintained string, and the new drift test reads the same constant.
- **`test/test_query_skill_structural_schema.py`** (new) — mirrors
  `test_cli_guide.py`'s `COMMAND_GROUPS` precedent exactly: hermetic, reads
  `artmind-query/SKILL.md` as text, asserts every canonical name from
  `structural_schema.py` appears somewhere in it and no `RETIRED_NAMES` entry
  appears at all (not even as a "this used to exist" footnote — see the note on
  historical framing below). This is the actual guard the phase asked for.
- **A real, previously-uncaught inaccuracy found while building this**: the
  hand-written `STRUCTURAL_SCHEMA` constant (accurate everywhere else, and already
  updated for Phase 4 in commit `de6098a`) claimed `:Conflict` has one shape and
  `EVIDENCE` always points to `:DocChunk`. Neither is true — `:Conflict` has had two
  shapes since Phase 3/6 (`_source: 'projection'`, single-entity, `EVIDENCE ->
  :Observation`, no `CONFLICTS_WITH`; and `_source: 'adjudicator'`, cross-entity,
  `EVIDENCE -> :DocChunk`, with `CONFLICTS_WITH`), and nothing had ever encoded that
  as structural data before. Fixed in `structural_schema.py` directly.

### 1. `artmind-query/SKILL.md` — full rewrite of the structural-schema section

Rewrote the "Fixed Structural Schema" section around Observation/Entity, the
two-hop `AGGREGATES`→`EXTRACTED_FROM` provenance path, single `RELATES_TO
{rel_type}`, and the History label pairs. Inverted the `--asOf` guidance (removed
from all ten patterns + `entity-listing`/`entity-resolve`/`entity-context`/`graph
metadata`; kept, with a narrower "floor" meaning, on `vector-text`/`chunks`/`docs
list`/`pattern10`/`db timeline`/`db sql`; `entity-history`'s is the one genuine
point-in-time filter). Rewrote Adjudicate around `:Conflict`'s two shapes, including
an honest gap: **`query graph conflicts` cannot reach the projection shape at all**
— there is no dedicated command for it yet; the documented workaround is reading
`entity-history` for two facts sharing a `_valid_from` with different values.

**A steer that changed how the rest of the phase was written**: partway through,
you told me to stop writing sentences like "there is no `(:UserChat)-[:MENTIONS]->
(:Entity)` edge (dead since the observation model landed; nothing writes it)" —
correct, but dead weight, since a full re-ingest means no reader will ever need the
historical footnote. From that point on, a retired name is simply absent, not
explained-as-absent, everywhere in this phase's output (skills, `CAPABILITIES.md`).
`RETIRED_NAMES` in `structural_schema.py` enforces this as a blanket ban, not a
"unless historically framed" exception — I had drafted the exception first and
reverted it once you corrected the framing.

### 2. `artmind-refine` → `artmind-curate` (rename + rewrite)

- `git mv artmind/skills/artmind-refine artmind/skills/artmind-curate`; full content
  rewrite. Deleted the six-step pipeline narrative and all of the old Workflow D
  ("real conflict or older document?" — dissolves, since superseded content is
  structurally out of every index). **Inverted the safety framing**: same-as groups
  are declarative (`same_as.yaml`) and reversible (remove the group, rebuild) — the
  skill now explicitly tells the reader to review generously, the opposite of the
  old "merges delete nodes with no un-merge, review defensively" stance.
- New content: `projection {rebuild, status, synthesize}`; the review queue's two
  outcomes (same-as proposal vs. conflict); and, per your explicit instruction, the
  **two-step `sameas approve` workflow** stated as a named procedure — approving
  runs a domain-scoped rebuild and does *not* clear `projection status`'s drift
  flag, so a bare `projection rebuild` is a required second step, not an optional
  cleanup.
- **Reference sweep, all landed**: `help.py`'s `_DESTRUCTIVE_CONCEPTS` (key removed
  outright, not renamed — see "Decisions" below), `profiles.py` (`ADMIN_PROFILE.skills`
  + both prose blocks), both opencode persona files, two test files rewritten to
  assert the new true/false split, and — the actual trap named in the prompt —
  `~/.artmind/.claude/skills/artmind-refine` **did** survive `artmind init` at the
  exit gate exactly as predicted, and `.pi/skills/artmind-refine` was a stale
  symlink `just dev-refresh-skills` had to remove. Both confirmed clean afterward.
- **Deliberately not touched**: `README.md` — far more broadly stale than a
  name-swap (it documents `ingest refine-pipeline`/`refine_pipeline.py`, deleted
  since Phase 3; renaming just the skill reference there would have made it *more*
  misleading, describing a skill correctly while the surrounding workflow it's
  embedded in is still entirely dead). Not in this phase's stated bullet list;
  flagged below for its own pass. `artmind_canvas/.../profiles.py`'s one stray
  mention — a separate subproject with its own CONTEXT.md — left for your call.

### 3. `artmind-update/SKILL.md` — shrunk, as predicted (214 → 205 lines)

Deleted "Correcting Supersession Outside a Session" entirely (`update supersede`,
the node-level command, doesn't exist — Step 2b's retraction flow is the only path
and already covers it). Fixed a stale worked example ("1 node superseded" → "1 fact
retracted", matching the real `nodes_retracted` field). Rewrote "Resolving Similar
Nodes": `refine-graph --filter` still exists and is still the right tool for
scoping to two specific suspect names (its multi-name comma-split has no
equivalent in `sameas propose --nameFilter`, which only takes one substring) — kept
it, corrected what happens after (writes a proposal now, not a direct merge; added
the `sameas`/artmind-curate hand-off). Step 2b itself was already correct from
Phase 6 and untouched.

### 4. `artmind-ingestion-helper/SKILL.md`

Added: frontmatter identity seeding on first vault-native ingest (with the vault
git commit), the deferred-rebuild-then-`projection synthesize` pattern for bulk
loads (verified this applies to both a folder `sync` and a multi-file `async` job —
Phase 4 fixed `worker.py`'s per-file rebuild to match `ingest sync`'s batching), and
`docs archive`/`archived`/`restore-from-archive`/`reindex` in Situation H — these
had actually landed in Phase 5 and the skill was still saying archive was "a later
phase's work," which was itself stale and removed. Fixed Situation G and every
`refine-graph` cross-reference to describe proposing into the same-as queue, not
merging directly.

### 5. `artmind-create-schema/SKILL.md` — full rewrite (260 → 379 lines)

The entire skill was teaching the pre-Phase-1 model: hand-written
`entities_prompt`/`properties_prompt`/`relationships_prompt` prose, list-form
`entity_types`, `event_at`. Rewritten around the meta-schema contract
(`schema_validate.py`'s actual enforcement — mandatory `kind`, map not list,
reserved `_` prefix), Steps 4–6 reframed as "declare classes, properties, relations"
instead of "write three good prompts," the name-is-identity rule folded into Step 3
with real examples verified against shipped schemas (`RISK_METRIC` recurrent vs.
`AUDIT_FINDING`/`TRANSACTION` occurrent), and a corrected Step 7 (`temporal:
valid_from`/`valid_to` only — `event_at` explicitly named as silently ignored, not
just old). **Assets replaced, not hand-converted**: `assets/fiction_schema.yaml`
and `assets/personal_journal_schema.yaml` are now byte-identical copies of the
real, currently-shipping, already-migrated production schemas (`diff` confirmed),
rather than a separately maintained "example" that could drift from reality.

### 6. Generated surfaces — verified against the actual rewritten output, not re-read for plausibility

Copied all five rewritten skills into a temp `ARTMIND_HOME` (mimicking what `init`
seeds) and called `help._skill_concepts()` for real; rendered `cli_guide.py`'s
fragment and confirmed the corrected `ingest refine-graph` docstring (see "Real
bugs," below) appears verbatim; rendered `schema_reference.build_schema_dict`
against all seven touched schemas and confirmed `event_at` doesn't appear anywhere
in a 432 KB fragment; stood up the actual FastAPI app via `TestClient` and hit
`/api/help/concepts`, `/api/cli-guide`, `/api/schema-reference` for real. All
confirmed again, live, against real AuraDB, at the exit gate (see below).

### 7. `CAPABILITIES.md` restructure (the largest single edit of the phase)

Full restructure of chapters 3–4 from scratch — "Knowledge Graph Construction" →
**"Observations — Turning Documents into Immutable Facts"**, "Graph Refinement &
Curation" → **"The Projection & Curation"** — replacing content about entirely
deleted machinery (accretive merge, the six-step refine-pipeline, entity-versions
snapshots, `_retire_orphaned_entities`) with the real mechanism. This is the actual
sources→observations→projection→query reframing; chapter numbers 1/2/5/6/7/8/9/10/11
were kept (only their content was fixed where wrong), so the restructure reads as a
reordering of what construction/refinement *meant*, not a renumbering cascade.

Found and fixed drift far beyond chapters 3–4 while verifying every cross-reference
against code: Ch 1 (schema prompts are assembled at runtime, not authored — closes
a real bug class, the relationship-prompt header-leak that affected ~1/5 of edges
before Phase 1); Ch 6 (five structural node types not four, `--asOf` removal
verified live against `cli.py`, `timeline`/`entity-history` re-specified, and the
same projection-conflict gap documented in the skill); Ch 7 (fully rewritten around
`_artmind_id`/the resolution table/retire/archive/reindex — the old chapter still
described `docs clean`, deleted since Phase 3); Ch 8 (dead cross-references to
deleted rows and dead anchors, `_upsert_entity`, `_link_entity_in_session`); Ch 9
(the Phase 5 snapshot inversion — sources-only export, unconditional rebuild+sweep
on restore, `registry` dropped/`originals` added); Ch 11 ("refinement" → "curation",
and a pre-existing — not newly introduced — finding: the end-user opencode persona's
prose names operator-only skills `QA_PROFILE` never actually grants it).

### 8. `justfile` full sweep

Deleted `update-supersede` (calls a command gone since Phase 3, per the plan's own
finding) and the two commented-out dead stub recipes Phase 6 left in place. Then
ran a **programmatic** sweep rather than a second eyeball pass: introspected the
live Click tree and checked every one of the 89 `uv run artmind ...` invocations for
both command-path existence and flag validity against the command's actual
registered options. Found one real, previously uncaught bug this way (see "Real
bugs," below) and one real gap (`query entity-history` had no recipe at all — a
significant new Phase 4 command with zero justfile coverage). Both fixed.

### 9. Backlog items

- **`placement.py`**: found a concrete, verified cross-repo gap — the canvas
  backend's confirm endpoint (`AcceptedPlacement`) already expects a `title` field,
  but `propose_placement()` never generated one (only a kebab-case
  `target_file_hint`, a different thing). Added `title`/`title_confidence`;
  rewrote the module docstring to state the frontmatter mapping explicitly per
  `document-identity.md` (authored: `title`/`area`/`project`/`tags`; system-owned:
  `domain` → `_domain`).
- **Property-hint audit**: read all 735 property hints across the 14 unaudited
  schemas (plus re-checked the 2 Phase 3 already touched, since the finding below
  turned out to span all 9). Found and fixed the single highest-value, most
  systemic issue: `ACCOUNT.balance_rules` and `TRANSACTION.thresholds` were
  byte-identical, unformatted compound-value hints across **all 9 banking-family
  schemas** — the exact class of bug Phase 3 fixed on `RATE_ENTRY`, never caught
  because that pass was scoped to one class. Split into
  `balance_minimum`/`balance_maximum`/`dormancy_threshold` and `threshold_value`,
  format-pinned in the same style as the existing fixed hints; validated and
  confirmed the assembled prompts render correctly, and confirmed live in the
  admin-ui's Schemas tab against the real run folder (searching `balance_minimum`
  returns all 9 schemas). The remaining candidates found (below, "Deferred") are
  documented, not fixed, per the time-boxing the brief called for.
- **Opencode personas / admin-ui**: no further work needed beyond Workstream 2's
  rename sweep and Workstream 6/the exit gate's live verification.

---

## Real bugs found and fixed (not skill-doc drift — actual runtime code)

These came up while writing accurate documentation for the exact command being
described, and were fixed rather than merely flagged, matching the precedent set
by Phases 2–6's own notes ("leaving a provably-wrong pattern... would have been
worse than fixing it").

1. **Seven schemas' `temporal: event_at` was a silent no-op.** Traced through
   `ingest._build_observations`: the observation builder only recognizes the
   literal strings `valid_from`/`valid_to` on a property's `temporal:` tag —
   `event_at` has been dead since Phase 4 deleted the entity-level `event_at` axis.
   18 occurrences across `banking.cases`, `banking.risk_governance`, `contracts`,
   `fiction`, `project_governance`, `sales_collateral`, `personal_journal` fixed
   (`event_at` → `valid_from`). Confirmed via `schema_validate.validate_all()` and
   the full suite.
2. **`ingest refine-graph`'s own docstring and success message were stale.**
   Claimed to "merge aliases" and printed `merged=N`; Phase 6 changed it to write
   same-as *proposals*, and the `merged` stats key doesn't even exist any more — the
   CLI was silently printing `merged=0` on every successful run. Fixed both.
3. **`update` group's docstring claimed a `supersede` subcommand Phase 3 deleted.**
   Fixed.
4. **`update.py::export_chats` — both `--format by-entity` and `sequential`'s
   "Mentions:" line queried `(c:UserChat)-[:MENTIONS]->(e:Entity)`**, an edge
   nothing has written since the observation model landed (`write_user_chat` links
   via `EXTRACTED_FROM`/`AGGREGATES`, exactly like a document's chunks) — silently
   returning zero results forever, never erroring. Found this one because you asked
   directly "should we not fix the bugs" after I'd initially only flagged it;
   reconsidered and fixed it, since the actual change was small and well-scoped
   (two Cypher blocks in the one function already open). Added a regression test
   asserting on the actual Cypher sent (`MENTIONS` absent, `EXTRACTED_FROM`/
   `AGGREGATES` present) rather than a mocked return value — the existing tests
   used `MagicMock().data.return_value = [...]`, which is exactly the trap
   CLAUDE.md documents: it passes regardless of whether the query is right.
5. **`justfile`'s `query-graph-timeline` recipe called `--entityId`**, removed from
   `timeline` in Phase 4's re-spec (domain-scoped now, no entity parameter at all)
   — broken since Phase 4 landed, caught by the programmatic flag-validity sweep,
   not by reading. Fixed to the current `--from`/`--to` signature; added a new
   `query-entity-history` recipe (the command had none at all).
6. **`ACCOUNT.balance_rules`/`TRANSACTION.thresholds`** — see "Backlog items"
   above; the property-hint-format bug, at the same scale as Phase 3's original
   finding, on a class Phase 3 didn't look at.

None of these were speculative — each was confirmed by reading the actual
implementation (`ingest.py`, `update.py`, the live Click tree) before being
recorded as a finding, and each fix was verified (test suite, `domains validate`,
or a live admin-ui check) before being called done.

---

## Decisions taken, and why

**`artmind-curate` carries no `_DESTRUCTIVE_CONCEPTS` entry at all**, rather than
inheriting `artmind-refine`'s `True`. This is a genuine behavior change to the
admin-ui's help panel (confirmed live: "curate" shows no destructive badge, "update"
does), not just a rename — justified because the underlying capability actually
inverted: same-as groups are declarative and reversible, so there is no destructive
graph surgery left in that skill's workflow. Flagged as a decision rather than a
silent side effect because it changes what the UI visibly tells an operator.

**A retired name must be gone, not footnoted as historical**, per your
mid-session correction. This shaped the `RETIRED_NAMES` guard (a blanket ban, no
"unless explained" exception) and every subsequent rewrite in this phase,
including `CAPABILITIES.md`'s heavy edits — a document that could have accumulated
a lot of "this replaced X" narration instead states the current model directly.
The one deliberate exception, made consciously rather than by oversight: technical
reference material (this file, `CAPABILITIES.md`'s grounding notes) still names a
deleted mechanism when directly explaining *why the current one exists* (e.g.
"replaces `entity-versions` and its snapshot-on-supersede mechanism") — that
register is different from agent-facing skill prose, which is what your correction
was actually about.

**Property-hint fixes and the `event_at` fix were applied directly, not just
recorded as findings.** Both were mechanical, high-confidence, and verifiable
without a live corpus re-ingest (schema validation + prompt-assembly checks +, for
the banking fix, a live admin-ui confirmation). The remaining, lower-confidence
property-hint candidates were left as documented findings rather than fixed
blind, consistent with Phase 3's own precedent that this class of bug needs a
live run to actually confirm, not just a plausible-sounding hint rewrite.

---

## Exit gate

```
just dev-stop-daemons && just dev-install     # PASS — 5 skills refreshed from package
just dev-test                                 # PASS — 1660 passed, 14 skipped, 0 failed
```

`~/.artmind/.claude/skills/` after `init`: contained **both** `artmind-curate` and
`artmind-refine` — the exact trap the prompt named, reproduced live, not just
described. `rm -rf ~/.artmind/.claude/skills/artmind-refine` applied; `just
dev-refresh-skills` run afterward and found (and removed) a second instance of the
same trap: a stale `.pi/skills/artmind-refine` symlink the checkout-side refresh
target's own self-healing logic caught.

`just dev-cli-help` spot-checked against every skill's claims during writing, not
just at the end — e.g. `domains entities-prompt`'s "assembled at runtime" framing
verified against the actual command tree before it was written into the skill.

Grep sweep across all five skills for the full dead-term list
(`refine-pipeline`/`detect-supersession`/`normalize-time`/`consolidate-descriptions`/
`entity-versions`/`docs clean`/`docs purge`/`--replace`/`--force`/`event_at`/
`superseded_by`/`MENTIONS`): every remaining hit checked individually —
`--force` on `projection synthesize` and `--replace`'s "there is no --replace flag"
line are both live/correctly-historical, `event_at` in `artmind-create-schema`
correctly warns it's retired, `superseded_by` in `artmind-query` is the real,
current `:Document` property (unrelated to the removed Entity-level mechanism).
`MENTIONS`: zero hits anywhere.

Every remaining `--asOf` mention in `artmind-query` verified individually against
which commands still accept it: `pattern10` (presence flag), `vector-text`,
`db timeline`, plus `entity-history`'s genuine point-in-time filter, explicitly
distinguished from the entity commands that now take none at all.

**Admin-ui loaded live, against real AuraDB** (not just `TestClient`): Ingest &
browse tiles show `Observation` as a distinct row from `Entity` per domain,
matching the new structural census; CLI guide renders live-introspected commands;
Schemas tab shows all 9 banking schemas with `kind` badges per class, and
searching `balance_minimum` returns exactly the 9 schemas the property-hint fix
touched; the agent console's skill chips show `curate` with **no** destructive
badge and the exact description text from the new `SKILL.md` frontmatter, while
`update` correctly carries the badge — a live, end-to-end confirmation of the
`_DESTRUCTIVE_CONCEPTS` decision above, not an inference from reading the code.

---

## Deferred, on purpose

| Deferred | To | Why |
|---|---|---|
| `README.md` full refresh | A dedicated pass, not scoped here | Documents an entire dead workflow (`ingest refine-pipeline`), not just an old skill name — renaming in place would have made it more misleading, not less. Not in this phase's stated bullet list. |
| `artmind_canvas/backend/.../profiles.py`'s stray `artmind-refine` mention | Your call | Separate subproject, own CONTEXT.md/roadmap; one-line prose fix, trivial whenever you want it done. |
| The end-user opencode persona (`artmind.md`) naming operator-only skills `QA_PROFILE` doesn't grant | Whichever phase next touches `profiles.py`/`artmind/opencode/` | Pre-existing since before this phase, contradicts `profiles.py`'s own documented intent ("deliberately not here — those are operator concerns"); recorded in `CAPABILITIES.md` §11.4's grounding note with a concrete test hint (diff each persona's skill list against its `AgentProfile.skills` tuple) rather than fixed, since it wasn't part of the rename and fixing it well means deciding the persona's actual intended scope, not just deleting lines. |
| A dedicated `query graph conflicts` (or equivalent) surface for projection-shape conflicts | Not currently planned | Named as a real gap in both `artmind-query/SKILL.md` and `CAPABILITIES.md` §4.3/§6.6.2 rather than worked around silently. The current answer ("read `entity-history` for two same-instant facts") is honest but not a dedicated command. |
| Remaining property-hint candidates (`RISK_METRIC.breach_threshold`/`.actual_value`, `ROLE_ACTOR.approval_limit`, `METRIC_TARGET.value`, `general/METRIC.value`, `contracts` currency/duration fields, `technical_paper/METRIC.typical_range_or_benchmark_value`, `banking.cases/IMPACT_ASSESSMENT.count`, `sop_guides` duration fields, `banking.policy/IDENTIFICATION_DOCUMENT.retention_period`) | Phase 8's re-ingest | Each is a plausible instance of the same class of bug (unformatted scalar hint on a value type that could be written multiple ways), but none were verified against a live extraction run, matching Phase 3's own precedent that this class of finding needs a real corpus to confirm. **The signal to watch at Phase 8 is exactly what Phase 3 named**: intra-document conflicts on these specific scalar properties. |

## Open questions for Phase 8

1. **The projection-conflict query gap** (above) will be more visible once the
   corpus is re-ingested at scale — if it turns out to matter in practice, it's a
   real, scoped feature (a `query graph conflicts --source projection` mode, or
   similar), not a documentation fix.
2. **Whether the remaining property-hint candidates are real bugs or false
   positives** can only be settled by the re-ingest itself. Watch intra-document
   conflicts on the specific properties listed above first — they're the
   highest-confidence candidates from this reading pass, not an exhaustive list of
   every hint in the 14 schemas.
3. **The end-user opencode persona's actual intended scope** — does it deserve the
   same skill set as the admin persona minus schema-authoring/ingestion (matching
   `QA_PROFILE` exactly), or was naming "graph maintenance" and "ingesting
   documents" in its prose actually intentional and `QA_PROFILE` is the one that's
   under-scoped? This wasn't something Phase 7 was positioned to decide unilaterally.
