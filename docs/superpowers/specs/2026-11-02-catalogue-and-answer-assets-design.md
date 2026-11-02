# Knowledge catalogue and answer assets (P1) — design

Date: 2026-11-02. Status: **draft for review**. Parent:
`2026-11-02-retrieval-curation-architecture-design.md` §5.1, §5.2, phase P1. Builds on the
`query plan` catalogue (`2026-10-22-query-plan-design.md` §11, `artmind/query_plan/catalogue.py`)
and topic pages (`2026-11-01-summaries-survey-topics-design.md` §3). Needs no trace data,
except the `route_stats` part, which is deferred to P2 (§9).

## 1. Problem

1. **What the system holds and can answer is scattered.** Classes, documents and names
   are in the plan catalogue. Views, topic pages and insight snapshots are each read by their
   own module. Store kind (graph, chunks-only, structured) comes from schema files.
   Data health (twins, open same-as proposals, open conflicts, missing summaries) comes from
   four different commands. Nothing joins them, so the planner (P2) and the admin UI's
   Knowledge area (P6) would each re-derive them.
2. **The catalogue's freshness check misses partial changes.** The fingerprint is entity,
   document and history counts plus `:ProjectionState.last_rebuilt_at`, and that timestamp is
   written only on a **full** rebuild (`projection.record_rebuild`). A
   one-domain rebuild that renames an entity, an alias added by same-as, a saved view or a
   new topic page leaves the fingerprint unchanged. Only the 15-minute `MAX_AGE_S` catches it.
3. **Topic pages are invisible to search and traversal.** They are files in `_Topics/`
   (never ingested, by design). `query survey` finds them only by scanning every page's
   frontmatter for overlapping document ids, and nothing finds a page from a question or an
   entity.

## 2. Goals and non-goals

**Goals:**

1. One catalogue per domain with every part the planner and UI need (§4).
2. Change detection that sees every change that matters, at the cost of one query (§3).
3. A read-only registry of answer assets (§5).
4. A derived graph index for topic pages, never treated as evidence (§6).
5. Commands to read all of it (§7).

**Non-goals.**

- Changing how the planner decides (asset matching, intent, programs are P2). In P1 the
  planner only reads its existing inputs from the new catalogue (§8).
- `route_stats` (needs traces).
- Ingesting topic pages as documents. Building or refreshing pages automatically.

## 3. Change detection: a per-domain generation

**`:DomainState {_domain, generation, changed_at}`**, one node per domain, a uniqueness
constraint on `_domain` (`setup._setup_neo4j`). `generation` is an integer, incremented in
the **same transaction** as the change, at these choke points:

| Change | Where (one call each) |
|---|---|
| A document committed, re-committed, retired, restored, superseded | `_commit_document_tx`, `lifecycle._transition` |
| A projection rebuild wrote entities for the domain | the per-batch write in `projection.rebuild` (once per domain per rebuild, not per batch) |
| A table projected into the graph | `table2graph`'s commit (it uses `_commit_document_tx`) |
| A same-as approval or rejection | via the rebuild it triggers |
| A synthesis wrote descriptions | `projection synthesize`'s write |
| A conflict resolved or dismissed | `ingest resolve-conflict`'s write |

The bump is one Cypher statement, `MERGE (s:DomainState {_domain: $d}) SET s.generation =
coalesce(s.generation, 0) + 1, s.changed_at = $now`, in one helper (`catalogue_state.bump(tx,
domains)`). A hermetic test lists the write paths above and fails if any of them stops calling
it (the recorder asserts the statement inside each transaction).

**Disk-side assets** get a stamp computed from the files, not the graph: for each of
`domains/views/`, `_Topics/`, `insights/<domain>/` and `domains/schemas/`, the tuple (file
count, newest mtime, sha1 of the sorted names). Computing it is a `stat` per file, only a few
hundred files in a large vault.

**The fingerprint becomes** `(generation, entity count, document count, history count,
disk stamp)`. The counts stay as a check on writers outside the choke points (an import from
an older artmind, a hand-run Cypher). `last_rebuilt_at` is dropped from it, since generation
supersedes it. `MAX_AGE_S` rises from 15 minutes to 1 hour and stays as the safety net.

**Review:** generation rather than polling (the brief's hourly Neo4j monitor). Generation is
exact for every change artmind makes, costs one indexed read per plan, and needs no
background job. Polling would be both later and costlier.

## 4. The catalogue

`artmind/catalogue/` (moved out of `query_plan/`, since the planner, the UI and the agent all
read it; `query_plan/catalogue.py` keeps a re-export so imports keep working):

| Module | Does |
|---|---|
| `state.py` | `bump(tx, domains)`, `generations(session, domains)` (§3) |
| `stamp.py` | disk stamps (§3) |
| `parts.py` | one builder per part (table below), each a pure function of query results + files |
| `cache.py` | today's memory + disk cache, single-flight, per part |
| `model.py` | `DomainCatalogue` with one attribute per part; `to_json`/`from_json`; `CATALOGUE_FORMAT = 3` |

**Parts**, each cached and rebuilt on its own, so a cheap part never waits for an expensive
one:

| Part | Content | Built from | Invalidated by |
|---|---|---|---|
| `classes` | name, count, schema description | as today | generation |
| `relations` | top 50 `rel_type` with counts | as today | generation |
| `documents` | live and retired names (today) **plus** per document: id, `_domain`, `valid_from`, `has_summary` | one query | generation |
| `names` | the name index (today, ≤ 20,000) | as today | generation |
| `stores` | `graph` / `chunks` / `structured` per domain, `graph_extraction` flag | schema files (`schema_validate.domain_graph_extraction`), `db bridge` tables | schema stamp, structured registry mtime |
| `assets` | the domain's answer assets (§5) | disk stores + the topic index | disk stamps, generation (staleness) |
| `time` | content-date range of documents, share with `valid_from` set | one aggregate query | generation |
| `health` | see below | several sources | its own TTL (default 1 h, `ARTMIND_CATALOGUE_HEALTH_TTL_S`), or `catalogue refresh` |

**`health` fields** (per domain; counts plus up to 10 samples each, samples only in the
private on-disk cache and in command output, never in logs):

| Field | Source |
|---|---|
| `twins` | groups of same `entity_class` + casefolded `name` with more than one Entity (one aggregate query) |
| `same_as_open` | open same-as proposals touching the domain (the store behind `sameas list`) |
| `conflicts_open` | `:Conflict` with status open (the query behind `query graph conflicts`) |
| `summaries_missing` | live documents without a document-level `:DocSummary` |
| `unembedded` | entities, passages, summaries without vectors (the counts `projection status` computes) |
| `empty_extractions` | notes with empty observations over non-empty chunks (`staging_audit`, disk; graph domains only) |
| `behind_vault` | documents and tables the store is behind (`vault status`'s graph block) |
| `drift` | `projection status` drift flags |

`health` is the one part that runs many queries and a staging scan. It never sits on a plan's
critical path: the planner reads whatever cached `health` exists, and a missing one is
reported as `null`, not computed inline.

**Background refresh in `serve`.** After a plan or catalogue read finds a changed generation
for a domain, `serve` schedules that domain's `health` rebuild on a background thread,
debounced (at most one per domain per 5 minutes). `chat-ui` and `admin-ui` do the same. One-shot
CLI calls never start background work.

**Parent domains.** A parent's catalogue is the union of its children's, as today. Its
`health` is the sum, and samples are labelled with their child domain.

## 5. Answer assets

An **answer asset** is anything precomputed that can answer, or steer the answer to, a
question. P1 lists them. P2 matches questions against them.

`artmind/assets.py`, read-only, one function per kind, one common record:

```json
{
  "kind": "view | topic | insight | summary_set",
  "id": "trips_by_year | termination-rights | outcome_lift | <domain>",
  "domains": ["…"],
  "title": "…",
  "answers": "the text a matcher reads: view summary + examples; topic + overview; detector description",
  "anchor": {"kind": "entity", "_id": "…", "name": "…"} ,
  "built_at": "…",
  "stale": false,
  "stale_reasons": [],
  "evidence_kind": "rows | passages | subjects | documents",
  "open": "artmind query views show trips_by_year --compact",
  "status": "ok | invalid",
  "detail": {}
}
```

`anchor` is `null` when the asset has none.

| Kind | Source | `stale` | `open` | `detail` |
|---|---|---|---|---|
| `view` | `kg_views.store.list_views` (invalid ones listed with `status: invalid`) | never (live query) | `query views show <name>` | params with types and `entity_class`, `version` |
| `topic` | the topic index (§6), falling back to `topics.store.list_pages` when the index is absent | `topics.stale.staleness` (§3.4 of the topics spec) | `query topics show <slug>` | `sources` count, `checks`, anchor |
| `insight` | latest snapshot (`insights.store.load_latest`) | a newer graph generation than the snapshot's fingerprint records | `query insights show --detector <d> --domain <dom>` | detector, findings count, `as_of` |
| `summary_set` | one per domain: document-summary coverage | coverage below 100 % | `query graph pattern10 --documentName <name>` | `documents`, `with_summary` |

A summary is not listed one per document. That would be thousands of records with nothing to
match against beyond the document name, which the `documents` part already holds. The
`summary_set` record says whether "summarise D" can be answered from a stored summary.

**Similarity (`like`).** `assets.like(text, domains, kinds, k)` ranks assets by meaning:
topics by their index embedding (§6), views by an embedding of `answers` computed on the fly
and cached in the embedding cache (views are few). It returns `[(asset, score)]`. P2's asset
matcher and P5's topic dedup use it. In P1 it is exposed through `query assets --like`, so its
quality can be checked before anything depends on it.

## 6. The topic index

**Nodes and edges** (derived from page files, which stay the source of truth):

```
(:TopicPage {slug, topic, _domain, domains, built, anchor_kind, text, embedding, _embed_key,
             sources_version})
(:TopicPage)-[:CITES]->(:Document)        for each frontmatter source doc_id that exists
(:TopicPage)-[:CITES_PASSAGE]->(:DocPassage)  for each cited passage id that exists
(:TopicPage)-[:ABOUT]->(:Entity)          for an entity anchor, matched by key
```

- `_domain` is the page's first domain (for index filtering); `domains` holds them all. A
  page over several domains is found from any of them.
- `text` is the topic plus the page's Overview section, at most 2,000 characters. It is
  embedded with the `search_document: ` prefix through the embedding cache, as summaries are.
- `sources_version` is a hash of the frontmatter `sources`. The sweep rewrites a node only
  when it changes.
- Indexes: `topic_embedding` (vector, filtered on `_domain`), `topic_text_ft` (fulltext on
  `topic`, `text`), uniqueness on `slug`. Added by `artmind setup`.
- **Never evidence.** No `:Document`, `:DocChunk` or `:DocPassage` label; no `PART_OF`; the
  vector and fulltext indexes behind `vector-text`, `survey` and `entity-resolve` do not
  include it. A hermetic test checks that no retrieval query in `vector_query`, `survey` or
  `graph_query` names `:TopicPage`, except the topic-reading functions.
- Not in `graph_snapshot` and not in vault sync's fingerprint: each machine rebuilds the
  index from the committed page files, as it does passages from staging.

**Writers:**

| When | What |
|---|---|
| `topic build` / `topic refresh` writes a page | upsert that page's node and edges, then embed it |
| `topic delete` | detach-delete the node |
| `topic index [--domain] [--dryRun]` (new) | the sweep: upsert every page in `_Topics/`, delete nodes whose file is gone, embed what is missing; reports `{indexed, updated, deleted, embedded}` |
| `vault sync` after pulling page changes | runs the sweep for the domains touched |
| `projection rebuild --sweep` | runs the sweep too (it is "repair everything") |

Each writer bumps the page domains' generation (§3), so `assets` and catalogue caches see it.

**Readers in P1:** `assets` (topic kind), `survey`'s `topics` block (one query on `CITES`
instead of a frontmatter scan; falls back to the scan when the index is empty, with a
warning naming `artmind topic index`), and `assets.like`.

## 7. Commands

| Command | Proxied | Writes | Output |
|---|---|---|---|
| `artmind query catalogue [--domain D …] [--part P …] [--names] [--compact]` | yes | no | per domain, the requested parts (default: all except `names`; `--names` adds the name index, which is large) plus `source` (memory/disk/live) and `generation` per part |
| `artmind query assets [--domain D …] [--kind K …] [--like "<text>"] [--stale] [--limit N] [--compact]` | yes | no | asset records (§5); with `--like`, sorted by score and each carrying `score` |
| `artmind catalogue refresh [--domain D …] [--part P …] [--dryRun] [--compact]` | no | caches only | rebuilds the named parts now (default all); `--dryRun` reports which parts are stale and why |
| `artmind topic index [--domain D …] [--dryRun] [--compact]` | no | topic index | §6 |

All follow the P1a conventions (envelope, `--compact`, `--dryRun` with `effect`, exit codes). If P1 merges before P1a, its new commands already use the camelCase `--dryRun` and `--compact`, and gain the envelope and `effect` when P1a's `_echo_json` change lands; nothing in P1 depends on P1a otherwise.
`query catalogue` and `query assets` are routed in `COMMAND_GROUPS` and get `routes.yaml`
template validation like every `query` command.

`domains-overview` is unchanged (its callers include the plugin fixtures). Its rows gain
nothing; the catalogue is the richer read.

## 8. What the planner does with it in P1

Only what removes duplicated reads, with no change in decisions:

1. `candidates` reads views from `assets` (kind `view`) instead of `kg_views.store` directly,
   and documents from the `documents` part (as today).
2. The domain option text the classifier reads (query-plan spec §4.4) gains one line from
   `stores` and `assets`: "chunks-only; 2 views; 14 topic pages; insights as of <date>".
   This is the only change a classifier sees.
3. `confidence.weakest` and every other plan field are unchanged.

**Gate (from the parent §9):** `artmind plan replay --classifier both --repeat 4` on each vault
with labels gives no new `wrong` outcomes against the P0 baseline, and every label stays
identical across repeats. Until P0's labels exist, the gate is run on the labels that exist
and re-run once P0 completes.

## 9. Deferred to P2 (needs traces)

- `route_stats` part: per route and domain, turns, plan agreement, ratings, escalations,
  from `traces.analysis`. Defined once P0's ratings exist, so its fields match what P2's
  thresholds need.
- Asset matching in the plan, intent, programs.
- The `like` threshold for "this asset answers the question" (it needs labelled
  question–asset pairs).

## 10. Testing

Hermetic (`test/`, no Neo4j, no network; Cypher and parameters asserted through
`test/_neo4j_recorder.py`, never counts alone; synthetic text only):

| Area | Tests |
|---|---|
| Generation | each choke point in §3 sends the bump statement inside its own transaction, with the right domains; a parent domain is bumped through its children only |
| Stamps | file add, edit (mtime), rename and delete each change the stamp; an unchanged tree keeps it |
| Fingerprint | a generation change alone invalidates `classes`/`names`; a stamp change alone invalidates `assets`; `health` follows its TTL only |
| Parts | each builder from recorded rows + temp files; `health` samples capped at 10; parent union |
| Cache | memory/disk/live sources, single flight, format bump 2 → 3 discards old disk files; `ARTMIND_PLAN_CATALOG=0` builds live |
| Assets | each kind's record from fixtures; invalid view listed; topic fallback to frontmatter when the index is empty; `insight` stale on a newer generation |
| `like` | ranking with a fake embedder; views embedded once (cache hit on the second call) |
| Topic index | sweep upserts, updates only on `sources_version` change, deletes orphans; `topic build`/`delete` call it; no retrieval query names `:TopicPage` (static scan of query modules) |
| Survey | `topics` from `CITES` with the right parameters; fallback path with its warning |
| CLI | the four commands' payloads; `COMMAND_GROUPS`; `routes.yaml` validation |
| Planner | domain option text gains the asset line; every other plan field identical to before on synthetic plans |

Live probes (`ARTMIND_NO_PROXY=1` or a restarted `serve`): in `brain01` and `banking_corpus`,
`artmind setup`, `topic index`, then `query catalogue` and `query assets` (counts plausible,
twins and same-as numbers matching `sameas list` and a hand query); `query assets --like` on
three topics with known pages; `query survey` topics identical before and after the index;
`plan replay` per §8.

## 11. Rollout

1. `catalogue_state.bump` at every choke point and the `:DomainState` constraint
   (`artmind setup` in each vault). The fingerprint change ships at the same time.
2. The catalogue package and parts; `query catalogue`, `catalogue refresh`.
3. `assets` and `query assets`.
4. The topic index, `topic index`, the survey reader switch.
5. Planner reads (§8); replay gate.
6. Docs: `CLAUDE.md` rows (`artmind/catalogue/`, `assets.py`, `:TopicPage`, `:DomainState`),
   group docstrings, artmind-query skill (one line: `query assets` lists what a domain
   already answers; the skill diet itself is P3), `docs/INSTALL.md`
   (`ARTMIND_CATALOGUE_HEALTH_TTL_S`), `justfile` recipe.

Merged between collection rounds (parent §9). Then in each vault: `just dev-install`,
`artmind setup`, `artmind topic index`, restart `serve`/`chat-ui`/`admin-ui`.

## 12. Privacy

The catalogue's disk cache, asset records and `health` samples hold entity, document and
topic names. They stay in the gitignored `logs/` (cache) or in command output, never in the
checkout. The topic index embeds page text with the configured local embedder (Ollama), so
nothing leaves the machine except what `assets.like` sends to that embedder.

## 13. Open items

- Whether `health.twins` should ignore classes the schema marks as legitimately
  many-same-named (dates, generic events). Recommend a per-class opt-out in the domain's
  plan config (`domains/plan/<domain>.yaml`, `twins_ignore: [CLASS]`), added only when a real
  vault shows noise.
- Whether `like` should also embed insight detectors' descriptions. Recommend no: there are
  about ten detectors, and lexical rules route to them well (S6 works today).
