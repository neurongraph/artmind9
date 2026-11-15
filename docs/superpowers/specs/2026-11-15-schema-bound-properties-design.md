# Schema-bound properties

Status: design, 2026-11-15. Companion: `2026-11-15-temporal-model-design.md`,
which owns `temporal:` keys, date parsing and record time; §4's backfill and
§2.4's retime are shared with it.

## Goal

Make the properties step extract only what a domain's schema declares, keep the
schema's declared set short and justified by evidence, and let a property that a
question or mart turns out to need be added later and filled from the stored
passages, without a full re-extraction. Extraction then spends its effort on
entities, names, relationships and dates.

Four parts, each shippable on its own:

1. **Schema-only keys** (§2): no out-of-schema property is created or kept.
2. **Property audit** (§3): a per-domain report that proposes keep, drop or
   required for each declared key, from the staged extraction and the graph.
3. **Targeted backfill** (§4): `ingest backfill-properties` fills one declared
   property for one class from the stored chunks.
4. **Kept properties visible to the agent** (§5): brief entity rows carry the
   values of declared properties, within a budget.

## Non-goals

- Removing the properties step. A class with no declared property simply sends
  no entities to it (§2.3).
- Changing the entities or relationships steps, canonicalisation, or the
  document summary.
- Automatic schema edits. The audit proposes; the operator edits the schema.
- `ingest table2graph`. A table mapping already names its properties explicitly;
  it is out of scope and unchanged.
- Changing description synthesis (`projection synthesize`). §6 records what this
  design does to it.

## 1. Evidence

Measured on two vaults on 2026-11-15: brain01 (a personal journal, 19,160
observations) and banking_corpus (63 documents, 10,648 observations). The
scratch scripts are not part of this design; §3 turns them into a command.

### 1.1 Cost and sprawl

- The properties step is about a quarter of extraction prompt volume: in brain01,
  19.3M of roughly 60M prompt characters over 2,550 calls. Its stored values
  (2.1M characters) are as large as all the descriptions.
- Out-of-schema keys:
  - brain01 has 571 distinct keys against 81 declared, and 318 keys appear once;
  - banking_corpus has 856 keys against 447 declared;
  - undeclared keys are 11-35% of a brain01 class's values, and up to 33% in banking;
  - 88-94% of all values sit under declared keys.
- Overlap with the rest of the observation, by share of property characters:

  | | brain01 | banking |
  |---|---|---|
  | already in the description or context | 44% | 56% |
  | text only (a fact the description lacks) | 11% | 17% |
  | neither (the model's interpretation) | 44% | 26% |

- Fill rates split each class's declared keys into three groups:
  - **defining** keys, filled at least 95% (e.g. a class's type key, `date_or_time` on events, `what_it_measures`/`unit`/`value` on metrics);
  - **mid** keys, filled 10-90%;
  - **tail** keys, filled under 10%.

### 1.2 Curation load

- **Open projection conflicts:** 928 in brain01 and 1,639 in banking, nearly all on properties. Interpretive keys lead in both (`narrative_role`, `context_role`).
- **Blocked synthesis:** `projection synthesize` skips an entity with an open projection conflict. That blocks 22% of brain01's multi-observation entities and 78% of banking's.

### 1.3 What retrieval reads

- **Traces.** In the classic benchmark runs, answers come from `orient`, `survey`,
  `find`, `chunks` and `vector-text`, none of which reads entity properties.
  Graph patterns appear in a few turns and match on names, types and dates.
  `text2cypher` referenced specific properties in 17 brain01 queries, all of
  them declared keys.
- **Code.** Every property a consumer names is declared in its schema:
  - temporal keys (they set `_valid_from`);
  - mart seeds;
  - insights config;
  - saved views;
  - the hierarchy fallback.

  No code names an undeclared key.
- **Probe questions.** Fourteen questions aimed at mid properties (personality,
  emotion causes and resolutions, organisation sentiment, office atmosphere; and
  for banking approval limits, metric actuals, risk levels and failure paths)
  were run in both shapes:

  | | brief output | full output |
  |---|---|---|
  | brain01, 3 runs each | 1.3 correct of 8, 3 wrong | 2.3 correct of 8, 0 wrong |
  | banking, 1 run each | 3 correct of 6 | 3 correct of 6 |

  Banking answered from passages in both shapes. brain01's gain came from two properties:
  - `EMOTION.trigger`: the causes of an emotion across 1,700 entries in one list, more than passage search reaches.
  - `EMOTION.resolution`: turned a wrong "usually resolved" into "mostly unresolved".

  `personality_trait` was read in both shapes, by an agent that chose
  `pattern2 --shape full` on its own. Every other mid key (`sentiment`,
  `atmosphere`, `current_state`, `narrative_role`, `notable_characteristic`)
  added at most a few words and changed no grade.
- **Brief rows hide properties.** A brief `entity-context` or `pattern1`/`pattern2`
  row carries only `truncated_properties` (key names and counts), so the agent
  sees that a property exists but not its values.

### 1.4 What follows

A property earns its place when it is one of these:

- **defining**: it says what the entity is;
- **temporal**: it sets valid time;
- **depended on**: a mart, insights config, saved view or skill reference names it;
- **a cause or outcome**: it records why something happened or how it ended, in a way that answers a question across many documents (`trigger`, `resolution`, `outcome`, `failure_path`).

Interpretive labels (sentiment, narrative role, emotional valence, atmosphere)
fail all four and generate most of the conflicts.

## 2. Schema-only keys

### 2.1 Prompt

`prompt_templates.properties` in `artmind/domains/meta.yaml` drops "There is NO
fixed schema for properties beyond what is listed below. You decide what matters
based on the entity's role in the document." in favour of:

> Extract ONLY the properties listed for each entity's class below, using exactly
> those key names. Omit a property the passage does not state. Never add a key
> that is not listed.

`assemble_properties_prompt` (`artmind/prompt_builder.py`):

- **Per-class blocks:** renders each class's block as "For <CLASS>, extract:"
  (it currently says "consider:"), and marks a `required: true` key
  "(required when stated)".
- **Vocabulary:** drops the `PROPERTY_VOCABULARY` section, and with it the call
  to `canonicalize.retrieve_property_vocabulary`. That section exists to tame
  invented keys; with none possible it only costs prompt and a graph read.

### 2.2 Build-time filter

`ingest._build_observations` keeps only keys the observation's class declares,
after `normalize_property_keys` (so a `property_aliases` variant still lands on
its declared key). One new helper does the filtering:
`observations.declared_property_keys(schema) -> dict[class, set[key]]`.
`retime.retime_observations` applies the same filter, so §2.4 can prune staged
data. A dropped key is counted, never logged with its value, and the per-document
count reaches the ingestion log line.

`_strip_exclusive` stays as is. Under the filter it only matters for a key that
two classes declare and only one marks `exclusive`, which is its original case.

### 2.3 Classes with no declared property

An entity whose class declares no property is not sent to the properties step.
A chunk whose entities all belong to such classes makes no properties call; its
`kg_chunk_status` properties step is recorded `skipped`, as chunks-only domains
already do. This is where most of the cost saving comes from once §3 trims a
schema.

### 2.4 Existing data

`ingest retime --domain d` (no LLM) already rewrites staged observations and
commits only changed folders. With §2.2 it also drops undeclared keys, so one
retime per domain brings old staging in line. Its `effect` block gains a change
`{kind: "property_values_dropped", count, sample: [key names]}`, so the dry run
shows what would go. Values are not deleted outright: the removed values move
to `logs/retime-dropped/<domain>/<doc_key>.json` beside the run (private,
gitignored), so a mistaken trim can be recovered without re-extraction.

### 2.5 Schema guidance

Some schema guidance tells the model to put facts "in properties" under keys
the class does not declare. In shipped schemas:

- `contracts`: `reference_as_written`;
- `contracts`: "the row's figures" on rate-card rows;
- `banking.reference`: monthly values.

Each must become a declared key or the guidance must change, in the same change
as §2.2.

To keep this from coming back, `domains validate` scans `guidance:` and class
`description`/`guidance` text for `properties.<key>`. It flags a WARN when
`<key>` is not declared on a class the text is attached to.

### 2.6 Validation

`domains validate` (`schema_validate`):

- **ERROR:** a key named by any of the following is not declared on the class
  they name:
  - a mart declaration (`seed.property`, the `match` list where it names a property);
  - the domain's insights config (`outcome.property`, open-loop `status`/`urgency`);
  - a saved view's Cypher (`e.<key>` on a class the view scopes to).
- **ERROR:** `required:` is not a boolean.
- `temporal:` and `exclusive:` keys are declared by construction; nothing new.

## 3. Property audit

`artmind domains property-audit --domain d [--compact]`: read-only, no LLM, no
Neo4j unless `--graph`. One row per (class, declared key) plus one row per
(class, undeclared key) still present in staging:

| field | meaning |
|---|---|
| `class`, `key`, `declared` | the key, and whether the schema declares it |
| `fill` | share of the class's observations carrying the key |
| `in_description`, `text_only`, `inferred` | character shares, as §1.1 (word overlap with the observation's description/context, then with its chunk text) |
| `distinct_values` | distinct normalised values, capped count |
| `flags` | `temporal`, `exclusive`, `required`, `depended_on: [mart, insights, view:<name>, skill]` |
| `conflicts` | open projection conflicts on the key (`--graph` only) |
| `proposal` | `required`, `keep`, `drop`, or `review` |
| `reason` | the rule that set the proposal |

Proposal rules, first match wins:

1. The key is undeclared → `drop`.
2. The key is `temporal` or `depended_on` → `keep`.
3. `fill` ≥ 0.95 → `required`.
4. `inferred` ≥ 0.6 and `text_only` < 0.1 → `drop` ("mostly the model's interpretation").
5. `fill` < 0.10 → `drop` ("rarely stated").
6. Otherwise → `review`.

Thresholds are module constants and appear in the payload's `rules` block.

The audit lists no values: key names, counts and shares only. Its `next` is
`artmind domains validate`. Once the operator has edited the schema, the
follow-up is `artmind ingest retime --domain d --dryRun` to see what the trim
drops (§2.4).

Rule 4 would drop `trigger` and `resolution` in brain01 (their `inferred` share
is high). That is why the proposal is a proposal: the probe evidence (§1.3)
keeps them, and the operator records that by marking the key `required` or by
adding a `keep_reason:` string to its declaration. The audit then reports
`keep` with that reason. `keep_reason` is the schema's place for evidence the
audit cannot measure.

## 4. Targeted backfill

```
artmind ingest backfill-properties --domain d --entityClass C --property p[,p2]
    [--match GLOB] [--dryRun] [--compact]
```

For a property newly declared on class C (or re-declared after a trim), fill it
from the stored chunks without re-running entities, relationships,
canonicalisation or summaries.

- **Scope:** the staging folders of domain `d` (optionally `--match`), and in
  each, the chunks that have at least one observation of class C.
- **Prompt:** the properties prompt (§2.1) restricted to class C and the named
  properties, with the document's date anchor (`temporal.relative_anchor`) when
  any of them is `temporal:`. It lists only the chunk's existing C entities, by
  canonical name.
- **Write:** each returned value is merged into that observation in the staged
  `observations.json`; other keys are untouched, and names and ids never change.
  Changed folders are committed through `commit_to_graph` with the rebuild
  deferred, then one projection rebuild, as `retime` does. Each commit bumps
  `catalogue.state`.
- **Cache:** per chunk, keyed by chunk text sha + class + sorted property names +
  property-prompt version + model, under `logs/backfill-cache/`. A re-run after
  a failure resumes.
- **Tier 1 `--dryRun`** (`WRITE_COMMANDS`, `test/test_dry_run_backfill_properties.py`):
  reads staging only and returns `effect` with:
  - `changes: [{kind: "observations", count}]`;
  - `llm: {calls: <chunks>, model, est_cost_usd}`;
  - `reversible: true`, with `undo` naming the stash under `logs/backfill-stash/`.
- **Privacy:** each chunk's text is sent to the configured provider once. The
  help text and the dry-run `summary` say so.
- **Marts:** a new mart dimension that seeds from a property follows the same
  path: declare the key, backfill it, then `mart build`. `mart` docs gain that
  sequence.

`retime --redoProperties` stays for the temporal case it serves today; it
becomes `backfill-properties` over each dated class's `temporal:` keys and is
kept as an alias path with the same output.

## 5. Brief rows carry declared property values

`brief.entity_line` gains `properties`: the entity's declared, non-temporal
property values, in schema order, `required` keys first. The budget is 600
characters per row: a list value keeps its newest items, the rest are dropped,
and anything cut is named in `truncated_properties` as today. Undeclared keys are
never shown (after §2.4 there are none).

This reaches every brief caller:

- `entity-context`: the row's `entity`;
- `pattern1`, `pattern2`, `pattern7`, `pattern8`: `pattern_row`.

Graph-brief size tests (`test_graph_brief.py`) gain a hub case with a full
property bag and assert the 24,000-character agent budget still holds.

The `artmind-query` skill keeps passing `--shape brief`. With §5 the brief row
shows what full shape showed for the properties that matter. Switching the skill
to full shape instead was measured and rejected:

- In the brain01 probe runs, 14 of 21 full-shape entity calls overran the
  24,000-character output budget and were cut behind a handle (full `pattern1`
  averaged 162,000 characters).
- Brief entity-context calls were never cut (0 of 20).
- Turns took 40% longer: 68 s against 48 s on average.

## 6. Description synthesis

- **Prompt:** `projection synthesize` lists an entity's properties in its prompt
  (`synthesize.build_synthesis_prompt`). After §2 that list holds declared keys
  only. Measured on existing syntheses, 13% of their words came only from
  properties.
- **Conflicts:** fewer properties means fewer projection conflicts, and so fewer
  entities skipped for an open conflict (78% of banking's multi-observation
  entities today).
- **Not changed here:** skipping on a conflict over a non-required key is a
  separate change. It is noted as follow-up F2.

## 7. Rollout

1. §2.5 guidance fixes in the shipped schemas, and §2.6 validation.
2. §2.1-§2.3 prompt and filter.
3. §5 brief rows.
4. §3 audit.
5. §4 backfill, with `retime --redoProperties` moved onto it.

Per vault:

1. Run `artmind domains property-audit`, and edit the schema (through `init --force`
   for shipped schemas, by hand for a vault's own).
2. Run `ingest retime --dryRun`, then `ingest retime`, to drop undeclared and
   trimmed keys from staging and the graph.
3. Backfill any newly required key.
4. Run `projection rebuild`.

No re-extraction is needed to adopt §2.

## 8. Acceptance

- **Extraction:**
  - On a re-extracted sample (150 brain01 entries, all 63 banking documents),
    properties-step prompt characters fall by at least 50%.
  - No staged observation carries an undeclared key.
  - The property-dated share of observations stays within 1% of today's.
- **Answers:**
  - The classic benchmark suites on brain01 and banking_corpus show no drop in
    grade bands (`traces compare --grade`, 3 runs per side).
  - The mid-property probe sets (`<vault>/benchmarking/questions_mid_properties.md`)
    with brief output match today's full-output result on M03-M05 (brain01).
- **Downstream:**
  - `mart status` coverage on brain01 is unchanged.
  - `insights config check` passes.
  - Open projection conflicts fall.

## 9. Follow-ups

- **F1.** Define `required:` semantics for the entities step: whether a missing
  required key should mark the chunk for backfill.
- **F2.** Synthesis: do not skip an entity whose open conflicts are all on
  non-required keys.
- **F3.** Fold the defining keys of a class into the entities step (one field
  per entity), so classes whose only declared keys are defining or temporal
  make no properties call at all.
