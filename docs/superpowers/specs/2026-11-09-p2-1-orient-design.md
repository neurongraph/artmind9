# P2-1 `artmind query orient`: design

Date: 2026-11-09. Status: **design agreed in brainstorming, written for review**. Parent spec:
`2026-11-08-classic-baseline-and-skill-simplification-design.md` §4.2 (this document refines it; where they
differ, this one wins for P2-1). Public checkout: counts and generic examples only.

## 1. Framing: four parts, and who does what

Answering a question involves four parts:

1. **The agent**: typically a mid-frontier LLM (Sonnet, DeepSeek class).
2. **The skill**: artmind's key instruction set (`artmind-query`).
3. **The question**: it defines the scope of the exploration.
4. **The orientation data**: a layered description of the knowledge corpus.

The orientation data is **unpeeled by the agent, with the skill's help, using the question as the driver**.
`orient` is therefore not one fact sheet sized to a budget. It is a set of layers the agent opens on demand,
and each layer names what lies one level below it.

| Part | Does | Never does |
|---|---|---|
| Question | Sets the scope: which areas, and what kind of answer (a fact, a set, a history, a comparison) | — |
| Skill | Teaches how to read each layer, and which question signals mean "peel further here" | Restate facts the layers carry |
| Agent | Matches the question against the layer it holds; decides to peel, retrieve or stop | Guess structure it has not seen |
| Orientation | States facts at the depth asked for, and names what lies one level down | See the question, rank or route |

Consequences:

- **No question input** (parent spec §4.2 rule 1). The question decides *which* peel the agent asks for, never
  *what* `orient` computes. The same call gives the same output for every question.
- **Predictable for a mid-frontier model**: structured JSON only (no markdown rendering), a fixed shape per
  layer, and one small decision per step.
- **Facts, not a plan**: a peel may say what it cut and which existing command shows the rest ("19 classes, top
  8 shown; the rest via `entity-listing`"). It never suggests a command because of a question.
- Supersession, versioning and similar concerns are not special cases. They appear as instances of general
  layers (here, the domain's time model).

## 2. Layers and peels

### 2.1 Layers (what the corpus description holds)

| Layer | Holds |
|---|---|
| L0 corpus (meta-meta) | vault, generation and built-at (freshness), the domain tree by family, totals (documents, entities, tables, time span) |
| L1 domain identity (meta) | purpose (from the schema's `description` / `subject`), the unit of knowledge (what one document is, naming pattern), the voice (narrator or none), the time model (what dates a document, versions and successors, documents per period), kind and stores |
| L2 domain contents (metadata) | classes with counts and a short definition; relation types as `CLASS -REL-> CLASS` with counts; stores, and for structured: tables and bridge classes |
| L3 already answered (assets) | views (name, summary, params), topic pages, latest insights snapshot date, summaries present or missing |
| L4 trust (health) | open conflicts, twins, drift, documents behind the vault, missing summaries; counts only |

### 2.2 Peels (how the agent opens them) — decided: two peels by scope

1. `artmind query orient`: L0 plus one card per domain drawn from L1 (with asset counts). Small by
   construction; families collapse past the budget. Enough to choose the domain set and see whether a view or a
   chunks-only rule applies.
2. `artmind query orient --domain d[,d2]`: L2–L4 for those domains. Enough to choose a command and spell its
   arguments.
3. Below that: existing commands (`views show`, `entity-listing`, `structural-metadata`, `query catalogue`),
   named by peel 2 per part where it cut.

Rejected: peeling by layer (`--layer time|classes|...`) — more calls and more choices for the agent; a mix of
both — two ways to do one thing.

Skill rule this allows: "orient once; then orient the domains the question touches."

## 3. Peel 1: the domain card (agreed)

Example (`banking.policy`, public sample corpus):

```json
{
  "domain": "banking.policy",
  "family": "banking", "siblings": 7,
  "purpose": "Formal policy documents for FirstUK Bank covering AML/CFT, KYC, fraud, ...",
  "kind": "graph", "stores": ["graph", "structured"],
  "unit": {"documents": 8, "pattern": null, "example": "policy_aml.md"},
  "voice": null,
  "time": {
    "rule": {"valid_from": "document field 'Effective Date'", "version": "document field 'Version'"},
    "dated": {"from_content": 8, "defaulted_to_ingestion": 0},
    "span": ["2026-01-15", "2026-06-01"],
    "versions": {"with_successor": 2, "retired": 1}
  },
  "vocabulary": ["POLICY_PROVISION", "CONTROL_MEASURE", "RISK_CATEGORY", "REGULATORY_REFERENCE", "POLICY"],
  "entities": 1907,
  "assets": {"views": 0, "topic_pages": 12, "insights": null, "summaries": "all"}
}
```

About 550 characters per card; banking's nine domains fit the budget without collapse.

| Field | Source | Notes |
|---|---|---|
| family, siblings, kind, stores, entities | catalogue `stores` / `names`, the domain tree | exists |
| purpose | schema `description`, cut at a sentence boundary under ~200 characters | exists in the schema, shown nowhere today |
| unit | `documents`: count; `pattern`: **declared only** (schema `temporal.document_patterns` filename rule), else null; `example`: one document name | never inferred from names: a wrong guess misleads |
| voice | narrator from `domains/plan/<domain>.yaml` | the loader moves into `artmind/domain_config.py` now (planned for P2-3; needed here) |
| time.rule | schema `temporal.document` / `document_patterns` | declared, not derived. Dates belong to documents; the domain carries only how they are dated |
| time.dated | count of documents dated from content vs defaulted to ingestion date | tells the agent whether date filters are trustworthy here |
| time.span | earliest/latest `valid_from` over **content-dated documents only** | the catalogue `time` part today counts ingestion-defaulted dates as dated; `orient` excludes them as `insights/profile.py` does. **Kept provisionally; an optimisation point** — if variant results go south, try dropping it from peel 1 (span then only in peel 2) |
| time.versions | counts of documents with a successor and of retired documents | **new**: catalogue document records do not carry `superseded_by` today. Which documents and their dates are peel-2 / `structural-metadata` facts |
| vocabulary | top 5 class names by entity count | a deliberate preview of L2: the fastest signal for choosing among overlapping siblings |
| assets | catalogue `assets` | counts and the insights snapshot date |

### 3.1 Card addition: `coverage`

`coverage: {behind_vault: N, summaries_missing: N}` — present only when a value is above zero. These are the
two health facts that change what the agent should believe ("not in the graph yet", "survey is weaker here").

## 4. Peel 2: `orient --domain d[,d2]` (caps provisional, §5.1)

| Layer | Contents | Cap / cut |
|---|---|---|
| L2 classes | name, count, kind, definition, measured types, dated share, schema `relates_to` (§4.1) | all up to 25; past that name + count only |
| L2 relations | `FROM -REL-> TO` with counts, **measured from the data** (decided: the catalogue relations part gains endpoint classes) | top 20, then `{shown, total, rest: "artmind query graph metadata"}` |
| L2 stores | structured: tables, grain, bridge classes (what `db bridge` returns) | 10 tables, then names |
| L3 views | name, summary, params (name, type, required), 2 examples; `presentation_format` / `skill_md` stay in `views show` | 8 full, then name + summary |
| L3 other assets | topic page titles, insights snapshot date + detector names, summaries state | 10 titles, then a count |
| documents | only what the card omits: retired / superseded documents (name, `valid_from`, successor) | 20, then count + `structural-metadata` |

Budget ~8,000 characters per domain, ~20,000 per call. Cut order when over: smallest classes' definitions → types past 5 → view examples →
relations past 10 → topic titles. Every cut is listed in a `cuts` block.

**L4 health is dropped as a layer** (decided). Most health counts are operator work the agent cannot act on;
conflicts are handled per entity by Adjudicate / `graph conflicts`, twins by `entity-resolve` (P2-3); health is
not built on this call's path, so the layer would often be absent and make the shape unstable. The two facts
that matter go on the card (§3.1). The budget goes to class definitions and types (§4.1).

### 4.1 Classes: definitions and types, not properties (decided)

Properties come in two kinds: the few that help *find* entities, and the many that *describe* one once found.
The descriptive ones are `entity-context`'s job and are left out of orient. The findable ones are facts about
the class, so peel 2 spends its budget one level up, on the class:

Illustrative (type values from the packaged journal schema's `type_examples`; counts invented):

```json
{"class": "EVENT", "count": 2648, "kind": "occurrent",
 "definition": "<the schema description, whole>",
 "types": [["social_gathering", 400], ["appointment", 300], "..."],
 "dated": 0.99,
 "relates_to": {"LOCATION": ["occurs_at"], "EMOTION": ["evokes", "results_in"]}}
```

- `definition`: the whole schema description (it carries scope rules such as "not routine background actions").
- `types`: **measured** from the graph (decided): `type` values lower-cased and trimmed, with counts, top 8,
  each value on ≥ 2 entities; then `{shown, distinct, rest}`. Measured spread (2026-11-09): 5–54 distinct values
  per class, but values held by ≥ 3 entities cover 79–100 % of each class's entities, so the top 8 carry the
  class; the long tail is singletons.
- `dated`: share of the class's entities with their own date property (`date_or_time` / declared temporal key);
  decides entity-date vs document-date filtering. The one property fact kept.
- `relates_to`: as declared in the schema; the measured relations sit beside it at domain level (§4).
- Classes with no entities are omitted; a class with entities but no schema entry appears with `definition:
  null`.

~450 characters per class. Cut order inside a domain: definitions of the smallest classes, then `types` past 5.

<details><summary>Evidence behind dropping properties</summary>

### Property names — declared (schema) vs measured (graph)

Check of both vaults (2026-11-09, read-only; `UNWIND keys(e)` per domain over `:Entity` node properties, where
the projection writes them and where `metadata`, `entity-context`, patterns and text2cypher read them):

| | banking (8 graph domains) | brain01 `personal_journal` |
|---|---|---|
| declared keys present on ≥1 entity | 41–91 % per domain | 52 of 52 |
| declared keys < 5 % fill, populated classes | 5–35 per domain | 1 |
| measured keys not declared | 12–323 per domain, about half on a single entity | 758, of which 411 on a single entity, 76 at ≥ 5 % |
| useful undeclared keys | `context_role` (.17–.27), `trigger_condition` | `narrative_role` (.25–.46 on 8 classes), `location` (.25–.41), `emotional_valence` |
| classes in graph, not in schema | 2 (1 entity each) | 1 (10 entities) |
| query time | 15–130 ms per domain | ~120 ms |

`property_aliases` are folded (no alias source key survives). Neither source alone is right: declared names miss
dead keys and real undeclared ones; measured keys are half noise. The first proposal (declared names with fill
rate, plus undeclared keys above a threshold) was dropped in favour of §4.1: descriptive properties belong to
`entity-context`, not orientation.

</details>

## 5. Budgets, honesty, freshness (defaults; checked against real sizes in a live task)

### 5.1 Budgets and cuts

- Peel 1: ~8,000 characters for the whole call. Over budget, a family collapses to one row (parent, child
  count, summed documents and entities, union of stores, child names) and `orient --domain <parent>` expands
  it. Largest families collapse first.
- Peel 2: ~8,000 per domain, ~20,000 per call. Cut order: smallest classes' definitions → types past 5 → view
  examples → relations past 10 → topic titles → documents past 10.
- Every cut appears in `cuts: [{part, domain, shown, total, rest}]`, where `rest` is the existing command that
  shows the remainder. Nothing is dropped without a `cuts` entry. Character budgets apply to compact JSON.
- `orient`'s budget is its own: it is fitted before `_echo_json`, so `ARTMIND_OUTPUT_BUDGET` never stores an
  orient payload behind a handle.

### 5.2 Honest parts

- Each card / domain block carries only parts that were read. A part that failed (Neo4j error, unreadable
  schema, view store failure) is absent and named in `warnings` ("banking.policy: views unreadable: ..."); it is
  never `0`, `[]` or `null`.
- `null` is reserved for "declared absent" (`voice: null` = no narrator configured; `pattern: null` = no
  declared filename rule). The help text says so.
- Neo4j unreachable: peel 1 still returns schema-only facts (purpose, time rule, voice, declared stores) with a
  warning, and exits 0; a registry failure exits 3 per CLI conventions only if no card can be built.

### 5.3 Freshness and cost

- All graph facts come through `artmind/catalogue/` (memory + `logs/plan-catalog/` cache, fingerprint =
  generation + disk stamp). New or extended parts: `relations` gains endpoint classes; `types` (per class: top
  values, distinct count, dated share); `documents` records gain `superseded_by` / retired. `CATALOGUE_FORMAT`
  bumps to 4 (older cache files are discarded, as today).
- `health` is never built here. `coverage` (§3.1) reads `behind_vault` / `summaries_missing` only when the
  health part is already cached; otherwise the field is absent and `warnings` says "coverage not computed".
- Schema facts (purpose, definitions, time rule, kind, `relates_to`) are read from the run folder's schema files
  on each call (cheap; `stamps` already covers them).
- Read-only; proxied by `serve`; traced as its own span layer (`orient`); envelope per CLI conventions;
  `domains-overview` unchanged.
- The narrator loader (`query_plan/config.py`) and `is_domain_name` move to `artmind/domain_config.py`;
  `catalogue/cache.py` drops its planner import and the entanglement seam list shrinks by one.

## 6. Skill text and measurement (decided)

### 6.1 The variant `artmind-query-orient`

A copy of `artmind/skills/artmind-query/` under `artmind/skills/artmind-query-orient/`, with this text deleted:

- "The first two commands, every time";
- "Exception: chunks-only domains" (becomes one line: a `chunks-only` card rules out graph commands; use
  `vector-text` / `survey`);
- Route, including the routing sub-agent;
- the listing half of Views (`views list`); matching, `views show` and `views run` stay;
- Store routing's `db bridge` call (peel 2 carries tables and bridge classes);
- Discover's openers: `graph metadata`, `entity-listing --countAll` and the usual `structural-metadata`
  (decided: dropped too; peel 2 carries classes, counts, types, relation endpoints and documents). Their
  commands stay available and are named in `cuts.rest` when a peel cuts.

About 120 lines go; about 25 come in ("Orient — read the corpus first"):

1. `artmind query orient --compact` once per conversation.
2. Pick domains from each card's `purpose`, `vocabulary` and `family`; a family row expands with `--domain`.
3. `artmind query orient --domain <d> [--domain <d2>] --compact` for those domains.
4. A view that fits → `views show` / `views run`. Otherwise choose the command from classes (definition, types,
   `dated`), relation endpoints and stores, and spell its arguments from them.
5. Orient again only when the user moves to a new area. A part named in `warnings` was not read: do not treat
   it as empty.

The description line in the skill frontmatter changes from "always checks views first" to "always orients
first".

### 6.2 Measurement (parent spec §3.4)

Before the gate: re-grade every baseline run against the current tiers (only changed questions cost a call),
and re-judge one baseline pair with `claude-sonnet-5-5`; if the band narrows, every gate uses that model.

One run per vault with Skill variant `artmind-query-orient` (e.g. `H` banking, `H1` brain01), admin consoles
restarted first, judged against every baseline run with the same judge model. Passes when:

- the must-item rate per vault and question group is at or above the lowest baseline run's;
- the balance against every baseline run is within the band;
- no must item is lost that every baseline run found;
- no question is materially worse than all baselines without a dossier; the user reads the flagged answers.

Reported, not gated: commands per turn (baseline median 4–5), orientation calls and characters per turn, latency,
cost. `traces misses --run <id>` for both runs; scope misses are the ones to watch.

Not gated but checked live: `orient` and `orient --domain` sizes in both vaults (and in a many-domain vault such
as `my_work`) against §5.1 budgets.

## 7. Non-goals for P2-1

- No question input (P2-1b, only by measurement).
- No `next` hints (P2-2), no superseded-history route (P2-2b), no twin grouping (P2-3).
- No change to `domains-overview` or the plugin.
- Composites appear in orient only after the composite step.
