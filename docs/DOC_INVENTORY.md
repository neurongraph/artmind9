# Documentation inventory

A one-time scan (2026-08-29) of every `.md` file that documents the **artmind
codebase** — as opposed to the document corpus, agent-skill source, or vendored
packages. Built to drive a curation pass: rearrange, archive, and in a few cases
regenerate. See "Excluded from scope" at the bottom for what this deliberately
skips and why.

Classification is based on file content (status lines, cross-references) and git
history (`git log --follow`), not just directory location.

## 1. Live documents

Current architecture/design, getting-started, and reference material that should
stay accurate and maintained. Read these first when onboarding.

| File | Last touched | Notes |
|---|---|---|
| [README.md](../README.md) | 2026-07-22 | Main entry point. |
| [CONTEXT.md](../CONTEXT.md) | 2026-08-23 | Living glossary — artmind's domain model/vocabulary. |
| [CONTEXT-MAP.md](../CONTEXT-MAP.md) | 2026-08-23 | Index across the `artmind` and `artmind_canvas` contexts. |
| [artmind_canvas/CONTEXT.md](../artmind_canvas/CONTEXT.md) | 2026-08-16 | Canvas subproject's domain glossary. |
| [docs/INSTALL.md](INSTALL.md) | 2026-08-15 | Authoritative install/runtime reference (per CLAUDE.md). |
| [docs/document-identity.md](document-identity.md) | 2026-08-24 | Current identity/versioning mechanism. Post-redesign. |
| [docs/projection-pipeline.md](projection-pipeline.md) | 2026-08-25 | Current observation→projection mechanism. Post-redesign. |
| [docs/stores-and-repos.md](stores-and-repos.md) | 2026-08-24 | Current store/repo topology (code repo, vault, run folder, data dir, archive root). |
| [docs/CAPABILITIES.md](CAPABILITIES.md) | 2026-08-27 | Actively-maintained capability map / scoring checklist. |
| [docs/apqc-fibo-vs-banking-schemas.md](apqc-fibo-vs-banking-schemas.md) | 2026-07-06 | Standing rationale doc (APQC/FIBO vs. `banking_*` schemas); not tied to a build phase. |
| [docs/INCREMENTAL_INGESTION_v2.md](INCREMENTAL_INGESTION_v2.md) | 2026-07-22 | "Reflects the system as it runs today" — explicitly the current reader's guide. |
| [artmind_canvas/docs/ROADMAP.md](../artmind_canvas/docs/ROADMAP.md) | 2026-08-18 | Live roadmap for the canvas subproject. |
| [benchmarking/questions.md](../benchmarking/questions.md) | 2026-08-23 | Live gold-standard Q&A fixture, reused across runs. |
| [benchmarking/specs.md](../benchmarking/specs.md) | 2026-06-14 | Current benchmarking/evaluation framework spec. |

**Moved to archive (confirmed stale):**

| File | Status | Reason |
|---|---|---|
| [docs/archive/Query_specs.md](archive/Query_specs.md) | ✅ Archived 2026-08-29 | Pre-redesign (July 2026). Cypher pattern library for pre-observation/projection entity model. Patterns use incorrect node relationships (missing `:Observation` layer, wrong property names (`domain` vs `_domain`, `id` vs `_id`)) and would fail or return wrong results if executed. Marked with stale banner. |
| [docs/archive/refine-merge-conflict-supersede-guide.md](archive/refine-merge-conflict-supersede-guide.md) | ✅ Archived 2026-08-29 | Pre-redesign (July 2026). Documents merge/conflict/supersession machinery against the old `:Entity`-only model, before the observation/projection split (Phase 8, Aug 2026). Marked with stale banner. Source material for the `artmind-refine` skill but not current documentation. |

**Moved to GitHub issue (completed)** (per your bucket 1):

| File | Status line | Result |
|---|---|---|
| `docs/admin-ui-curation-workflow.md` (DELETED) | "**Status: backlog — not scheduled.**" | ✅ Moved to [Issue #19](https://github.com/neurongraph/artmind9/issues/19) — doc deleted, all content preserved in GitHub issue. |

## 2. Design history (already used to build — archived)

✅ **ARCHIVED** to `docs/archive/` — specs and plans that drove features now shipped
in the code. Preserved for audit trail, regression reference, and pattern learning.
See [docs/archive/README.md](archive/README.md) for browsing guide.

**All moved to archive, preserving directory structure:**

- `docs/archive/superpowers/plans/` (20 files): artmind-update, cli-hyphen-convention,
  poole-hierarchical-domains, pull-kg, session-graph-snapshot, artmind-wizard,
  cross-domain-conflicts-and-temporality, chat-ui-redesign,
  banking-corpus-qa-benchmark, staging-commit-model, admin-dashboard-layout,
  banking-corpus-extension, banking-temporal-metadata, global-temporal-defaults,
  incremental-supersession-hardening, supersession-integrity-fixes,
  structured-data-ingestion-plan, entity-supersession-history-zone,
  structured-semantic-classification-pipeline-plan, and one feature summary.

- `docs/archive/superpowers/specs/` (15 files): matching design specs for the above.

- `docs/archive/redesign/` (14 files): Phase 8 redesign process record — phase plan,
  change inventory, skills review, phase 1 migration review, 7 phase implementation
  notes, 2 runbooks (Phase 3 and Phase 8), quality scorecard (before/after baseline),
  and skills review.

- `docs/archive/cross_domain_conflicts/` (2 files): early Jul 2026 design drafts for
  cross-domain query and temporality; consolidated into superpowers pair above.

- `docs/archive/` root (4 files): historical individual specs/plans that predate
  superpowers structure or are explicitly superseded:
  - `plan.md` — Original May 2026 "Artmind Query CLI and Skill Plan" (superseded by
    CLI as documented in CLAUDE.md).
  - `admin-ui-plan.md` + `admin-ui-spec.md` — Built the current Lane A/B admin UI.
  - `INCREMENTAL_INGESTION.md` — Explicitly superseded by v2 (which cross-references
    this for "design rationale and history").

## 3. Other (not codebase documentation — reclassify or relocate)

| File(s) | Why it's not bucket 1 or 2 |
|---|---|
| `docs/corpus_project_status/*.md` (8 files: CORPUS_PLAN, PHASE_3_DOCS_SUMMARY, PHASE_3_DOCUMENT_LIST, PHASE_4_STATUS, PROJECT_COMPLETION_SUMMARY, SMARTSAVER_VERTICAL_SLICE_STATUS, corpus_background, planning_document) | Documents the creation of the synthetic "FirstUK Bank" **document corpus content itself**, not the artmind codebase — this is `document_corpus`-adjacent even though it physically sits under `docs/`. Recommend relocating next to the corpus data (`banking_document_corpus/` or `data/documents/`) rather than treating it as codebase docs. |
| [benchmarking/baseline-2026-08-23.md](../benchmarking/baseline-2026-08-23.md), [benchmarking/after-cutover.md](../benchmarking/after-cutover.md) | Benchmark **run results** (data snapshots from specific runs), not documentation of the code. Keep alongside `redesign-quality-scorecard.md` as the redesign's permanent before/after record. |
| [docs/CAPABILITIES-REVIEW-PROMPT.md](CAPABILITIES-REVIEW-PROMPT.md) | A reusable review *prompt*/process template for auditing `CAPABILITIES.md`, not documentation of the system itself. |
| `artmind_canvas/docs/adr/0001–0015` | Architecture Decision Records — already correctly homed, already historical-by-design. No action needed; distinct from "specs that built X" because an ADR log exists precisely to be a permanent trail. |

## Excluded from scope entirely

- `banking_document_corpus/**`, `data/documents/**` — the document corpus (per your instruction).
- `test/data/docs/personal_journal/**` — unit test fixtures.
- `.agents/skills/**`, `.claude/skills/**`, `.pi/skills/**`, `artmind/skills/**` — agent skill source (`artmind/skills/` is the source of truth per CLAUDE.md; the others are symlinks/seeded copies).
- `artmind/opencode/agent/*.md` — opencode persona source, same treatment as skills.
- `.venv/**`, `artmind_canvas/backend/.venv/**` — vendored third-party packages.

## Suggested next steps

✅ **COMPLETED:**
1. ✅ Filed [GitHub Issue #19](https://github.com/neurongraph/artmind9/issues/19) for `docs/admin-ui-curation-workflow.md`; doc deleted.
2. ✅ Created `docs/archive/`, moved 55 files from bucket 2 wholesale, preserving directory shape.
3. ✅ Verified `refine-merge-conflict-supersede-guide.md` is pre-redesign; archived + marked stale.
4. ✅ Verified `Query_specs.md` is significantly stale (missing `:Observation` layer, wrong property names); archived + marked stale.

**REMAINING:**
5. Move `docs/corpus_project_status/` out of `docs/` to sit next to the corpus data it describes (currently it's corpus-adjacent even though it sits in `docs/`).
6. *(Optional)* Regenerate Query_specs.md as a current reference (could be automated by reading actual `graph_query.py` patterns + latest schema).
7. *(Optional)* Update cross-references in live docs that previously pointed to moved files (most are dated filenames, so they'll be clear to fix if encountered).
