# Supersession Integrity Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three pre-existing integrity gaps found by grounding `docs/INCREMENTAL_INGESTION_v2.md` against the live `banking-corpus` graph: (1) LLM extraction can mint unaudited `SUPERSEDES` edges that bypass the audited supersession paths; (2) a supersession whose effective date fails to parse links the documents but never retires the old version, so stale content still surfaces in `--asOf` queries; (3) lifted `version` strings retain trailing annotations that break version-based notice resolution.

**Architecture:** All three are small, independent, self-asserted-truth / write-path fixes — no refine-pipeline involvement.
- Task 1 adds a reserved-relationship-type guard to the two extraction relationship writers (`ingest.py`, `update.py`) so system-managed edge types can only be created by their audited helpers.
- Task 2 scopes `parse_supersession_notice` to its own `## Supersession Notice` section (so it stops matching metadata-table rows) and guarantees every applied supersession carries an effective date by falling back to the newer document's `valid_from`.
- Task 3 normalizes the lifted `version` to its leading numeric token.
- Task 4 (data cleanup) is operator commands/cypher, not code.
- Task 5 refreshes `docs/INCREMENTAL_INGESTION_v2.md` as a clean human-readable guide (no mention of "fixes" — it documents the system as it then behaves).

**Tech Stack:** Python 3.14 / uv, Click CLI, Neo4j via `neo4j` driver, pytest with mocked Neo4j sessions (the suite runs hermetically — no live Neo4j, no network). Run tests with `just dev-test` (= `uv run --group dev pytest test/ -v`).

---

## Context you must know before starting

Read `CLAUDE.md` at the repo root first. Key facts used below:

- **Two relationship writers exist.** `artmind/ingest.py:_write_to_neo4j` (the extraction commit path) and `artmind/update.py` (the NL update path) both take an LLM-provided `rel_type`, normalize it to an uppercase label, and create the edge via `apoc.merge.relationship`. Neither restricts the type. `_sanitize_label` (`ingest.py:702`) does `re.sub(r"[^A-Za-z0-9_]", "_", s.strip()).upper()`; `_write_to_neo4j` does the equivalent inline at line ~928. So a `rel_type` like `"supersedes"` becomes the label `SUPERSEDES`.
- **`SUPERSEDES` is system-managed.** It is created only by `temporal.apply_supersession` (document scope: sets `scope`, `effective`, `detected_by`; retires the older doc + its chunks) and `temporal.apply_node_supersession` (entity/fact scope: sets `detected_by`, `at`, `status='superseded'`, etc.). It is read as lineage by `conflicts.py` and `text2cypher.py`. An edge minted by the extraction path has none of those audit properties.
- **Audited entity supersessions always carry `detected_by`.** `apply_node_supersession` always sets `s.detected_by`. So an Entity→Entity `SUPERSEDES` edge with `detected_by IS NULL` is provably not from the audited path — this is the safe cleanup predicate in Task 4.
- **`detect_supersession` route order** (`temporal.py`, bottom): for each document it tries the prose-notice route first, then the metadata-table route, then (schema-gated) the title-family route. The first route that resolves an `older` document wins for that pair; later routes skip an already-applied pair.
- **`parse_supersession_notice`** (`temporal.py`) currently scans the whole body when no `## Supersession Notice` section is present (`scope = section_match.group(1) if section_match else md_text`). That fallback is the root cause of Task 2 — it matches `| Supersedes | Version 2.1, [[doc]] |` metadata rows and returns a version with `effective=None` (the table-form `| Effective Date | … |` is not parseable by `_EFFECTIVE_RE`, which chokes on the `|`), pre-empting the metadata-table route that would have supplied the date.
- **`apply_supersession`** sets `older.valid_to = coalesce($effective, older.valid_to)` and only stamps the older document's chunks `if scope == "document" and effective:`. So `effective=None` ⇒ no `valid_to`, no chunk retirement ⇒ the old version stays live under `--asOf`.
- **`lift_document_dates`** (`temporal.py`) stores the raw `Version` header verbatim (`out["version"] = raw`). Version-based notice resolution (`_resolve_version_candidate` / `by_version_group`) keys on exact `str(version)`, so `"1.0 (Updated Monthly)"` never matches a notice citing `Version 1.0`.
- Tests mock Neo4j with small `FakeSession`/`FakeCtx` classes and `monkeypatch` — copy the style already in `test/test_supersession.py`, `test/test_temporal.py`, and `test/test_ingest_hooks.py`.
- The live `banking-corpus` graph currently contains: **one** spurious Entity→Entity `SUPERSEDES` edge (`SmartSaver Account Gross Interest Rate — 4.50% AER` → `SmartSaver Account Effective Rate After Fees — 4.20%`, all audit props null); **one** un-retired supersession (`sop_account_opening_v3.md` → `sop_account_opening.md`, `effective=None`, older `valid_to=None`, chunks not retired); and **one** annotated version (`interest_rate_schedule_2026.md`, `version="1.0 (Updated Monthly)"`). These are the concrete cases Task 4 cleans up.
- **Do not touch the run folder** (`~/.artmind`) or the live database from code — Task 4 is operator-run commands, surfaced in the final summary.

---

### Task 1: Reserve system-managed relationship types in the extraction writers

Extraction must not be able to create edge types that only the audited supersession helpers are allowed to create. Add a reserved-type guard to both relationship writers: when a normalized `rel_type` is reserved, skip creating the edge and log a warning naming the pair (so genuine intent surfaces for review rather than being silently reclassified).

**Files:**
- Modify: `artmind/ingest.py` (reserved set constant; guard in `_write_to_neo4j`'s relationship loop)
- Modify: `artmind/update.py` (same guard in its relationship loop)
- Test: `test/test_ingest_hooks.py` (or a new `test/test_reserved_relationships.py`)

- [ ] **Step 1: Write the failing tests.** Add tests that drive each relationship writer with a relationship whose `rel_type` normalizes to `SUPERSEDES` and assert no such edge is created:
  - For `_write_to_neo4j`: stage a minimal `doc_kg_dir` (document.json, chunks.json, entities.json, properties.json, relationships.json) where `relationships.json` contains one entity→entity rel with `"rel_type": "supersedes"`. Mock the Neo4j session (patch `GraphDatabase.driver`), capture every `session.run` cypher, and assert that **no** run creates a `SUPERSEDES` relationship (the reserved edge must be skipped) while a normal `rel_type` like `"relates_to"` in the same file **is** written. Assert a warning is logged.
  - For `update.py`: drive its relationship loop (follow the mocking style already in `test/test_update.py`) with an extracted relationship whose `rel_type` is `"supersedes"`; assert the reserved type is skipped and a warning logged.
  - Verify they FAIL first: `uv run --group dev pytest test/test_ingest_hooks.py -k reserved -v` (and the update test file).

- [ ] **Step 2: Implement.** In `artmind/ingest.py`, add a module-level constant near the other write helpers:

  ```python
  # Edge types created ONLY by their audited helpers (temporal.apply_supersession /
  # apply_node_supersession set scope/detected_by/effective and retire the older side;
  # PART_OF / EXTRACTED_FROM are structural). LLM-extracted relationships must never
  # mint these — an unaudited SUPERSEDES with null provenance corrupts lineage.
  RESERVED_REL_TYPES = frozenset({"SUPERSEDES", "PART_OF", "EXTRACTED_FROM"})
  ```

  In `_write_to_neo4j`'s relationship loop, right after `rel_type` is computed (line ~928), before the `EXTRACTED_FROM`/entity branches:

  ```python
  if rel_type in RESERVED_REL_TYPES and rel_type != "EXTRACTED_FROM":
      logger.warning(
          "Neo4j: refusing reserved relationship type {} from extraction "
          "({} -> {}); reserved for audited supersession helpers",
          rel_type, source_name, target_name or target_id,
      )
      continue
  ```

  (Note: the loop's own `EXTRACTED_FROM` branch is the legitimate structural writer for that type — keep it working. The intent is to block extraction-authored `SUPERSEDES`, and defensively `PART_OF`; adjust the condition so the loop's legitimate `EXTRACTED_FROM` path is unaffected. Read the actual loop and choose the cleanest placement.)

  In `artmind/update.py`, import/define the same reserved set (import `RESERVED_REL_TYPES` from `artmind.ingest`) and, after `rel_type = _sanitize_label(...)` (line ~327), add the same skip-and-warn guard before the `session.run`.

- [ ] **Step 3: Run the new tests, then the full suite.**
  ```bash
  uv run --group dev pytest test/ -v
  ```
  Expected: ALL PASS. If a pre-existing extraction/relationship test now fails because it relied on writing one of the reserved types from extraction, that test was encoding the bug — confirm against the graph semantics before adjusting it, and prefer fixing the fixture's `rel_type`.

- [ ] **Step 4: Commit.**
  ```bash
  git add artmind/ingest.py artmind/update.py test/
  git commit -m "fix(ingest): reserve system-managed relationship types from extraction

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
  ```

---

### Task 2: Scope the prose-notice parser and guarantee an effective date

A supersession that resolves an older document but no effective date links the two but never retires the old version. Two changes: (a) `parse_supersession_notice` only fires inside an actual `## Supersession Notice` section, so it stops matching `| Supersedes | Version X |` metadata rows and pre-empting the metadata-table route; (b) `detect_supersession` guarantees every applied pair carries an effective date, falling back to the newer document's `valid_from`.

**Files:**
- Modify: `artmind/temporal.py` (`parse_supersession_notice`, `detect_supersession`)
- Test: `test/test_supersession.py`

- [ ] **Step 1: Write the failing tests** in `test/test_supersession.py`:
  1. `test_prose_notice_ignores_metadata_supersedes_row`: a body with **no** `## Supersession Notice` section but a `| Supersedes | Version 2.1, [[old_doc]] |` row → `parse_supersession_notice(body)` returns `None` (not a version match).
  2. `test_prose_notice_still_parses_real_section`: a body **with** a `## Supersession Notice` section naming `Version 2.0 (effective 2026-01-15)` → still parses correctly (guards against over-narrowing).
  3. `test_metadata_table_route_supplies_effective_when_prose_absent`: end-to-end `detect_supersession` (mock `neo4j_session`, `_read_doc_body`, `apply_supersession`, `load_schema`) on a doc whose only declaration is the metadata `| Supersedes | Version 2.1, [[old]] |` + `| Effective Date | 2026-03-01 |` → the applied pair carries `effective="2026-03-01"` (metadata-table route wins, dated correctly).
  4. `test_supersession_effective_falls_back_to_newer_valid_from`: a resolvable pair whose parsed `effective` is `None`, where the newer doc row has `valid_from="2026-03-01"` → the applied pair's `effective` is `"2026-03-01"` and `apply_supersession` is called with that date (not `None`).

  Verify FAIL: `uv run --group dev pytest test/test_supersession.py -k "prose_notice or effective" -v`.

- [ ] **Step 2: Implement.**
  - In `parse_supersession_notice` (`temporal.py`), replace the whole-body fallback so the parser is section-scoped:
    ```python
    section_match = _NOTICE_SECTION_RE.search(md_text)
    if not section_match:
        return None
    scope = section_match.group(1)
    ```
    (Verify no other caller depends on the whole-body scan; the metadata-table route handles `| Supersedes | … |` rows.)
  - In `detect_supersession`, when a pair is resolved but `effective` is falsy, fall back to the **newer** document's `valid_from` before appending to `report["applied"]` / calling `apply_supersession`. The newer doc's row already carries `valid_from` (the docs query selects it). Apply this to the notice **and** metadata-table branches (the title-family route already uses `valid_from` as its effective). Keep it null-safe: if the newer doc also lacks `valid_from`, leave `effective` as-is (the pre-fix behavior) rather than inventing a date.

- [ ] **Step 3: Run supersession + temporal + full suite.**
  ```bash
  uv run --group dev pytest test/test_supersession.py test/test_temporal.py -v
  uv run --group dev pytest test/ -v
  ```
  Expected: ALL PASS. If a pre-existing test asserted the old whole-body prose behavior, confirm whether it encoded the bug before touching it.

- [ ] **Step 4: Commit.**
  ```bash
  git add artmind/temporal.py test/test_supersession.py
  git commit -m "fix(temporal): scope prose notice to its section and guarantee supersession effective date

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
  ```

---

### Task 3: Normalize the lifted document version

Strip trailing annotations from the lifted `version` so version-based notice resolution matches.

**Files:**
- Modify: `artmind/temporal.py` (`lift_document_dates`)
- Test: `test/test_temporal.py`

- [ ] **Step 1: Write the failing test** in `test/test_temporal.py`: `lift_document_dates` on a body with `| Version | 1.0 (Updated Monthly) |` (and a `valid_from` label) returns `version == "1.0"`. Add a companion assertion that a clean `| Version | 2.0 |` still yields `"2.0"`. Verify FAIL.

- [ ] **Step 2: Implement** in `lift_document_dates`, at the version branch (currently `out["version"] = raw`):
  ```python
  if canon == "version":
      m = re.match(r"\s*(\d+(?:\.\d+)*)", raw)
      out["version"] = m.group(1) if m else raw.strip()
  ```
  Keep the raw value only if no leading numeric token is present (don't lose a non-numeric version). `re` is already imported in `temporal.py`.

- [ ] **Step 3: Run temporal + full suite.**
  ```bash
  uv run --group dev pytest test/test_temporal.py -v
  uv run --group dev pytest test/ -v
  ```
  Expected: ALL PASS.

- [ ] **Step 4: Commit.**
  ```bash
  git add artmind/temporal.py test/test_temporal.py
  git commit -m "fix(temporal): normalize lifted document version to leading numeric token

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
  ```

---

### Task 4: Data cleanup on the live graph (operator-run, not code)

The three code fixes prevent recurrence but don't repair rows already in `banking-corpus`. This task is a set of commands for the operator to run against the live database with a warm/`ARTMIND_NO_PROXY=1` invocation. **Do not run these from the plan implementation** — surface them in the final summary for the operator to run and review.

- [ ] **Step 1: Remove unaudited entity supersession edges** (Issue #1 residue). Safe predicate — audited edges always carry `detected_by`:
  ```cypher
  MATCH (:Entity)-[s:SUPERSEDES]->(:Entity) WHERE s.detected_by IS NULL DELETE s
  ```
  Suggest running a `MATCH ... RETURN` first to review the rows, then `DELETE`.

- [ ] **Step 2: Re-retire the un-dated supersession** (Issue #2 residue). After Task 2 is deployed (`artmind init` / restart daemon so the new code is live), re-run the idempotent scan for the affected domain so `apply_supersession` now stamps `valid_to` on the old document and its chunks:
  ```bash
  ARTMIND_NO_PROXY=1 artmind ingest detect-supersession --domain banking.sop_guides
  ```
  Then verify `sop_account_opening.md` now has a non-null `valid_to` and its chunks are retired.

- [ ] **Step 3: Re-normalize annotated versions** (Issue #3 residue). Either re-run the temporal normalization for the affected domain, or a one-off cypher for the single known row:
  ```cypher
  MATCH (d:Document {name:'interest_rate_schedule_2026.md'})
  SET d.version = '1.0'
  ```

- [ ] **Step 4:** Include all three commands verbatim in the final operator summary, with the note that they require a live Neo4j and the deployed (post-fix) code.

---

### Task 5: Refresh `docs/INCREMENTAL_INGESTION_v2.md`

Keep the human-readable guide accurate after the fixes. **Do NOT describe these as "fixes" or reference bugs** — v2 is a clean guide to how the system behaves; simply update it so its statements remain true, using live-graph examples where helpful.

**Files:**
- Modify: `docs/INCREMENTAL_INGESTION_v2.md`

- [ ] **Step 1:** In the supersession section, add a short note that the `SUPERSEDES` edge is system-managed — created only by the document/fact supersession helpers, never by entity extraction — so lineage edges always carry provenance (`detected_by`, `scope`, `effective`). Frame it as a property of the design, not a change.

- [ ] **Step 2:** In the supersession / as-of sections, state that an applied document supersession always carries an effective date (the superseding version's `valid_from` when a declaration omits an explicit one), so the older version and its chunks are always retired and drop out of `--asOf` queries. Keep the existing worked example; verify its dates still match the live graph after Task 4's cleanup.

- [ ] **Step 3:** Where the guide mentions version-based notice resolution, note that lifted versions are normalized to their numeric token (e.g. a `Version 1.0 (Updated Monthly)` header resolves as `1.0`).

- [ ] **Step 4:** Re-ground any example that Task 4 changed (e.g. the `sop_account_opening` retirement, the `interest_rate_schedule_2026.md` version) by re-querying the live graph, so every example in the doc is still literally true. Run no test suite here (doc-only), but proofread for consistency with the code sections above.

- [ ] **Step 5: Commit.**
  ```bash
  git add docs/INCREMENTAL_INGESTION_v2.md
  git commit -m "docs: refresh incremental-ingestion v2 guide

  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
  ```

---

## Verification checklist (end of plan)

- `uv run --group dev pytest test/ -v` — full suite green.
- Extraction can no longer create a `SUPERSEDES` (or other reserved) edge — new Task 1 tests prove it for both writers.
- A metadata-table-only supersession applies with the correct effective date, and any applied pair lacking a parsed date falls back to the newer doc's `valid_from` — new Task 2 tests prove it.
- `lift_document_dates` normalizes `1.0 (Updated Monthly)` → `1.0` — new Task 3 test proves it.
- Operator has the three Task 4 cleanup commands (require live Neo4j + deployed code).
- `docs/INCREMENTAL_INGESTION_v2.md` reads as a clean guide, every example still true against the live graph, no mention of "fixes".

## Deployment caveats to report to the operator

1. Code edits are live immediately under the editable install, but a running `serve` daemon serves the old build — restart it (`just dev-stop-daemons`) before the Task 4 re-scan so `detect_supersession` runs the new code.
2. Run the Task 4 cleanup commands against the live graph yourself (they need Neo4j and are outside the hermetic test suite); review each `MATCH`/`RETURN` before the `DELETE`/`SET`.
