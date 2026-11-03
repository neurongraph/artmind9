# Knowledge catalogue and answer assets (P1) — implementation plan

Spec: `docs/superpowers/specs/2026-11-02-catalogue-and-answer-assets-design.md`.
Conventions: `docs/superpowers/specs/2026-11-02-cli-uniformity-design.md` (`artmind/cli_conventions.py`).
Branch `feat/catalogue-assets`, based on `origin/feat/cli-uniformity`. Order follows spec §11.

Rules for every task: test first; `env -u ANTHROPIC_BASE_URL uv run --group dev pytest test/ -q -m "not slow_docling"`
green before each commit; one commit per task, trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>`;
synthetic test text only; Cypher and parameters asserted through `test/_neo4j_recorder.py`; tests keep
`ARTMIND_PLAN_CATALOG=0` (conftest) unless a test opts in; catalogue caches, asset records and health
samples live only under the gitignored `logs/`. No merge into master.

## Step 1 — generation (spec §3)

**Task 1. `artmind/catalogue/state.py` and the bump at every choke point.**
- New package `artmind/catalogue/` with `state.py`: `BUMP_CYPHER` (the one statement from spec §3),
  `bump(tx, domains)` (normalises, drops empties, dedups, one `tx.run`; no call for an empty list),
  `generations(session, domains)` (one indexed read for any number of domains; a domain with no node is 0).
  A parent domain is never bumped itself: callers pass the leaf domains they wrote; readers of a parent sum
  its children (`generations` expands by `domain_predicate`-style prefix).
- Call sites, one call each, inside the existing transaction:
  - `ingest._commit_document_tx` (domain from `staged["domain"]`; covers table2graph, update confirm,
    write-to-graph, pull-kg, vault-sync replay, archive restore);
  - `lifecycle._transition` (domain read in the same tx from the document node; `rebuild=False` callers
    included, so a commit that re-retires bumps once more, harmless);
  - `projection.rebuild`: once per call for the domains of its effective + folded keys (callers batch, so
    "once per domain per rebuild" holds per batch of one rebuild transaction; a full batched rebuild bumps once
    per batch transaction — recorded as a deviation if the test cannot prove once-per-rebuild);
  - `synthesize.synthesize_key._write` (the synthesis write);
  - `conflicts.resolve_conflict` (both the record path via `conflict_records.apply` and the graph-only path:
    this one runs in an implicit auto-commit session, so it moves into one `execute_write`).
  - Same-as approve/reject go through the rebuild they trigger; no extra call.
- `setup._setup_neo4j`: `CREATE CONSTRAINT domain_state_domain IF NOT EXISTS FOR (n:DomainState) REQUIRE n._domain IS UNIQUE`;
  the setup report's constraint list gains it.
- Tests: `test/test_catalogue_state.py` (statement and params; empty/dup/parent handling; `generations`
  query) and `test/test_catalogue_bump_sites.py` — one test per choke point, driving the real function over
  a `WritableRecorder` and asserting the bump statement appears among the transaction's calls with the right
  `domains`; a source-level guard that fails if a listed function stops mentioning `catalogue_state`/`bump`.
  Setup test for the constraint.

**Task 2. Disk stamps and the new fingerprint.**
- `artmind/catalogue/stamp.py`: `stamp(path_or_dir)` → `(file_count, newest_mtime_ns, sha1 of sorted relative names)`;
  `disk_stamps(domain)` → dict for `views`, `topics`, `insights`, `schemas`.
- The plan catalogue fingerprint becomes `(generation, entities, documents, history, disk stamp)`;
  `last_rebuilt_at` leaves it; `MAX_AGE_S` 900 → 3600. `CATALOGUE_FORMAT` → 3 in Task 3 (same commit as the move).
- Tests: stamp changes on add, edit, rename, delete and not on an untouched tree; fingerprint query asserts
  `DomainState` is read and `ProjectionState` is not; existing catalogue tests updated.

## Step 2 — the catalogue package (spec §4)

**Task 3. Move and split into parts.**
- `git mv artmind/query_plan/catalogue.py` into `artmind/catalogue/` as `model.py` (dataclasses, `to_json`/`from_json`,
  `CATALOGUE_FORMAT = 3`), `parts.py` (one pure builder per part: `classes`, `relations`, `documents`, `names`,
  `stores`, `assets`, `time`, `health`), `cache.py` (memory + disk, single flight, per part, per-part
  fingerprints: generation parts vs disk-stamp parts vs `health` TTL).
- `artmind/query_plan/catalogue.py` stays as a re-export (`get`, `build`, `fingerprints`, `reset`, `set_clock`,
  `DomainCatalogue`, `NameEntry`, `NAME_INDEX_MAX`, `MAX_AGE_S`, `_class_descriptions`, ...) so old imports and
  monkeypatches (`catalogue._class_descriptions`) keep working; planner/candidates keep reading the same attributes
  (`classes`, `relations`, `documents` names, `names`, `truncated`).
- `documents` part gains per-document records (`id`, `_domain`, `valid_from`, `has_summary`), one query, while the
  name list the planner reads stays identical and sorted.
- `stores`: `graph`/`chunks`/`structured` from `schema_validate.domain_graph_extraction` and the structured registry.
- `time`: one aggregate query (content-date range, share with `valid_from`).
- Tests: each builder from recorded rows + temp files; cache source memory/disk/live; single flight; format 2 → 3
  discards old disk files; a generation change alone invalidates `classes`/`names`; a stamp change alone
  invalidates `assets`; `ARTMIND_PLAN_CATALOG=0` builds live; parent domain union; existing planner tests unchanged.

**Task 4. `health` part and background refresh.**
- Builders for `twins`, `same_as_open`, `conflicts_open`, `summaries_missing`, `unembedded`, `empty_extractions`,
  `behind_vault`, `drift`: counts plus ≤ 10 samples, reusing the existing readers (`sameas` store, conflicts query,
  `projection status`, `staging_audit`, `vault status` graph block). TTL `ARTMIND_CATALOGUE_HEALTH_TTL_S` (default 3600).
- `get` never builds `health` inline: it returns the cached part or `None`.
- `catalogue/refresh.py`: `schedule_health(domains)` — debounced (≤ 1 per domain per 5 min), daemon thread, only after
  `enable_background()` has been called; `serve`, `chat-ui` and `admin-ui` call `enable_background()` at start, one-shot
  CLI never does. Parent: sum with child labels on samples.
- Tests: samples capped at 10; no background thread in a plain call; debounce with a fake clock; `health` follows
  its TTL only; the server entry points enable it (static check on the three start functions).

**Task 5. `query catalogue` and `catalogue refresh`.**
- `artmind query catalogue [--domain D …] [--part P …] [--names] [--compact]` (read-only, proxied: `_entry` already
  proxies every `query` call; the `serve` route is checked); output per domain, per part: data + `source` + `generation`.
- `artmind catalogue refresh [--domain D …] [--part P …] [--dryRun] [--compact]` — new group `catalogue`; writes caches
  only; `--dryRun` returns which parts are stale and why with an `effect` (no graph write, no file write).
  Both in `COMMAND_GROUPS`, `WRITE_COMMANDS` unaffected (cache-only; recorded in `_READ_ONLY` if the write-helper
  scan flags it), `routes.yaml` untouched unless a route uses them (it does not in P1).
- Plugin fixtures regenerated only if plugin-visible output changes (it should not).
- Tests: payload shapes, envelope, `--names` gating, dry run writes nothing (recorder + tmp logs dir), COMMAND_GROUPS.

## Step 3 — assets (spec §5)

**Task 6. `artmind/assets.py` and `query assets`.**
- One record shape, one function per kind: `views` (from `kg_views.store.list_views`, invalid ones with
  `status: invalid`), `topics` (index first, `topics.store.list_pages` fallback; staleness from `topics.stale`),
  `insights` (latest snapshot; stale when the graph generation is newer than the run's recorded generation),
  `summary_set` (one per domain from the `documents` part). `list_assets(domains, kinds, stale_only, limit)`.
- `like(text, domains, kinds, k)`: topics by index embedding, views by an embedding of `answers` through the embedding
  cache; fake-embedder tests, second call is a cache hit.
- `artmind query assets [--domain D …] [--kind K …] [--like TEXT] [--stale] [--limit N] [--compact]` with
  `rows_total`/`truncated`.
- Tests: each kind from fixtures; invalid view listed; topic fallback; insight stale on a newer generation; `like`
  ranking; CLI payload; COMMAND_GROUPS.

## Step 4 — topic index (spec §6)

**Task 7. `artmind/topics/index.py`, writers, `topic index`.**
- Setup: uniqueness on `TopicPage.slug`, vector index `topic_embedding` (filter `_domain`), fulltext `topic_text_ft`.
- `index.upsert_page(tx, page)` (node, `CITES`, `CITES_PASSAGE`, `ABOUT`, `sources_version` hash; rewrites only on change),
  `delete_page`, `sweep(domains, dry_run)` → `{indexed, updated, deleted, embedded}`; embedding with `search_document: `
  through the embedding cache. Every writer bumps the page domains' generation in the same transaction.
- Writers: `topic build`/`refresh` after the page write, `topic delete`, `topic index` (new), `vault sync` for the domains
  whose page files changed, `projection rebuild --sweep`.
- `topic index` joins `WRITE_COMMANDS` (tier 1: real `--dryRun` with `effect`, `test_dry_run_topic_index.py`).
- Never evidence: static test over `vector_query`, `survey`, `graph_query` (and the text2cypher schema text) that none
  names `:TopicPage` outside the topic-reading functions; a test that the node never carries a Document/DocChunk/DocPassage
  label and the sweep writes no `PART_OF`.
- Not in `graph_snapshot` / vault-sync fingerprint (static test on their node-label lists).

**Task 8. Survey reader switch.**
- `stale.survey_topics` reads `CITES` (one query, parameters asserted); falls back to the frontmatter scan when the index is
  empty, with a warning naming `artmind topic index`. `survey` appends the warning; output otherwise identical.

## Step 5 — planner reads (spec §8)

**Task 9.**
- `candidates` reads views from `assets` (kind `view`) instead of `kg_views.store`; the `planner` list_views call too.
- `DomainOption.text()` gains one trailing part from `stores` and `assets` ("chunks-only; 2 views; 14 topic pages;
  insights as of <date>"); the planner passes the catalogue through; absent catalogue ⇒ text unchanged.
- Tests: option text with and without assets; every other plan field identical to before on the existing synthetic plans
  (existing planner/candidates suites unchanged and green); `health` never read by `plan` (a recorder proves no health query).
- Replay gate is a live probe (below), not a hermetic test.

## Step 6 — docs

**Task 10.** `CLAUDE.md` rows (`artmind/catalogue/`, `assets.py`, `:TopicPage`, `:DomainState`, `topics/index`), group docstrings,
`artmind-query` skill one-liner (`query assets` lists what a domain already answers), `docs/INSTALL.md`
(`ARTMIND_CATALOGUE_HEALTH_TTL_S`), `justfile` recipe (`dev-catalogue` → `artmind query catalogue`/`catalogue refresh`),
`routes.yaml` untouched. Final full run, plugin tests if touched, push `origin feat/catalogue-assets`.

## Live probes (spec §10) — need an operator-named vault

`artmind setup` + `artmind topic index` inside the vault; `query catalogue`; `query assets` (twins and same-as counts against
`sameas list`); `query assets --like` on three known topics; `query survey` topics identical before/after the index;
`artmind plan replay --classifier both --repeat 4` where labels exist. If no vault is named, they are reported as not run.
