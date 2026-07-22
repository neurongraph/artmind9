# How artmind handles incremental updates

A guide to what happens when you re-ingest a document that has changed — how the
new version is timestamped and versioned, how it supersedes the old one, and how
queries return "current truth" without any manual cleanup step.

This is a reader's guide, not a change log. Everything below reflects the system
as it runs today, grounded in the `banking-corpus` graph. If you want the design
rationale and the history of how these behaviours were hardened, see
[`INCREMENTAL_INGESTION.md`](INCREMENTAL_INGESTION.md).

---

## 1. The mental model

artmind is **accretive and self-describing**:

- **Accretive** — nothing is ever destructively overwritten at ingest. Every
  graph write is a `MERGE`/upsert. A re-ingested document becomes a *new*
  `Document` node beside the old one; history is preserved, not erased.
- **Self-describing** — a document declares its own truth (its effective date,
  its version, which document it replaces) in its header and body. At commit
  time, two deterministic per-document hooks read those declarations and stamp
  them onto the graph. No LLM judgement, no cross-document reasoning.

Two ideas follow from this and are worth holding onto:

| Concept | What it means |
|---|---|
| **Retire, don't delete** | A superseded document isn't removed. It gets a `valid_to` date. Queries filter it out *as of* a point in time; the history stays queryable. |
| **Per-document at ingest, cross-document at refine** | Ingest hooks only act on the committing document's own declarations. Anything that requires comparing *multiple* documents — de-duplicating entities, detecting genuine conflicts, consolidating descriptions — is deliberately deferred to the separate `refine` pipeline. |

So the one-line answer to "what happens when I update a document?" is: **you get a
second version, linked to the first by a `SUPERSEDES` edge, with the old version
dated closed — and current-truth queries stop seeing the old one automatically.**

---

## 2. A worked example

The `banking-corpus` graph contains a real three-version chain in the
`banking.reference` domain — a monthly interest-rate schedule:

```
interest_rate_schedule_2026.md      valid_from 2026-01-15  →  valid_to 2026-02-01
interest_rate_schedule_2026_02.md   valid_from 2026-02-01  →  valid_to 2026-03-01
interest_rate_schedule_2026_03.md   valid_from 2026-03-01  →  valid_to (none — current)
```

Each newer file carries a metadata header naming the one it replaces. For
`interest_rate_schedule_2026_02.md`:

```markdown
| Version        | 1.0                                              |
| Effective Date | 2026-02-01                                       |
| Status         | Superseded                                       |
| Supersedes     | [[interest_rate_schedule_2026]]                  |
| Superseded By  | [[interest_rate_schedule_2026_03]] from 2026-03-01 |
```

When `interest_rate_schedule_2026_02.md` was ingested, the commit hooks:

1. lifted `valid_from = 2026-02-01` and `version = 1.0` onto its `Document` node;
2. read the `| Supersedes |` row, resolved `[[interest_rate_schedule_2026]]` to
   the older `Document`, and created:

```
(interest_rate_schedule_2026_02) -[:SUPERSEDES {scope:'document',
                                                 effective:'2026-02-01',
                                                 detected_by:'notice'}]->
(interest_rate_schedule_2026)
```

3. stamped `valid_to = 2026-02-01` and `superseded_by = <newer id>` on the older
   `Document` **and on all 22 of its chunks**.

That last point is what makes querying work with zero cleanup — see §6.

---

## 3. Document identity: what counts as "the same document"

Two independent identity checks run at the start of ingestion
(`ingest_file`, `artmind/ingest.py`):

**Content identity (SHA-256).** The file's hash is checked against the registry.
- *Byte-identical* content is rejected as a duplicate (nothing to do).
- *Any edit* changes the hash, so the file is accepted as a new document.
- `--force` overrides the duplicate block by minting a synthetic extraction key,
  so even identical content gets its own independent `Document` node.

**Name identity (filename).** If the filename collides (case-insensitively) with
one already registered, the incoming copy is stored under a timestamped name
before it is registered:

```
policy_fees.md   →   policy_fees_20260722_153000.md
```

Both versions now coexist in the registry, on disk, and in the graph. The
timestamp-rename suffix is understood by the versioning machinery — the
"title family" logic in §5 strips it back off so the two files are recognised
as siblings.

> **Not the update path:** `docs clean` *deletes* a document, its chunks, and any
> orphaned entities. That erases history rather than versioning it — reserve it
> for genuine removal, never for "replace with a newer version."

---

## 4. How a re-ingest is written to the graph

The graph write (`_write_to_neo4j`) is uniformly upsert-based:

- **`Document` and `DocChunk`** are `MERGE`d by id — a re-ingest mints fresh
  nodes for the new version. The old version's nodes are untouched.
- **Entities upsert by `(name, entity_class, domain)`** — so an entity mentioned
  in *both* versions lands on the **same** node. Its properties are merged
  accretively (`_upsert_entity` → `_merge_prop_value`):
  - lists **union**,
  - strings **append** as `"old | new"`,
  - numbers and booleans **keep the existing value**.
- **Provenance survives** regardless: each version's chunks keep their
  `EXTRACTED_FROM` edges, and relationships carry `chunk_id`/`doc_id` stamps, so
  you can always trace which version asserted what.

This accretive entity merge is correct for *peer* documents that each contribute
a piece of the truth. It is the wrong default once one document is known to
*replace* another — which is exactly what §7 fixes.

---

## 5. Supersession: the three ways a link gets made

`SUPERSEDES` is a **system-managed** edge type — the only two writers allowed to
create it are the document-scope and fact-scope helpers described below
(`apply_supersession` / `apply_node_supersession`), which always stamp full
provenance (`scope`, `effective`, `detected_by`, or `at`/`status`). The generic
entity-relationship writer that turns LLM-extracted relations into graph edges
explicitly refuses to create a `SUPERSEDES` edge, so an entity relationship the
extractor merely *describes* as "superseding" (e.g. one rate figure being
described relative to another) can never masquerade as real document/fact
lineage. Every `SUPERSEDES` edge in the graph is therefore audit-traceable to one
of the three link routes below.

A `SUPERSEDES` edge can be created three ways. All are idempotent and additive —
re-running never double-links or corrupts existing edges. The
`detected_by` property on the edge records which route made it. Whichever route
resolves a pair, the applied edge always carries an effective date: if the
declaration itself doesn't parse one, the newer document's own `valid_from` is
used instead — a link is never made without a date, since a dateless link
would never actually retire the older version (see §6).

### Route 1 — Prose "Supersession Notice" (automatic, at commit)

A `## Supersession Notice` section naming the superseded **Version**. Real
example from `policy_complaints_v3.md`:

```markdown
## Supersession Notice

**This policy (Version 3.0, effective 2026-06-01) supersedes and replaces
Version 2.0 (effective 2026-01-15) in full.** ...
```

The older version is resolved by matching that version number *within the same
title family*, so a boilerplate `Version 1.0` on an unrelated document can never
mislink. A lifted `Version` header is normalized to its leading numeric token
(e.g. `1.0 (Updated Monthly)` resolves as `1.0`) so an annotated version string
still matches a bare version cited in a notice. Ambiguity is **skipped, never
guessed**, and this route only ever looks inside an actual `## Supersession
Notice` section — text elsewhere in the document (including a metadata-table
row, Route 2's territory) is never mistaken for a prose declaration.

### Route 2 — Metadata-table row (automatic, at commit)

A `| Supersedes | [[doc_name]] |` row (plus `| Effective Date |`), naming the
older document directly by name. This is what the interest-rate chain in §2 uses.
Resolved by document name rather than version — handy for document families where
every file is nominally "Version 1.0", or where the "Supersedes" row cites a
version rather than a name (`sop_account_opening_v3.md` does exactly this: its
only declaration is `| Supersedes | Version 2.1, [[sop_account_opening]] |` — no
prose section at all — so Route 2 resolves it by name and supplies the
`| Effective Date | 2026-03-01 |` from the same table).

### Route 3 — Inferred title-family chain (automatic, opt-in per domain)

Some document families are *versioned* (a newer sibling replaces the older); some
are a *series* (monthly meeting notes don't supersede each other). Only the schema
author knows which. So this route is **off by default** and enabled per domain:

```yaml
temporal:
  defaults:
    supersede_on_title_family: true   # e.g. banking.policy_schema.yaml
```

When enabled, documents sharing a title family (`_title_stem` reduces
`interest_rate_schedule_2026`, `_2026_02`, `_2026_03` — and `policy_complaints`,
`_v2`, `_v3` — to one family stem) are ordered by `valid_from` and chained:
each newer document supersedes its immediate predecessor, with the edge marked
`detected_by:'title_family'`. This closes the "someone re-ingested an edited file
but forgot the notice" gap. Guardrails: only documents with a real `valid_from`
participate, ties on `valid_from` are skipped, and an **explicit notice always
wins** — if Route 1 or 2 already linked a pair, Route 3 leaves it alone.

### Route 4 — Manual and fact-level (on demand)

- **Manual, document scope:** when a notice was missing and the schema flag is
  off, an operator can link two documents directly:
  ```bash
  artmind ingest supersede --domain banking.reference NEWER OLDER --effective 2026-03-01
  ```
- **Fact scope (node-level):** `apply_node_supersession` (used by the
  `artmind update` natural-language flow) retires a single superseded *Entity*
  node — for facts like "the branch manager changed" where the old fact lives on
  its own node rather than in a distinct document. It sets `valid_to`,
  `status='superseded'`, and `superseded_by` on the old node without touching the
  document layer.

A whole-domain rescan (`artmind ingest detect-supersession --domain D`, also
refine step 2) applies the same logic in bulk and is safe to re-run at any time.

---

## 6. Querying current truth: the as-of filter

Retirement works because of one NULL-safe predicate applied at query time
(`asof_predicate`, `artmind/graph_query.py`):

```
$asOf IS NULL
OR ( (valid_from IS NULL OR valid_from <= $asOf)
     AND (valid_to  IS NULL OR valid_to  >  $asOf) )
```

Untimed nodes are always visible; timed nodes are visible only in their window.
This is wired through the graph patterns, metadata/timeline/entity listings,
**and** vector + full-text search — so superseded chunks drop out of semantic
search too, not just structured queries.

Watching it work on the real interest-rate chain:

| Query `--asOf` | Document returned |
|---|---|
| `2026-02-15` | `interest_rate_schedule_2026_02.md` (only) |
| `today` (2026-07-22) | `interest_rate_schedule_2026_03.md` (only) |
| *omitted* | **all three** versions |

This holds even for a Route 2 (metadata-table) link that names its predecessor by
version rather than filename, with no prose section at all —
`sop_account_opening_v3.md`'s only declaration is
`| Supersedes | Version 2.1, [[sop_account_opening]] |` plus
`| Effective Date | 2026-03-01 |`. Once linked, the older `sop_account_opening.md`
(and all 37 of its chunks) carry `valid_to: 2026-03-01`, so `--asOf today` returns
only `sop_account_opening_v3.md`.

The `--asOf` value accepts `today`/`now`/ISO dates at year, month, or day
precision (values are ISO strings compared lexically, so `2026-01` works as a
prefix). Anything unparseable raises rather than silently hiding content.

> **Default to `--asOf today`.** Without it, *no* temporal filter is emitted and
> superseded content resurfaces. The `artmind-query` skill now treats
> `--asOf today` as the standard retrieval posture, dropping it only for
> explicitly historical questions ("what did the policy say in January",
> "previous version"). Omit it — or pass a past date — to query history on
> purpose.

For lineage itself, `artmind query graph timeline --entityId <id>` reconstructs
an entity's dated relationships chronologically.

---

## 7. Version-aware entity properties

Recall from §4 that entities merge accretively across versions. Consider a fee
that changed from 5.0 to 6.0 between versions:

- **Without intervention:** the number rule keeps the *old* 5.0, and a string
  field like `effective_date` accretes to `"2026-01-15 | 2026-06-01"`. That's
  wrong once the new document supersedes the old.

- **What actually happens:** when a commit applies a `SUPERSEDES` edge for the
  document, `_reassert_superseding_properties` runs immediately after. It takes
  the superseding document's *own* extracted property values and writes them over
  the merged entity nodes (matched by the same `(name, entity_class, domain)`
  key). Updated scalars win; date strings stay clean. The fee reads 6.0, and
  `effective_date` is the new date — not a concatenation.

This applies **only** to the superseding document's domain properties. Two
important boundaries:

- **Peer documents still accrete** (by design). If two documents genuinely
  co-describe an entity and neither supersedes the other, their values still
  merge — surfacing genuine disagreement is the job of refine's conflict
  detection, not ingest. You can see peer accretion in the corpus today: the
  `SmartSaver Account` product entity carries a `communication_template` value of
  `"tpl_001 | Rate Change Notification | SmartSaver Account Description | ..."` —
  several peer communications each contributed a template name.
- **Identity fields** (`name`, `description`, `aliases`, `context`) keep their
  accretive behaviour — reconciling those is consolidation's job at refine.

---

## 8. Timestamp & version reference

Everything the pipeline stamps, and who sets it:

| Property | Node | Set by | Meaning |
|---|---|---|---|
| `added_at` | registry row | `_register_document` | wall-clock ingestion time |
| `last_modified` | `Document` | `extract_kg` | file mtime of the original (UTC ISO) |
| `date`, `author` | `Document` | `extract_kg` | markdown frontmatter, if present |
| `ingested_at` | `Document` | temporal hook (first commit wins, via `coalesce`) | first commit time |
| `valid_from`, `version`, `time_source` | `Document` | temporal hook | lifted from header labels (`\| Effective Date \|`, `\| Version \|`) per schema `temporal.document` mapping; frontmatter fallback; optional `valid_from = ingestion_date` default |
| `valid_to`, `superseded_by` | `Document` | supersession | effective date of the superseding version + its id |
| `valid_to` | `DocChunk` | supersession (document scope) | drives as-of exclusion of stale text |
| `valid_from` / `valid_to` / `event_at`, `time_source` | `Entity` | temporal hook, per schema `temporal.entities` mapping | deterministic parse of a schema-declared date property; lands at commit time |
| `scope`, `effective`, `detected_by` | `SUPERSEDES` rel | `apply_supersession` | audit trail: how the link was made (`notice` / `title_family` / `manual`) |
| `at`, `reason`, `source_chat_id`, `status:'superseded'` | Entity (node scope) | `apply_node_supersession` | fact-level supersession from the NL update flow |

All valid-time values are ISO strings compared lexically, so year/month prefixes
work in `--asOf`.

Entity temporal fields land at commit time from the schema mapping. In
`banking.policy`, for instance, the schema maps a `POLICY` entity's
`effective_date` property to `valid_from`, and regulatory references pick up dates
like `Terrorism Act 2000 → valid_from 2000-07-20` (`time_source:'property'`)
directly at ingest, with no refine pass required.

---

## 9. How the schema drives it

The per-domain schema's `temporal:` block controls all of the above. The
`banking.policy` schema is a good exemplar:

```yaml
temporal:
  document:
    valid_from: [Effective Date, effective_date]   # header labels to read
    version:    [Version]
  entities:
    POLICY:                { valid_from: effective_date }   # entity property → valid_from
    REGULATORY_REFERENCE:  { valid_from: effective_date }
  relative_anchor: document.valid_from
  defaults:
    valid_from: ingestion_date        # fallback when no header date is present
    time_source: default_ingestion
    supersede_on_title_family: true   # opt in to Route 3 inference (§5)
```

A dotted child domain (e.g. `banking.reference`) inherits and can override its
parent's block; only the `defaults`, `relative_anchor`, `document`, and
`entities` keys are merged, which is why the `supersede_on_title_family` flag
lives under `defaults`.

---

## 10. Operator workflow for an update

1. **Author the new version** with either a `## Supersession Notice` section
   (naming the superseded Version) or `| Supersedes | [[old_doc_stem]] |` +
   `| Effective Date | … |` metadata rows. *Or*, if the domain has
   `supersede_on_title_family: true`, just give the new file a same-family name
   and a newer effective date — the chain is inferred.
2. **Ingest:** `artmind ingest sync <file> --domain D` (or `async`). The commit
   hooks link and retire the old version automatically.
3. **If a link is missing** (no notice, flag off): `artmind ingest supersede
   --domain D NEWER OLDER --effective DATE`.
4. **Query:** `--asOf today` for current truth; omit `--asOf` for full history;
   `query graph timeline` for lineage.
5. **Periodically run refine** for the cross-document judgement layer (duplicate
   merging, conflict detection, description consolidation). Updates don't
   *require* it — entity-level hygiene is simply deferred there by design.

---

## 11. What ingest does *not* do (by design)

The commit hooks are strictly per-document and self-asserted. These belong to the
refine pipeline, not to ingestion:

- **De-duplicating entities** that different documents named slightly differently.
- **Detecting genuine conflicts** between documents (a superseded claim is
  labelled `superseded` — history — rather than `conflicting_claims`).
- **Consolidating descriptions/aliases** across contributors.
- **Cross-domain comparison** — only the refine conflicts pass, and only when
  invoked with two or more domains, ever reasons across domain boundaries.

Keeping these out of ingest is what makes a commit fast, deterministic, and
free of surprise LLM judgement — while still landing correct document- and
chunk-level current truth the moment a document is committed.
