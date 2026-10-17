# Insights layer — schema-driven graph analytics, snapshots and cards — design

Status: draft for review (2026-10-17). Branch `spec/insights-layer`.

## 1. Problem

Views (`docs/superpowers/specs/2026-10-09-kg-views-design.md`) answer a question
someone already knows to ask: a reviewed, parameterised Cypher query, run live.
Nothing in artmind tells the operator what they did **not** think to ask:

- **No unprompted findings.** Nothing reports which entities are becoming more
  or less prominent, which groups the graph falls into, who connects those
  groups, what changed since last month, or which entities go with a good or
  bad outcome.
- **No use of the graph as a graph.** Every query layer (patterns, views,
  text2cypher, vector search) reads neighbourhoods. Nothing computes over the
  whole structure: communities, centrality, bridges, similarity.
- **No memory of the graph's state.** There is no record of what the graph
  looked like at an earlier point in time, so "what changed" cannot be asked.
- **Schema and graph drift unseen.** The schema says what should exist; the
  extracted graph shows what does. Nothing compares the two. Gaps such as a
  `recurrent` class whose entities are seen once each, a free-text property
  with hundreds of distinct values, or two relationship types that mean the
  same thing go unnoticed until a query fails.

### 1.1 Evidence from an exploratory run

A probe session on a personal-journal vault (about 1,700 daily notes over five
years, about 9,000 entities, 17,000 entity-to-entity edges) ran read-only
Cypher through `artmind views test` and computed algorithms in Python with
networkx. It produced findings no view would have asked for:

- Louvain on a person co-mention graph split cleanly into a family circle and
  two work circles (modularity 0.40). Normalised per 100 notes, one work circle
  grew roughly twelvefold in four years while family stayed flat. Modularity
  fell each year, so the circles were merging.
- Betweenness picked out the two or three people who connect the circles.
- Silence measured against each person's own cadence surfaced people normally
  seen every three weeks who had not appeared for over a year.
- Day-level mood (majority polarity of the day's EMOTION entities) against
  co-occurring entities and event types showed strong, consistent lifts:
  travel days much better than baseline, health-incident days much worse.
- A `JOINED` / `LEFT` edge pair on the anchor person, a step change in which
  organisations were mentioned, and a new circle's first appearances all
  pointed to one transition in the same quarter.

It also showed what the method must handle:

- **An anchor hub.** The diarist entity sat on 96% of days and held 44% of
  all edges. Unless it is excluded, every centrality measure returns it.
- **Duplicates split signals.** Pairs such as a kinship word and its
  variant, a person and their initials, or one organisation under two names halved counts. In one
  case they flipped a mood association's sign. Neighbourhood similarity alone
  found household cohorts, not duplicates. The useful signal was name
  similarity, plus similar neighbours, plus never being mentioned together.
- **Occurrent-like classes.** EVENT and ACTIVITY entities averaged about one
  observation each, so trends only exist at the level of their `type`
  property.
- **Free-text vocabularies.** A `PLAN.status` property had 200 distinct values
  and an emotion `type` had 35. The schema offered no closed status, so
  "open loop" detection could not trust it.
- **Volume bias.** The first and last years were partial, so raw counts
  misled. Every time series has to be normalised by documents per period.
- **Misattributed anchor edges.** `anchor → WORKS_AT → X` pointed at job
  roles, places and institutions that belonged to someone else.
- **No Graph Data Science plugin.** Neo4j Community ships APOC only.
  Pulling the needed slice with Cypher and computing in Python took
  milliseconds at this scale.

## 2. Goals and non-goals

Goals:

1. **Detectors.** A library of deterministic, domain-agnostic detectors that
   turn the graph into ranked findings: salience trends, dormancy,
   communities and their drift, brokers, outcome lift, transitions, open
   loops, same-as candidates.
2. **Driven by schema and shape.** The schema (`kind`, `type_examples`,
   `relates_to`, `temporal`) and a measured shape profile decide which
   detectors run on which classes, with no per-domain code. A small optional
   config file tunes the rest.
3. **Graph health.** Report where the graph and its schema disagree, as
   findings that point to a schema edit or a curation action.
4. **Snapshots and diffs.** Every refresh writes a snapshot into the vault,
   committed with it. Each run is diffed against the previous one, so "new
   since last run" is itself a finding.
5. **Grounded.** Every finding carries the entity `_id`s and counts it rests
   on, so an agent can cite it via `query entity-context`.
6. **Surfaces.** CLI, agent route plus skill, and an admin-ui Insights tab.
   The admin-ui also gains a read-only Views tab.
7. **Narration.** An LLM writes one sentence per top card and a name for
   each community, from the card's numbers only, cached in the snapshot.

Non-goals (v1):

- Causal claims. Lift findings are co-occurrence and are labelled so.
- Real-time or automatic refresh. Refresh is manual, like ingest
  (`trigger: manual`): nobody should discover automatic LLM spend by surprise.
- The Neo4j GDS plugin. It is an optional later backend (§14).
- Writing to the graph. Same-as candidates go to the curation queue for
  review; insights never merge entities themselves.
- An Obsidian note surface. It is a later phase; the CLI JSON is enough to
  build one.
- Cross-domain insights. v1 runs per domain family, like views.

## 3. Concepts

| Term | Meaning |
|---|---|
| **Detector** | A named, deterministic function. It reads a profile and config, pulls the slice of graph it needs (a *projection*), computes, and returns findings. |
| **Projection** | A read-only Cypher query, scoped by `$domains`, returning rows (entity-period counts, an edge list, typed day records). Detectors never hold a driver themselves; they ask the projection layer. |
| **Finding** | One result, with a stable `key` (detector + subject ids), a `score` in [0, 1], `evidence` (ids, counts, periods) and `card` data. |
| **Card** | How a finding renders: `stat`, `series` (sparkline/table by period), `ranking`, `network`, `timeline` or `pairs`. |
| **Run** | One refresh of one domain: the profile, the plan (which detectors ran on what, which were skipped and why), all findings, narrations and the diff. Saved as a snapshot. |
| **Health finding** | A finding about the graph or schema rather than the corpus. It carries a suggested action (`schema`, `curate`, `reextract`). |
| **Anchor** | An entity excluded from co-mention projections and centrality because it is everywhere: the diarist of a journal, the company in its own filings. |
| **Evidence day** | A distinct `_doc_valid_from` on which an entity has an observation. It is the unit of salience. On undated corpora it falls back to distinct documents. |

## 4. Inputs

### 4.1 The domain schema

Read with `temporal.load_schema(domain)`; never modified by insights.

| Schema element | Used for |
|---|---|
| `entity_types.<C>.kind` | `recurrent` classes get entity-level detectors (salience, dormancy, centrality, communities). `occurrent` classes are aggregated by `type` (trends, lift). The profile can override this (§4.2). |
| `type_examples` | Bucket labels for occurrent aggregation and lift, and the reference vocabulary for sprawl health. |
| `relates_to` verbs | Declared vs observed relationship usage. Transition verbs (§6.6), matched by name: `joined`, `left`, `started`, `resumed`, `moved_to`, `married_to`, … plus any listed in config. |
| `temporal` | Whether time-based detectors run. If most documents fall back to `default_ingestion` time, they are skipped with a reason. |
| `property_aliases` | Already applied at extraction; the profile counts post-alias keys. |
| `PERSON.type_examples` containing `self` | The first anchor heuristic (§4.4). |

### 4.2 The shape profile

Computed at the start of every run by one batch of projection queries. Stored
in the snapshot, so health findings can be diffed too.

Per class: entity count, mean and median observations per entity, share of
entities seen on one evidence day only, filled rate and distinct-value count
of each property, and top `type` values.

Per observed `(rel_type, source class, target class)` triple: count, and
whether the schema declares it.

Per domain: documents per period, the share of documents whose valid time came
from the default rather than from content, open conflicts by property, and
connected-component sizes of the entity graph.

**Effective kind.** A class's effective kind is its declared `kind`, unless
the profile contradicts it: a `recurrent` class whose median evidence days per
entity is 1 and whose single-day share is ≥ 80% is treated as occurrent for
detector planning. The contradiction is reported as a health finding (§7).

### 4.3 The insights config (optional)

`<ARTMIND_HOME>/domains/insights/<domain>.yaml`. In a vault that is
`<vault>/.artmind/domains/insights/`, committed. Like views, it is user data:
`setup` / `init` never seed or overwrite it. Every key is optional; with no
file, everything is inferred.

It is deliberately **not** a block inside the schema YAML.
`projection.schema_set_hash()` hashes every schema file's content, and a
changed hash flags schema drift for a projection rebuild. Tuning a detector
must never look like a schema change.

```yaml
domain: personal_journal
anchors: ["@<entity _id>"]            # override anchor inference; names resolve like view params
period: year                          # year | quarter | month
min_evidence_days: 4                  # entity floor for entity-level detectors
outcome:                              # enables the lift detector (§6.5)
  class: EMOTION
  property: type
  positive: [joy, contentment, pride, relief, hope, love, excitement, satisfaction, inspiration]
  negative: [worry, anxiety, frustration, exhaustion, sadness, stress, disappointment, pain]
transitions:
  extra_verbs: [promoted_to]
open_loops:                           # enables the open-loops detector (§6.7)
  class: PLAN
  property: status
  open: [firm intention, tentative, in progress]
  closed: [completed, executed, abandoned, acted upon]
max_findings: 25                      # per detector, plus every new finding
min_period_docs: 20                   # periods with fewer documents are shown, never scored
keep_runs: 12                         # snapshot retention (§9.2)
narrate_top: 15                       # narration budget (§11)
detectors:                            # per-detector switches and thresholds
  dormancy: {silence_multiple: 3, min_silence_days: 60}
  communities: {resolution: 1.0, classes: [PERSON]}
  same_as: {enabled: true}
narrate: true
```

A missing `outcome` or `open_loops` block skips that detector with the reason
`not configured`. The `artmind-insights` skill (§10.3) can draft the block
from the profile's vocabulary.

### 4.4 Anchor inference

When `anchors` is not set:

1. A PERSON entity whose `type` is `self`, if exactly one exists.
2. Otherwise, any entity present on ≥ 80% of the domain's evidence days.

Anchors are excluded from co-mention projections, centrality and dormancy.
They are kept as the source side of transition detection, so "the anchor
joined X" works. The inferred anchors and the rule that chose them are
recorded in the run's plan and shown on the Insights tab, so a wrong guess
can be seen and corrected in config.

## 5. Pipeline

```
profile ─▶ plan ─▶ project ─▶ compute ─▶ score ─▶ diff ─▶ narrate ─▶ snapshot
```

1. **Profile** (§4.2): read-only Cypher. Fails fast if Neo4j is unreachable.
2. **Plan**: each registered detector declares `applies(profile, schema,
   config) -> list[Target] | Skip(reason)`. A target is, for example, one
   class or one class-type pair. The plan is recorded in full, skips
   included.
3. **Project**: detectors call named projections in
   `artmind/insights/projections.py`. Each is one read-only, `$domains`-scoped
   Cypher statement, validated with the same `validate_read_only` /
   `validate_domain_scoped` checks as views, under the 60 s server-side
   timeout. Shared projections (entity-evidence-days, the co-mention edge
   list) are memoised per run.
4. **Compute**: pure Python over rows. Graph algorithms use networkx (§8).
5. **Score**: each detector maps its raw statistic to `score` in [0, 1] with
   a support floor (§6). The run keeps the top `max_findings` per detector
   (default 25). A `--only` refresh carries the other detectors' findings
   over unchanged, so they are not reported as gone.
6. **Diff**: against the previous snapshot of the same domain (§9.3).
7. **Narrate** (optional): §11.
8. **Snapshot**: atomic write (§9.1).

`refresh` short-circuits if nothing changed. The run's `fingerprint` is the
schema-set hash, the same-as hash, the insights config hash, the entity count
and the latest document valid date. If it matches the latest snapshot's,
refresh prints `insights up to date` and exits 0, unless `--force`.

## 6. Detector library (v1)

Every detector is normalised. A count per period is a rate per 100 documents
in that period, and a period with fewer than `min_period_docs` (default 20)
documents is shown but never scored. Thresholds below are defaults, all
overridable under `detectors.<name>`.

### 6.1 `salience_trend` — rising and fading entities

- **Applies to:** recurrent classes with ≥ 2 scored periods.
- **Projection:** `entity_period_days`, giving (entity, period, evidence days).
- **Statistic:** a Theil–Sen slope of the normalised rate over the last
  `window` periods (default 3), plus the ratio of the last full period to the
  mean of the earlier ones.
- **Score:** `min(1, |log2 ratio| / 3 · min(1, sqrt(support / 30)))`, so an
  8× change at full support scores 1.0. Support is the total evidence days;
  minimum 8.
- **Card:** `series`, rising and fading in separate rankings.

Occurrent classes get the same treatment per `type` value (`type_trend`),
which is how "work activity rose, creative activity fell" is found.

### 6.2 `dormancy` — gone quiet relative to their own rhythm

- **Applies to:** recurrent classes with temporal data.
- **Projection:** `entity_days`, the evidence-day list per entity with
  ≥ `min_evidence_days` days.
- **Statistic:** `silence = as_of − last_day`, where `as_of` is the latest
  document valid date in the domain (not today). `cadence` is the median gap
  between consecutive evidence days. The detector fires when
  `silence > silence_multiple × cadence` and `silence > min_silence_days`.
- **Score:** `min(1, log(silence/cadence) / log(30))`, weighted by support.
- **Card:** `ranking` with last seen, usual gap and silence.

### 6.3 `communities` — circles, and their drift

- **Applies to:** recurrent classes listed in `detectors.communities.classes`.
  The default is PERSON if present, otherwise the class with the most
  co-mention edges.
- **Projection:** `co_mention_edges`: for entities of that class with
  ≥ `min_evidence_days`, anchors excluded, an edge weighted by shared evidence
  days, minimum weight 2. A `relates_to` projection (RELATES_TO edges between
  members) is an alternative selectable in config. It is not the default,
  because in an anchor-centred corpus most RELATES_TO edges run through the
  anchor.
- **Compute:**
  - Louvain (`seed=7`, configurable resolution) on the whole span, which
    gives the **circles**.
  - The same per period. Each period's communities are matched to the
    whole-span circles by member-weighted Jaccard (≥ 0.3), so a circle keeps
    its id across periods.
  - Modularity per period.
- **Findings:**
  - `circle`: members and their top members by weighted degree.
  - `circle_share`: each circle's share of normalised mentions per period.
  - `circle_shift`: the largest per-period share change.
  - `cohesion_trend`: modularity's slope; falling modularity means the
    circles are merging.
- **Card:** `network` for circles (≤ 60 nodes drawn, the rest listed),
  `series` for share and modularity.
- **Circle naming** is narration (§11).

### 6.4 `brokers` — who connects the circles

- **Projection:** `co_mention_edges`, the same one as §6.3.
- **Compute:** betweenness centrality with edge distance `1/weight`. For each
  node, it also counts how many distinct circles its neighbours fall into.
- **Score:** betweenness rank, kept only for nodes whose neighbours span
  ≥ 2 circles.
- **Card:** `ranking`, with the circles each broker spans.

### 6.5 `outcome_lift` — what goes with good and bad outcomes

- **Applies to:** domains with an `outcome` config.
- **Projection:** `outcome_days` assigns each evidence day a polarity: the
  majority of the configured class's positive vs negative values that day,
  with ties as neutral. `entity_days` and `type_days` give, per entity or
  occurrent type, the days it appears.
- **Statistic:** for each subject with ≥ `min_support` polar days (default
  10):
  - its negative share minus the baseline negative share (and the same for
    positive);
  - a two-proportion z-score.
- **Kept:** findings with `|z| ≥ 2`.
- **Score:** `min(1, |z| / 5)`.
- **Card:** `pairs` (subject, share, baseline, lift), better and worse
  ranked separately. Every lift card carries the fixed caption
  *"Co-occurrence on the same day, not cause."*

The detector is generic: any class with a polar property works (incident
severity, review sentiment, deal outcome).

### 6.6 `transitions` — moments when something changed

There are two sources:

- **Explicit:** anchor-side relationships whose verb is a transition verb
  (§4.1) or in `transitions.extra_verbs`. Each becomes an event: verb,
  target, first date.
- **Implicit (change points):** among the anchor's top targets for each
  relationship type (for example the organisation it most `WORKS_AT`), a
  change in the dominant target between consecutive periods. The same goes
  for a `circle_shift` above 2× its median change.

Events within `cluster_days` of each other (default 90) are grouped into one
transition. A transition backed by ≥ 2 independent sources scores higher.

- **Card:** `timeline`.

### 6.7 `open_loops` — intentions with no follow-up

- **Applies to:** domains with an `open_loops` config.
- **Compute:** entities of the class whose configured property is in `open`,
  whose last evidence day is more than `stale_days` (default 60) before
  `as_of`, and which have no later observation.
- **Score:** age, weighted by `urgency` when that property is present.
- **Card:** `ranking`.

Statuses that fit neither `open` nor `closed` are counted and reported as a
vocabulary-sprawl health finding (§7).

### 6.8 `same_as_candidates` — probable duplicates

- **Applies to:** recurrent classes.
- **Candidate generation:** pairs within one class where at least one holds:
  - one normalised name is a prefix or token-subset of the other
    (`Sam` / `Sam Rivera`);
  - one name matches the other's initials (`AB` / `Asha Bose`);
  - the two names are a known kinship or alias pair;
  - normalised edit distance is ≤ 0.25;
  - fulltext-index hits.
- **Evidence for each pair:**
  - the Jaccard similarity of their co-mention neighbourhoods;
  - how often they are co-mentioned (duplicates are almost never mentioned on
    the same day);
  - whether their time spans overlap;
  - whether their `relationship_to_*` or `type` values agree.
- **Score:** a fixed logistic combination of those signals. Its weights are
  tuned on a small labelled fixture (§13).
- **Output:** findings, plus `artmind insights same-as --export`, which writes
  candidates in the format `artmind-curate` reads for review. Nothing merges
  automatically.

Kinship pairs ship as a small, editable list in the package
(`artmind/insights/aliases.yaml`, for example `dad`/`daddy`/`papa`,
`mom`/`mum`/`mummy`). The config can extend it.

## 7. Graph-health detectors

They run on every refresh, from the profile and the schema alone. They need no
corpus config. Each carries an `action`.

| Detector | Finds | Action |
|---|---|---|
| `kind_mismatch` | A declared `recurrent` class that behaves as occurrent (§4.2), or the reverse | `schema`: change `kind`, or fix the class's naming guidance |
| `vocabulary_sprawl` | A property whose distinct values are > 5× its class's `type_examples` (or > 40 with no examples), plus near-duplicate values (`In progress` / `in progress`) | `schema`: list the values in the hint; `retime --redoProperties` to fold them |
| `relationship_usage` | Declared triples never observed; observed triples not declared; pairs of rel types with the same endpoint classes and > 50% target overlap (`WENT_TO` vs `VISITS`) | `schema` |
| `attribution_suspects` | Anchor edges whose target's `type` is atypical for that relationship (for example `WORKS_AT` → a LOCATION-like or role-like ORGANIZATION) | `reextract` or `curate` |
| `open_conflicts` | Open conflicts grouped by class and property, with trend | `curate` |
| `fragments` | Small connected components (2–5 entities) cut off from the main graph | `curate` |
| `split_signal` | A same-as candidate whose two halves show opposite lift or trend findings | `curate` (high priority: the duplicate is distorting results) |

Health findings are diffed like any other, so the Insights tab can show that a
schema edit fixed sprawl.

## 8. Compute dependencies

- **networkx in core** (`networkx>=3.4`). It is pure Python, about 2 MB.
  numpy/scipy are not required. A test in a clean environment confirmed that
  the following run without them:
  - communities: Louvain, label propagation, greedy modularity, modularity;
  - centrality: betweenness, closeness, eigenvector, Katz;
  - structure: k-core, clustering, bridges, articulation points;
  - similarity: Jaccard, Adamic–Adar.
- **Avoided:** PageRank, HITS, SimRank, spectral methods and layouts all need
  numpy. Where a "lead member" is needed, weighted degree or eigenvector
  centrality stands in. Network layout happens in the browser (§10.4).
  - On `[ingest]` installs numpy and scipy are already present via docling.
    Nothing may branch on that.
- Determinism: fixed seeds, sorted inputs and stable tie-breaks (score, then
  name, then `_id`). The same graph gives the same snapshot byte-for-byte,
  apart from timestamps.
- Scale: projections cap their node sets with `min_evidence_days`.
  Co-mention projections are refused above 5,000 nodes or 200,000 edges, with
  a finding telling the operator to raise the floor.

## 9. Snapshots

### 9.1 Location and format

```
<vault>/.artmind/insights/<domain>/
  runs/2026-10-17T09-30-12Z.json     # one file per run
  latest.json                        # copy of the newest run (small, stable path for readers)
```

Outside a vault this is `~/.artmind/insights/<domain>/`. The path comes from
`VaultLayout.insights_dir` / `paths.INSIGHTS_DIR`, the same pattern as
`REVIEWS_DIR`. It is never seeded, never ingested (insights files are not
mapped in `vault.yaml`; `ingest` must also refuse paths under `.artmind/`
regardless), and committed with the vault. Writes are atomic: the temp file
goes in `logs/`, never in the committed folder, the same as review dossiers.

```json
{
  "format": 1,
  "domain": "personal_journal",
  "created_at": "2026-10-17T09:30:12Z",
  "as_of": "2026-10-01",
  "fingerprint": {"schema": "…", "same_as": "…", "config": "…", "entities": 5120, "latest_doc": "2026-10-01"},
  "anchors": [{"_id": "…", "name": "…", "rule": "type=self"}],
  "profile": {"classes": {…}, "relations": […], "documents_per_period": {…}},
  "plan": [{"detector": "dormancy", "target": "PERSON", "status": "ran"},
           {"detector": "open_loops", "status": "skipped", "reason": "not configured"}],
  "findings": [{
     "key": "dormancy:PERSON:<_id>",
     "detector": "dormancy", "kind": "corpus",
     "score": 0.82, "title": "Quiet for 18 months",
     "subjects": [{"_id": "…", "name": "…", "class": "PERSON"}],
     "stats": {"last_seen": "2025-03-02", "cadence_days": 18, "silence_days": 578, "support": 30},
     "card": {"type": "ranking", …},
     "narration": null,
     "diff": "new"
  }],
  "circles": [{"id": "c1", "name": "Family", "name_source": "llm", "members": ["…"]}],
  "diff": {"previous": "2026-09-12T…Z.json", "new": 4, "gone": 2, "changed": 7},
  "llm": {"model": "…", "calls": 12, "cached": 30}
}
```

### 9.2 Retention

The newest `keep_runs` runs are kept (default 12, set in config). `refresh`
deletes older run files and says so. Git history keeps the rest.

### 9.3 Diff

Findings are matched by `key`. Each one is marked `new`, `gone` (present in
the previous run, absent now, kept in the snapshot with `diff: gone` and its
last stats), `changed` (score moved ≥ 0.15 or a headline stat moved ≥ 25%), or
`same`. Circles are matched by member Jaccard, so a renamed circle is
`changed`, not new. "What changed" is the Insights tab's first section.

### 9.4 Privacy

Snapshots hold corpus content: names, counts and narrations. They follow the
review-dossier rule. They live only in the vault, whose repo is private.
Tests, fixtures and examples in the code repo use synthetic data only.
`review leakcheck` also takes private terms from the latest snapshots'
subject names, so a code diff that quotes a snapshot is caught.

## 10. Surfaces

### 10.1 CLI — `artmind insights` (not proxied; needs Neo4j for `refresh`)

| Command | Does |
|---|---|
| `insights refresh --domain D [--force] [--no-narrate] [--only DET,..]` | Runs the pipeline and writes a snapshot. Prints a summary: findings per detector, new/gone, skipped detectors with reasons. |
| `insights list --domain D [--detector X] [--kind corpus\|health] [--new]` | Findings from `latest.json`, ranked. |
| `insights show <key>` | One finding with full evidence and its card. |
| `insights profile --domain D` | The shape profile only. Writes nothing; useful for authoring config. |
| `insights runs --domain D` | Snapshot history with diff counts. |
| `insights same-as --domain D --export PATH` | Candidates in curation-queue format. |
| `insights config check --domain D` | Validates the config file: unknown keys, unknown classes and properties, outcome values never seen in the graph. |

All commands take `--compact`. `refresh` reports a stale graph (`artmind:
graph is N docs behind the vault`) as an advisory, as queries do, and runs
anyway.

### 10.2 Agent route — `artmind query insights …` (proxied by `serve`)

`query insights list|show` read `latest.json` only, with no Neo4j and no
compute, so they are fast and safe on the grounded Q&A surfaces. Their output
carries `as_of` and `created_at`, so the agent can say how fresh an insight is.
`TracedGroup` records them as spans like any other `query` call.

### 10.3 Skills

- **`artmind-query`** gains an "Insights" section.
  - **When to use:** broad or retrospective questions ("what's changed this
    year", "who matters most at work now", "what tends to go with bad
    days").
  - Answer from findings. Ground each subject through `query entity-context`
    with its `_id`.
  - Always pass on the lift caption.
  - Say when the snapshot is older than the latest documents.
  - Never run `refresh`; the agent has no write path.
- **New `artmind-insights`** skill, for the operator from Claude Code or the
  admin console:
  - interpret a run;
  - draft or extend `domains/insights/<domain>.yaml` from `insights profile`;
  - turn health findings into schema edits (handing off to
    `artmind-create-schema`) or curation (handing off to `artmind-curate`);
  - promote a corpus finding into a view (handing off to
    `artmind-create-view`, with the finding's projection as the starting
    Cypher and its title as `source_question`).

### 10.4 Admin-ui: Insights tab

A new tab in `dashboard.html`, next to Schemas, backed by
`artmind/webui/insights_routes.py`:

- `GET /api/insights?domain=` returns `latest.json`, trimmed for display.
- `GET /api/insights/runs?domain=` returns the run history.
- `POST /api/insights/refresh` starts a background refresh. Progress is
  reported through `GET /api/insights/refresh/progress`, the same pattern as
  snapshot restore.

The layout, top to bottom:

1. **Header:**
   - domain picker;
   - `as_of`;
   - a "graph has newer documents" badge when the fingerprint's
     `latest_doc` < the registry's latest;
   - the inferred anchors, each with a "wrong?" link to the config;
   - the Refresh button.
2. **What changed:** new and changed findings since the previous run.
3. **People & circles:**
   - a network card drawn as SVG by a small built-in force layout (about
     100 lines in `static/insights.js`, no new vendored library), with
     circle colours and broker nodes outlined;
   - circle-share series;
   - brokers and dormancy rankings.
4. **Trends:** salience and type trends.
5. **Associations:** outcome lift, always with its caption.
6. **Transitions:** timeline.
7. **Graph health:** health findings grouped by action.

Each card has actions:

- **Ask in console** opens the agent console with a prepared question
  grounded on the finding's `_id`s.
- **Copy promote-to-view prompt** gives a prompt for `artmind-create-view`.
- **Send to curation** appears on same-as and split-signal findings and
  writes through the same export as the CLI.

Narrations show under each card title, marked as generated.

### 10.5 Admin-ui: Views tab (read-only)

Yes, the admin-ui gets a Views tab too. Views and insights are siblings, one
asked and one unprompted, and promotion moves findings from one to the other.
Without the tab, the operator can't see the result of a promotion. The tab is
small because `kg_views` already does the work:

- `GET /api/views?domain=` returns the catalogue (`store.list`): name,
  summary, examples, params.
- `GET /api/views/{name}` returns `view.yaml` and `SKILL.md`.
- `POST /api/views/{name}/run` takes the params dict and returns
  `run_view(..., render=True)`. On `needs_disambiguation` / `no_match` it
  returns the candidates, and the UI offers them as `@<_id>` choices.
- The UI is a catalogue list, a detail pane with a form generated from the
  param specs (entity params get a typeahead backed by `query
  entity-resolve`), and the rendered markdown result via the vendored
  `marked` + `purify`.

Authoring stays with the `artmind-create-view` skill and `views save`. The tab
never writes view files.

## 11. LLM narration and circle naming

- **When:** at the end of `refresh`, unless `--no-narrate` or `narrate:
  false`.
- **What:**
  - one sentence per finding in the top `narrate_top` (default 15) by score,
    plus every `new` finding;
  - one 1–3 word name per circle.
- **Input:** a finding's structured `stats`, `subjects` (names, classes,
  `type`, `relationship_to_*`) and periods. No chunk text and no other
  findings. Circle naming gets the top 10 members' names, their
  `relationship_to_*` / `type` values, and the circle's top shared
  ORGANIZATION / LOCATION neighbours.
- **Prompt rules** (versioned in `artmind/insights/prompts.py`):
  - state only what the numbers show;
  - no causal words for lift findings;
  - no advice;
  - name only the subjects given.
  - A post-check rejects a narration that names an entity not in `subjects`
    or contains a number not in `stats`. A rejected narration falls back to
    the template title.
- **Model:** the same configured provider as extraction (`config.env`),
  logged to `llm_calls.log` like other calls.
- **Cache:** keyed by a hash of (prompt version, finding stats, subjects).
  Carried forward from the previous snapshot, so an unchanged finding costs
  nothing. Circle names are keyed by the member set, so a circle keeps its
  name while membership drifts by less than 30%.
- **Failure:** any LLM error leaves `narration: null` and
  `name_source: "template"` (`Circle around <lead>`). The run still
  succeeds.

## 12. Module layout

```
artmind/insights/
  __init__.py
  model.py          # Finding, Card, Run, Plan dataclasses; JSON (de)serialisation; format version
  config.py         # load + validate domains/insights/<domain>.yaml (unknown keys are errors)
  profile.py        # shape profile (§4.2), effective kind, anchor inference (§4.4)
  projections.py    # named read-only Cypher projections, memoised per run
  graphs.py         # networkx builders, Louvain wrapper, circle matching across periods, weighted degree
  stats.py          # normalisation, Theil–Sen, two-proportion z, cadence
  detectors/
    __init__.py     # registry: name -> Detector(applies, run)
    salience.py dormancy.py communities.py brokers.py lift.py
    transitions.py open_loops.py same_as.py
    health.py       # §7 detectors
  diff.py           # match by key / circle Jaccard, mark new/gone/changed
  narrate.py prompts.py
  store.py          # snapshot paths, atomic write, retention, latest.json
  runner.py         # the pipeline (§5)
  aliases.yaml      # kinship/alias pairs for same_as
```

- The CLI goes in `cli.py`: an `insights` group, plus `query insights`
  under `query`. Both are routed in `COMMAND_GROUPS` so `test_cli_guide.py`
  passes.
- `paths.INSIGHTS_DIR` and `VaultLayout.insights_dir` are added.
- Each detector is a module with one `applies` and one `run`, testable on its
  own against rows; no detector touches the driver.

## 13. Testing

All hermetic, in `test/`, with synthetic data only:

- **Detector unit tests** feed hand-built projection rows to `run` and assert
  on findings: a rising entity, a dormant entity with a known cadence, two
  planted communities plus a bridge node, a planted lift with a known
  z-score, a transition pair, a duplicate pair with initials.
- **Planning tests** cover:
  - a profile with a contradicted kind turns entity-level detectors into
    type-level ones and emits `kind_mismatch`;
  - missing `outcome` config skips lift with its reason;
  - anchor inference by `type=self` and by coverage.
- **Projection tests** use the `run_side_effect` recorder pattern
  (`test/test_update.py`). They assert on the Cypher sent and its params:
  `$domains` present, read-only check passes, anchors passed as excluded
  ids. They never assert on counts from a mocked session.
- **Determinism:** the same rows twice produce identical findings JSON.
- **Diff tests** cover new/gone/changed, and a circle that keeps its id
  through a member change.
- **Store tests** cover:
  - the atomic write leaves no temp file in the committed folder;
  - retention;
  - `setup` never touches `domains/insights/` or `insights/`;
  - ingest refuses paths under `.artmind/`.
- **Narration tests** use a stub LLM. They check that the post-check rejects
  an unknown name or number, that the cache is reused for unchanged stats, and
  that a failure falls back to template text.
- **Routes tests** use `TestClient` for both tabs' APIs, including view-run
  disambiguation.
- **A same-as weight fixture:** about 40 synthetic labelled pairs, used to fit
  and pin the logistic weights.
- **End to end,** outside the hermetic suite:
  `ARTMIND_NO_PROXY=1 artmind insights refresh --domain <d>` against a live
  vault. Expect a plan with no unexpected skips and a byte-stable second run
  (`--force`, timestamps aside).

## 14. Fine-tuning questions

These are settled by default above, and each is worth revisiting after the
first real runs:

1. **Unit of salience.** Evidence days treat "mentioned five times on Monday"
   as one. That suits journals; for meeting notes or filings, distinct
   documents may be better. Proposal: the unit follows whether the schema has
   `temporal.document`, overridable in config.
2. **Co-mention vs typed edges for communities.** Co-mention found the
   circles; typed edges (COLLEAGUE_OF, RELATED_TO) are sparser but
   semantically sharper. Worth testing a blend: co-mention weight plus a bonus
   per typed edge.
3. **Score calibration across detectors.** Each detector scores in [0, 1]
   independently, so "top 15 overall" mixes scales. Start with per-section
   ranking on the tab, and only rank globally once there is operator feedback
   to calibrate against.
4. **Feedback on findings.** A thumbs-up/down per card, stored in the
   snapshot and carried by key, would let detectors learn which findings are
   noise. It is left out of v1 to keep snapshots one-way, and is the obvious
   v2.
5. **Insights over the structured store.** `table2graph` entities are in the
   graph and get covered. The raw SQL tables do not. Column-level profiling
   could feed health findings later.
6. **The GDS backend.** If a domain outgrows the 5,000-node cap, add an
   optional `compute: gds` that runs Louvain/betweenness in-database via GDS
   Community, behind the same detector interface.
7. **Anchor-centred insights.** In journals the anchor is the point: "my
   week" is the anchor's ego network. A later `ego` detector could profile
   the anchor's own activity mix and transitions as a first-class section,
   rather than only excluding it.
8. **Re-running the outcome after curation.** `split_signal` findings suggest
   lift results should be recomputed after a same-as merge. Today that means
   a manual `refresh`; a "results may change after these merges" banner may
   be enough.

## 15. Phasing

1. **Core:** profile, config, projections, the snapshot store, diff, CLI
   (`refresh`, `list`, `show`, `profile`, `runs`), and detectors
   `salience_trend`, `type_trend`, `dormancy`, `communities`, `brokers`, plus
   the health detectors `kind_mismatch`, `vocabulary_sprawl`,
   `relationship_usage` and `open_conflicts`.
2. **Associations and identity:** `outcome_lift`, `same_as_candidates` with
   export, `split_signal`, `transitions`, `open_loops`,
   `attribution_suspects`, `fragments`.
3. **Narration:** circle naming and card sentences, with the cache and the
   post-check.
4. **Surfaces:** `query insights` plus the `artmind-query` section, the
   `artmind-insights` skill, the admin-ui Insights tab, and the Views tab.

Each phase lands with its tests, and with `CLAUDE.md`'s repo-layout table,
`justfile` recipes and the CLI group docstrings updated together.
