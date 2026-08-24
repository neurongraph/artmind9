# Phase 2 implementation notes

What actually landed for Phase 2 (identity and versioning), against the spec
in [document-identity.md](./document-identity.md). Read that first — this is
implementation scope and decisions, not the design.

## What changed

- **`artmind/document_identity.py`** (new) — the six-row resolution table
  (`resolve_identity`), body-only versioning (`decide_version`), the
  frontmatter contract (`build_frontmatter`/`serialize_frontmatter`), and the
  one `markdown_path_for` resolver.
- **`artmind/vault_git.py`** (new) — commit-per-frontmatter-change
  (`commit_paths`, no-op when nothing actually changed) and opt-in push
  (`maybe_push`, gated on `ARTMIND_VAULT_GIT_PUSH=1`, never fatal).
- **`artmind/db.py`** — `documents` table rebuilt around `artmind_id`
  (nullable, unique) instead of `logical_id`; no migration path, per your
  call that re-ingesting is fine — an old-shaped table is dropped and
  recreated empty.
- **`artmind/ingest.py`** — `ingest_file` now dispatches: vault-native
  markdown (`.md` inside `ARTMIND_VAULT_DIR`) goes through the full new
  mechanism in `_ingest_vault_native` (no copy into `originals/`/`markdowns/`,
  resolution table, frontmatter write); everything else (binary sources, or
  an ad-hoc `.md` outside the vault) goes through `_ingest_binary_or_adhoc`,
  which is the pre-Phase-2 path with `--force`/`--replace` removed. `_register_document`
  now stores `canonical_path()` (vault-relative when applicable) instead of a
  raw `.resolve()` — the two representations must match exactly, since
  `resolve_identity`'s lookups use the same function (see "Bug found" below).
- **`ingest_to_kg`/`commit_to_graph`** — the `replace` parameter is gone;
  re-ingesting a known identity is always a replace now, and the retraction
  step is a safe no-op for a genuinely new document (every MATCH finds
  nothing).
- **CLI** (`ingest sync`) — `--replace` deleted; `--force` narrowed to
  structured files only (it always meant something different there — a
  separate parameter on `ingest_structured_file`, never wired to KG identity);
  new `--setDomain` (forces domain + re-extraction), `--fork`/`--adopt`
  (resolve an `_artmind_id` collision instead of refusing). `--domain` is a
  fallback now — a vault-native file's own `_domain` wins. A directory batch
  no longer prompts for domain; a single file with neither frontmatter domain
  nor `--domain` still does.

## Doc_id simplification

The graph's `Document.id` **is** `_artmind_id` for a vault-native document —
one identity, not two parallel ones (a random physical id plus a separate
stable logical id). This falls out naturally once identity is assigned once
and never re-derived: there's no reason left to mint a second id. Binary
sources keep the old two-tier scheme (`_resolve_doc_identity` against
`Document.logical_id` in Neo4j) unchanged — see scope note below.

## Scope boundary: binary and tabular sources are untouched

`docs/document-identity.md` covers all three source types, but the phase
plan's Phase 2 bullets are markdown-identity-specific, and Phase 5 is where
`_derived/` promotion (the mechanism that would let a binary source's
*derived* markdown carry `_artmind_id`) actually lands. So for now:

- Binary sources (pdf/pptx/docx) still copy into `documents/originals/` and
  `documents/markdowns/`, and still resolve identity via the pre-Phase-2
  `_logical_id`/`_resolve_doc_identity` path (a Neo4j lookup, not a registry
  lookup) — unaffected, not regressed, just not upgraded yet.
- Tabular sources (csv/xlsx) are already path-only per the spec itself
  ("accepted limitation") — no change needed.

This means the six-row resolution table, `_artmind_id`, and the frontmatter
contract apply **only** to vault-native markdown today. Extending it to
binaries is Phase 5's job, not a gap in this implementation.

## A real bug the live exit gate caught

First live run against the actual vault + Neo4j failed on the second ingest:
a body edit's re-ingest raised `IdentityConflict` — a false "refuse" on a
document that had only ever existed at one path. Root cause: `_register_document`
stored `str(file_path.resolve())` (always absolute) while `resolve_identity`'s
registry lookups keyed on `canonical_path()` (vault-relative when the file is
inside the vault). Same file, two different string representations in two
places that must agree exactly for the resolution table to work. Fixed by
routing both through `canonical_path()`. Added `test/test_ingest_vault_native.py`
specifically to catch this class of bug — an integration test against a real
temp vault + registry, not just isolated unit tests of either side.

## Exit gate — run live against the real vault schema + real Neo4j

Same sequence the phase plan specifies, using a throwaway domain/vault
(cleaned up after):

1. First ingest → `new`, version 1, full system frontmatter block written.
2. Edit body, re-ingest → `reingest`, version 2, `_artmind_id` unchanged,
   prior version's graph contributions retracted before the rewrite.
3. Touch only frontmatter (tags) → `reingest`, `metadata_only` tier, version
   **stays** at 2.
4. `git mv` → `move`, identity survives, version stays at 2, no re-extraction.

All four passed. `just dev-test` is green (1109 tests) throughout.

## Deferred, on purpose

- Derived-markdown promotion, binary-source `_artmind_id` (Phase 5).
- `docs reindex` — nothing to rebuild *from* yet without Phase 5's promotion.
- Async job (`ingest async`) domain/fork/adopt flags — worker.py's per-file
  commit is unbatched (one commit per file, not one per job); `ingest sync`'s
  directory batching got the "one bulk commit" treatment, async did not, for
  time. A reasonable fast-follow, not a correctness gap.
- No Neo4j index added for `artmind_id` specifically — `Document.id` already
  carries a unique constraint and *is* the `artmind_id` for vault-native
  documents, so lookups by `.id` are already indexed.
