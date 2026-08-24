# Skills review

What the observation/projection redesign does to `artmind/skills/` — 1,640 lines
across five skills. Companion to
[redesign-change-inventory.md](./redesign-change-inventory.md).

## The pattern worth seeing first

A large share of these skills is **workaround knowledge** — instructions that exist
only because the model has a defect. The redesign removes the defects, so the
instructions go with them:

| Current instruction | Why it exists | After |
|---|---|---|
| *"**Default to `--asOf today` on every retrieval** — without it there is NO temporal filter, and superseded documents surface alongside current ones"* | no default currency filter | **deleted** — the projection is current by construction |
| *"EXCEPTION: pattern5 and pattern10 ignore `--asOf`… judge currency yourself"* | two patterns can't scope | **deleted** |
| *"there is no built-in un-merge, so record the recommendation, don't attempt a fix"* | `apoc.mergeNodes` is destructive | **deleted** — remove a member from the same-as group and rebuild |
| *"check each row's `materialized` flag: `false` means the `Conflict` node has since been deleted"* | annotations orphan | **deleted** — conflicts are rebuilt, never orphaned |
| *"re-running detect-conflicts is not a guaranteed no-op — `Conflict.id` hashes the aspect text"* | LLM-derived ids | **deleted** — deterministic keys |
| *"`entity-versions` will have no history for a document superseded before this capability shipped"* | history couldn't backfill | **deleted** — observations are the history |
| *"Merges precede conflicts… conflicts precede consolidation… one embedding sweep at the end"* | six ordered steps a human must not reorder | **deleted** — one rebuild, automatic |

That is roughly 400 lines of compensation. It is also the clearest evidence the
redesign is worth doing: every one of these is a thing an operator currently has to
remember, and forgetting it silently produces a wrong answer.

---

## `artmind-query` (393 lines) — heaviest rewrite

**Wrong, must change**

- **"Fixed Structural Schema"** (§19–31) — every line. `MENTIONS` is deleted;
  `EXTRACTED_FROM` now originates from `:Observation`; the property lists change
  (`_domain`, `_status`, `_valid_from`, no `event_at`, no `superseded_by`);
  *"Entity-to-Entity relationship types are domain-specific — always check
  metadata"* becomes one `RELATES_TO` with a `rel_type` property. And it omits an
  entire second node population.
- **The `--asOf today` default** (§300–307) — **inverts**. Currently mandatory;
  afterwards `--asOf` is *removed* from every entity command, and passing it to
  `vector-text`/`chunks` means a valid-time question, not a currency one.
- **Retrieve table** (§268–269) — `timeline --entityId` re-specified as
  domain-scoped; `entity-versions` → `entity-history`.
- **Adjudicate** (§325–377) — simplifies sharply. The whole *"is this a live
  disagreement or a superseded document?"* branch disappears: superseded content is
  out of the index, so it cannot reach the comparison.
- **Store routing** (§204–214) — SCD-2's `_is_current` → `_status`.

**New content needed**

`_temporal_props` and when it means "drill into `entity-history`" · the Observation
layer and that ordinary queries never touch it · `:Conflict` as a projection output
rather than a detection pass · reading `projection status` staleness warnings.

**Survives intact:** the whole store-routing section (§102–218) — bridge, hybrid,
records-plus-guidance, and all three worked examples.

## `artmind-refine` (399 lines) — largest deletion

Its own description names five capabilities; **three are deleted** (temporal
normalization, supersession, destructive merging).

| Section | Fate |
|---|---|
| *"Why order matters"* — the six-step pipeline | **delete** |
| Safety rules — *"merges delete alias nodes with no un-merge"* | **invert** — same-as groups are declarative and reversible; the skill's central risk framing was built on irreversibility |
| Workflow A — propose/review/apply over `refine-pipeline` | **rewrite** as `sameas propose` → review → `sameas approve` |
| Workflow B — focused merge | rename to `sameas propose --filter` |
| Workflow C — merge forensics | **rewrite** — "there is no un-merge" is now false |
| Workflow D — *"real conflict or just an older document?"* | **delete** — the question dissolves; history is out of the index |
| *"Reading superseded entity values"* | **delete** → `entity-history` |
| Known Caveats (4) | **delete** all four |
| Workflow E — structured classifications | **survives**, minus `_is_current` → `_status` |
| `scripts/summarize_gates.py` | rewrite — it parses `pipeline_report.json`, which no longer exists |

**New:** `projection {rebuild, status, synthesize}`, and the review queue with its
**two outcomes** — a candidate pair is either the same thing (same-as proposal) or
two things making incompatible claims (conflict proposal).

## `artmind-update` (211 lines) — gets shorter

- **Step 2b "Node-Level Supersession"** (§73–125, 50 lines) — **delete entirely**.
  It becomes automatic: a chat observation carries today's `valid_from`, a 2024
  document's carries 2024's, and the winner rule already picks the chat.
- **"Correcting Supersession Outside a Session"** (§185–202) — delete;
  `update supersede` is gone.
- §125 — *"they get `valid_to`, `superseded_by`, and `status: 'superseded'`"* — all
  three properties deleted.
- Step 3 output — `nodes_superseded` gone; report observations written instead.
- "Resolving Similar Nodes" — `refine-graph --filter` → `sameas propose --filter`.

The net is a **materially simpler skill**, which is the right signal: the complexity
was in the write path, and the write path is now "record what was asserted, rebuild".

## `artmind-ingestion-helper` (377 lines) — moderate

- **Situation G** (duplicates) → `sameas propose`
- **Situation H** (remove a document) → `docs clean`/`docs purge` become
  `docs retire`/`docs archive`; `--replace` deleted (always a replace now)
- **Full Pipeline Reference** (§212–236) — steps 3–4 are `refine-graph`
- **Common Gotchas** — the duplicate-entities row
- **Survives:** async jobs, `pull-kg`, extraction resume, structured classification

**New:** `projection synthesize` as the deliberate second step of a bulk load
(`ingest sync <dir>` → `projection synthesize`) · frontmatter id seeding on first
ingest · the git commit artmind makes per ingested version · `docs reindex`.

## `artmind-create-schema` (260 lines) — mostly additive

- **Step 3 (design entity classes)** — must now assign **`kind: recurrent |
  occurrent`** to every class. New required step, no default, and the validator
  fails without it.
- **"YAML Structure Reference"** (§204) — `entity_types` becomes a map.
- **Step 7 (temporal block)** — shrinks; `supersede_on_title_family` is gone.
- **Steps 4–6 (the three prompts)** — **depends on cluster 8.** If schemas become
  structured declarations with prompts assembled at runtime, these three steps stop
  being "write good prose" and become "declare classes, properties and relations".
  That would be the largest change to this skill, and it shouldn't be written twice.
- **Assets** — both sample schemas need the new `entity_types` map form.

**New:** the meta-schema contract · reserved `_`-prefixed names · guidance on
recurrent vs occurrent · the **name-is-identity** rule (no measurements or dates in
a recurrent class's names — the single biggest lever on projection quality).

---

## Sequencing note

`artmind-create-schema` should be written **after** cluster 8 is decided, and
`artmind-query`'s structural-schema section should be written **with** the test that
guards it — not before. The other three can be rewritten as soon as the code
lands.
