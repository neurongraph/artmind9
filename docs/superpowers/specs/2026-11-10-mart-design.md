# The mart: facts, dimensions and wholes over the corpus

Date: 2026-11-10. Status: **approved 2026-11-10** (storage details corrected to the structured store's real layout). Parent spec:
`2026-11-08-classic-baseline-and-skill-simplification-design.md` (work plan §0.1; this replaces item D's
"composites (episode)" framing). Related: `2026-11-10-topic-breadth-design.md` (topic plans, breadth over text);
`2026-11-07-structured-table-relationships-and-sql-retrieval-design.md` (on `master` / `sql-browser-enhancement`:
table relationships, semantic metadata and a stronger `text2sql`). Archived predecessor: the assembly-pass
episodes spec (`2026-11-06-assembly-pass-episodes-design.md`, tag `archive/p3-bundle`). Public checkout: counts,
ids and generic examples only; brain01 is named by question id, never by content.

## 1. Problem

Many questions ask about a **whole made of parts** that no single document states: a trip and its days, an
illness, a death and the rites that followed, a festival's days each year, every place visited in a country,
deaths among relatives. The graph has the parts (dated events with participants and places) but not the wholes,
and not the hierarchies that group parts: which country a town is in, which people are family, which event
types are "a trip".

Measured on brain01 (graph queries, no LLM):

- **Q09 (deaths of relatives):** events linked to each person, or naming them, within 30 days of a death recover
  the person's episode, including rites on later days that every answer but one missed. One rite is linked to its
  person only by name, not by an edge.
- **Q15 (trips with one family member):** grouping that member's events by consecutive days places all 9 trips in
  some group, but over-merges: a household member appears in 401 occurrent events, and day-chaining yields 246
  groups. A trip needs "away from home", which the graph cannot tell.
- **Q05 (places visited in one country):** the graph has no containment links between places; a theme search
  does not name the towns. With a place → country hierarchy the question is a roll-up.

Thinking in episodes alone was too narrow. The general shape is a **star schema**: facts (things that happen) keyed
to conformed dimensions (time, person, place, type) with hierarchies, aggregated along them; wholes are groupings
of facts.

## 2. Where the mart sits

| Derived artifact | Saves | Over | Fresh because |
|---|---|---|---|
| View | a query | graph entities | it reruns |
| Topic plan | a retrieval program | text (surveys) | it reruns |
| **Mart** | facts, dimensions, wholes | entities and observations | rebuilt from the graph and stored decisions |

The mart is corpus data: built in code from the graph plus curation records, rebuilt on every projection rebuild.
It is queried with SQL through the structured store. It is evidence (every row cites its observations and chunks),
unlike a topic page.

## 3. The corpora's shape (research, 2026-11-10)

**brain01** (`personal_journal`, 8,830 entities, 11 classes, 19,127 observations, all dated):

| Role | Class | Rows | What it carries |
|---|---|---|---|
| fact | EVENT | 2,656 | `type` 100 % (about 30 values; top: work_event, social_gathering, visit, routine_disruption, outing, conversation, celebration, health_incident, appointment); participants 62 % (+ `PARTICIPATES_IN` 4,362 edges); place 41 % (`OCCURS_AT` 1,106) |
| fact | ACTIVITY | 1,473 | `type` (work, leisure, creative, care, study…), `who_involved` 99 %, frequency |
| fact | STATE_CHANGE, HEALTH_INCIDENT, EMOTION | 72, 10, 312 | small or optional |
| dimension | PERSON | 1,085 | `relationship_to_diarist` 93 %, free text ("colleague", a kinship term in a local language, "relative (an uncle's wife)") |
| dimension | LOCATION | 986 | `place_type` 100 %; a parent place in `location` for 49 %; almost no place-to-place edges |
| dimension | ORGANIZATION, OBJECT | 422, 867 | `org_type` and relationship; `type` (vehicle, device, medication…) |

Every mention is an `:Observation` node (own `_valid_from`, `doc_id`, `chunk_id`, entity `key`, type, participants,
place, source sentence), aggregated into its entity by `AGGREGATES`. A recurring activity is one entity with many
dated observations: mention-grain facts need no projection change.

**banking_corpus** (6,744 entities, 67 classes, 96 % recurrent): rules and structure, not events
(POLICY_PROVISION 756, PROCESS_STEP 757, CONTROL_MEASURE 361, BUSINESS_RULE 280, COMPLIANCE_OBLIGATION 253,
METRIC_TARGET 241, ESCALATION_LEVEL 42); occurrent classes 269 in all. Its facts are **normative** ("provision X
applies to role Y for product Z and risk W, from date D, with threshold T"); its event facts are already SQL tables
in the structured store (complaints, CSAT scores, vulnerable customers). Dimensions: Role/Org (`reports_to`,
department), Product (`product_type`), Risk category, Version (document supersession, a slowly changing
dimension), and the domain family.

The same five dimension shapes (time, person/role, place/org unit, type, source/version) appear in both, with
different members.

## 4. Components

1. **Declaration** per domain: `<vault>/.artmind/domains/mart/<domain>.yaml` (versioned like views: a change
   without a `version` bump is refused, and so is a downgrade; committed; never seeded; checked by `domains
   validate`).
2. **Dimension enrichment**: `artmind mart enrich [--domain] [--dryRun]`, rules first and the LLM for the rest,
   writing curation records. The only step with LLM calls.
3. **Builder**: `artmind mart build [--domain] [--dryRun]`, no LLM: entities, observations, records and the
   declaration → tables in the structured store, plus a generated metadata file for `text2sql`.
4. **Query**: `db schema`, `db sql`, `text2sql` over the mart tables, citing evidence columns.
5. **Status and review**: `artmind mart status`, `artmind mart review`.
6. **Orient**: mart counts on the card, mart tables and coverage in peel 2.

Data flow: extraction → projection (graph) → enrichment (records) → build (SQL tables) → agent SQL.

No journal- or bank-specific knowledge is in code: everything vault-specific is in the declaration and records.

## 5. The declaration

brain01 sketch (values illustrative):

```yaml
format: 1
domain: personal_journal
version: 1
narrator: from_domain_config            # the "I" entity (artmind/domain_config.py)
home: {city: "<home city>"}             # places inside it are home, others away
facts:
  happening: [EVENT, STATE_CHANGE, HEALTH_INCIDENT]
  mention:   [EVENT, ACTIVITY, STATE_CHANGE, HEALTH_INCIDENT, EMOTION]
dimensions:
  time:   {calendar: true}
  person: {class: PERSON, seed: {property: relationship_to_diarist}, words: kinship/bengali_english}
  place:  {class: LOCATION, seed: {property: location, edges: [LOCATED_IN]}}
  type:   {classes: [EVENT, ACTIVITY], property: type,
           families: {trip: [visit, outing, travel, stay], health: [health_incident, appointment],
                      ritual: [religious_ceremony, milestone], work: [work_event, work],
                      social: [social_gathering, celebration]}}
wholes:
  trip:
    from: happening
    where: {type_family: [trip, social], place: away}
    join: {consecutive_days: 1, share: [participant, place]}
    alone_never_joins: [narrator, household]
  life_event:
    from: happening
    anchor: person
    match: [edge, name, participants]
    where: {type_family: [health, ritual]}
    window: {grows_while_gap_days: 21}
```

`words:` names a built-in word list shipped with artmind (kinship terms in English and common Indian languages,
extendable by a vault-local list under `domains/mart/words/`). Validation checks: classes exist in the domain's
schema, families list real type values (warning for unknown), wholes reference declared families and facts.

## 6. Dimensions and enrichment

### 6.1 Levels (the same in every domain)

| Dimension | Levels | Attributes |
|---|---|---|
| Time | date → week → month → quarter → year | `period` (named calendar period) |
| Person | group (self, household, family, friend, colleague, professional, acquaintance, other) → relation (parent, spouse, child, sibling, in-law, uncle, aunt, cousin, grandparent, …) → side (maternal, paternal, marital) | `employer` via `WORKS_AT` |
| Place | venue → locality → city → region → country | `home` / `away` from the home base |
| Type | value → family | — |

Banking's declaration maps Role/Org (role → department → executive → board), Product (product → type → line),
Risk (category → parent) and Version (document → version family, with effective periods) onto the same machinery.

### 6.2 Enrichment order (first source that decides wins)

1. **User records.**
2. **Declared rules:** type families; kinship words (an uncle on the mother's side, an aunt on the father's side,
   "-in-law"); exact relationship words ("colleague", "father").
3. **Graph seeds:** a place's `location` property or a `LOCATED_IN` edge gives its parent; `place_type: city` marks
   the city level.
4. **LLM** for the rest: the member's name, class, description and up to 3 source sentences; the reply gives level
   values from the declared vocabulary (anything else is rejected). Proposals below the domain's confidence
   threshold go to review.

No gazetteer in v1; the country level comes from the LLM (on brain01, about 60 city members).

### 6.3 Records (the durable source of truth)

`CURATION_DIR/mart_dimension/<id>.json` (`artmind/curation_records.py`, one JSON file per decision, committed),
holding: dimension, member key, level values,
source (`user` | `rule` | `seed` | `llm`), confidence, model, decided_at. `CURATION_DIR/mart_calendar/<id>.json` holds named
periods per year (from the user, or proposed by `enrich` from `celebration` events whose names carry a period
name, then reviewed). Records travel with the vault like `same_as.yaml`; a member merged by same-as follows its
canonical key. The mart tables are derived from them and can be dropped at any time; `projection rebuild` never
calls the LLM, it replays the records.

### 6.4 Review

`artmind mart review [--domain] [--dimension]` lists proposals below threshold and members with no record;
`approve`, `edit` (writes a `user` record) and `reject` work like the same-as queue.

## 7. Facts, bridges and wholes

Tables in the structured store as the store already works: one parquet file per table under
`STRUCTURED_DIR/<domain>/mart_<table>.parquet`, registered in the registry (`structured/registry.register_table`,
`source_file = "mart:<domain>"`) and exposed as the DuckDB view `<domain>__mart_<table>` by `ensure_views`. The
store has no DuckDB schemas; the `mart_` prefix is the namespace.

| Table | Grain | Columns beyond keys |
|---|---|---|
| `fact_happening` | one occurrent entity | name, class, date, type, type_family, place_key, home_away, outcome, first/last mention date, mention count |
| `fact_mention` | one observation of a declared class | entity_key, class, date, type, type_family, place_key, home_away, doc_id, chunk_id, source sentence |
| `bridge_person` | happening entity × person (joins both fact tables by `entity_key`) | person_key, role from the edge (participant, affected, witness, with) |
| `bridge_place` | happening entity × place (joins both fact tables by `entity_key`) | place_key, role (occurs at, visits, went to) |
| `dim_time`, `dim_person`, `dim_place`, `dim_type` | one member | level columns and attributes (§6.1) |
| `whole_<kind>` | one whole | whole_id, start, end, days, member count, places, countries, participants |
| `fact_whole` | whole × fact | kind, whole_id, entity_key, role |

**Evidence**: every fact row carries its observation ids, chunk ids and doc ids.

**Wholes** are computed by rule from the declaration, no LLM in v1:

- `trip`: facts of the listed families at away places, chained over consecutive days when they share a participant
  or place; the narrator or a household member alone never joins two facts.
- `life_event`: per anchor person, facts linked to them by edge, by name or by participants, of the listed
  families, chained while the gap stays within the window.
- `whole_id` = sha256 of kind + earliest member key. Dates come from members only. Roles: for a trip, the first and
  last days are `start` / `end`, the rest `during`; for a life event, health facts are `during`, and ritual facts
  after the last health fact are `aftermath`.

LLM confirmation of groups (the archived design's step) is left out until measurement (§11) shows rules fall short.

## 8. Build and freshness

- `mart build` writes every table's parquet to a temporary file, renames them all into place when every table is
  written, then upserts the registry rows, then writes the generated metadata
  (§9.1). It runs automatically at the end of `projection rebuild` and `vault sync` (after the passage sweep) for
  domains with a declaration. Tier-1 `--dryRun`: rows per table, members without a record (`run artmind mart
  enrich`), whole counts.
- The build records the catalogue generation it came from; an unmoved generation is a no-op.
- `mart status`: built vs current generation, enrichment coverage (places with a country, people with a group, type
  values with a family, by source), unreviewed proposals.
- `mart enrich` never runs on its own (LLM cost; text leaves the machine); `mart status` and orient name the gap.
- Both commands go in `WRITE_COMMANDS` as tier 1, with `test/test_dry_run_mart_*.py` asserting on the reads and SQL
  sent.

## 9. Query and the text2sql interface

### 9.1 Generated metadata

`mart build` writes `<vault>/.artmind/domains/metadata/<domain>.mart.meta.yaml` (rewritten on each build, never
hand-edited) in the format of the structured-table spec (Feature 2): the mart's relationships (every fact and
bridge key to its dimension or whole), column values with synonyms from the dimensions (place countries, person
groups with "relative" for family, type families with their member types), glossary entries ("a trip is a row of
`whole_trip`"; "count happenings in `fact_happening`, trends in `fact_mention`") and rules ("always select the
evidence columns"). The authored `<domain>.meta.yaml` stays the user's; artmind merges the two before `text2sql`
reads them (authored wins), unless `sql-browser` accepts several metadata files.

### 9.2 Whichever lands first

- **Mart first:** `db schema` / `db sql` and today's `text2sql` work on the mart tables; the generated metadata is
  data waiting for the enhancement.
- **Enhancement first:** the mart writes the enhancement's format exactly and is validated by its validator; mart
  tables are ordinary registered tables named `<domain>__mart_<table>`, which the enhancement introspects like any
  other (no second DuckDB schema). The enhancement's
  artmind layer (its Task B4, citations) also keeps the evidence columns, including under its automatic `LIMIT`.
- The two branches meet by merge (`master` / `sql-browser-enhancement` and `baseline/v0.11-classic`); the user
  decides when.

### 9.3 Agent surface

- `db list` marks mart tables; `db schema --domain d` shows them with the generated glossary.
- Orient peel 1 card: `mart: <n> facts; wholes: trip <n>, life_event <n>`. Peel 2: the mart tables, hierarchy levels
  with coverage, whole kinds with their rules in words.
- One line in the orient variant skill: a question about counts, sets or groups over people, places, types or
  periods goes to the mart through `db sql` / `text2sql`, citing the evidence columns.

## 10. Privacy and cost

`mart build` makes no LLM call. `mart enrich` sends member names, descriptions and up to 3 source sentences per
undecided member to the configured provider; on brain01 it runs only on the user's yes after a dry run with counts
and an estimated cost. Mart tables and records stay in the vault (`.artmind/data`, `.artmind/domains`); hand labels
for measurement stay in the vault's `benchmarking/` (private).

## 11. Testing and measurement

Hermetic tests (`test/`):

- enrichment rules decide without an LLM (kinship words, type families, `location` seeds); the LLM step's prompt and
  parsing; replies outside the declared vocabulary rejected;
- record replay is deterministic; a user record wins; a merged member follows its canonical key;
- the build over a synthetic graph (`test/_neo4j_recorder.py`) into an in-memory DuckDB: table shapes, evidence
  columns, atomic swap;
- wholes on synthetic facts: a frequent participant alone never joins a trip; a name-only member joins a life event;
  the window grows; roles by order and family;
- declaration validation, including the banking declaration; CLI conventions (tier-1 dry runs).

Live: every new Cypher and SQL runs against brain01 before a step is called done; banking's declaration validates.

Measurement (brain01; results in the vault, counts in the repo):

1. **Enrichment coverage** by dimension and source; review-queue size.
2. **Wholes against hand labels:** the life events behind Q09 and the trips behind Q15, labelled in the vault.
   Membership precision and recall; exact start and end days. Gate: precision ≥ 0.9.
3. **Answers:** Q05, Q09, Q15, Q02, Q20, Q25, Q26, Q11, Q16, Q17 rerun alone with the mart, graded with Sonnet
   against the baselines.
4. Then a full benchmark run in both vaults.

## 12. Order of work

1. Declaration format, validation, the banking declaration (validated only).
2. Time, Type and Person dimensions (rules only), records, `mart review`.
3. Place: seeds, then the LLM step (`mart enrich`, dry run first).
4. The build: facts, bridges, dimensions, generated metadata, `mart status`; hooks in `projection rebuild` and
   `vault sync`.
5. Wholes: trip and life event.
6. Orient, the skill line, `db list` marking.
7. Measurement items 1–3.

Each step is tested, run live and committed before the next.

## 13. Risks and open questions

- **Extraction caps recall:** a happening never extracted cannot join a whole; measurement reports it and the
  extraction wave (item G) fixes it.
- **Free-text relationships** beyond the word list fall to the LLM; cost grows with unusual people.
- **No gazetteer:** a wrong country from the LLM is corrected by a user record.
- **Grain double count:** one happening mentioned on two days is two `fact_mention` rows and one `fact_happening`
  row; the glossary says which to count.
- **Open:** whether banking's normative facts are one table or one per normative class.
- **Open:** whether wholes should later become graph nodes as well (for `entity-context`), or stay SQL-only.
