# Temporal model: dated extraction, valid intervals and record time

Status: design, 2026-11-15; revised 2026-11-17 (one-time changes to existing
data and vault files moved into a migration pack, §8). Companion to
`2026-11-15-schema-bound-properties-design.md` (shared rollout, §9).

## Goal

Make every date artmind stores mean one thing, keep the detail extraction
already produces (ends of ranges, precision, time of day), make **dated
happenings** (events and activities) the place a question's dates come from,
date the units retrieval returns (chunks, passages, summaries), and add record
time so the graph can say both *when a fact was true* and *when artmind knew
it*. That is bi-temporality.

Relationships are deliberately **not** a date source (§1.6): a happening is.
Which classes are happenings, and how same-as treats each class, is declared
per class in the schema (`identity`, §5.3).

## Non-goals

- Timezones on valid time. Valid time stays a calendar date; a time of day is
  kept as an optional field, never in comparisons.
- A general temporal query language. The design adds fields and fixes the
  existing filters; it adds one flag (`--knownAsOf`, §6).
- Rewriting the structured store's SCD2 model, which is already bi-temporal for
  tables (§7.3 only carries its end dates into the graph).
- Dating relationships. Their dates repeat the happening's or the document's
  (§1.6); they stay as extracted provenance on `ASSERTS_RELATION`, unread.
- Passage-side recall for questions that span many years (F1). §5 gives those
  questions a graph route instead.

## 1. Evidence

Measured on brain01 (personal journal, 1,711 entries) and banking_corpus
(63 documents) on 2026-11-15, plus a code inventory of every date field.

### 1.1 One axis, mostly right

artmind stores valid time only, at two levels: the document's date
(`_doc_valid_from`) and the fact's own date (`_valid_from`, taken from a
property the schema tags `temporal:`).

| | brain01 | banking |
|---|---|---|
| documents dated by | filename (1,711) | header label (63) |
| observations dated from a property | 5,113 of 19,160 | 237 of 10,648 |
| events dated from a property | 98% | 0-75%, by class |
| property date differs from the document date | 89 | 76 |

Where the property date differs, it is a real correction: a planned event, a
past event recalled, a deadline.

### 1.2 Detail that is lost

`temporal.parse_iso` keeps the first date in a value and drops the rest:

| stored value | result |
|---|---|
| `2023-09-07 to 2023-09-08 (multi-day)` | `2023-09-07`; the end is lost |
| `2022-10-28 (evening)`, `2026-07-02 09:18 UTC` | the date; the time is lost |
| `2026-06 (planned)`, `2026-Q1`, `2026 (coming)`, `March 2026` | none: the fact falls back to the document date, silently |

The extraction prompt asks for `YYYY-MM-DD` followed by the original wording,
so this detail is produced and then discarded. Counts:

- brain01: 2,407 values carry a qualifier.
- Ranges: 13 in brain01, and banking deadlines given as quarters or months.

### 1.3 What carries no date

- **Ends of validity.** `_valid_to` is set on no document and no observation in
  either vault.
  - A superseded document gets an unprefixed `valid_to` from its `SUPERSEDES`
    edge, which leaves two names for one idea.
  - Retired and superseded facts keep an open end.
  - SCD2 table rows close with `_valid_to`, but `table2graph` passes only
    `_valid_from` to the graph.
- **Retrieval units.** Chunks, passages and document summaries carry no date
  (0 of 2,068 chunks, 0 of 2,147 passages, 0 of 3,779 summaries in brain01).
  - `vector-text --asOf` and `chunks --asOf` therefore filter nothing.
  - `summary_store.summary_rows` reads the unprefixed `valid_from` from a
    `document.json` that holds `_valid_from`, so even summaries meant to be dated
    are not.
- **Relationships.** The relationship step writes a date on 23% of brain01's
  assertions (`ASSERTS_RELATION.date`); `RELATES_TO` keeps none. §1.6 shows the
  happenings already hold those dates.
- **Record time.**
  - Content nodes carry no record time: observations, entities, edges,
    projection conflicts and chunks.
  - The `:Observation` → `:ObservationHistory` relabel stamps nothing, and a
    revived observation overwrites its state.
  - Supersession and lifecycle records have no timestamp (`effective: null` in
    practice).
  - `Document.ingested_at` keeps only the latest extraction.
  - The only record of what the graph said before is the vault's git history.

### 1.4 Questions

Dated questions in the classic benchmark traces:

- **"When did X" questions** are often right when a single entry holds the
  answer (one question was correct 21 of 21 times).
- **Questions that span years** ("every year", "over the years", "how many
  times") were correct 3 of about 130 times in brain01. Turns that ran
  `survey --by year` did no better (0 of 63).
- **The cause is recall per period, not dates.** A by-year survey for one
  festival surfaced 2 entries for a year in which the journal has 5 such entries.

So dates are present for the documents those questions need. This spec fixes
the date model; F1 covers recall per period.

### 1.5 Code defects found on the way

- **Year-only `--asOf`.** `--asOf 2026` drops every 2026 date, because
  `'2026-05-01' <= '2026'` is false under text comparison.
- **Unparsed values reach the graph.** A raw frontmatter or label value can land
  in `_valid_from` unparsed, and then sorts wrongly.
- **Default date treated as real.** The `default_ingestion` date (today) is
  counted as a real date by survey, timeline, mart and the projection winner.
  Only the anchor, catalogue and insights exclude it.
- **Mixed clocks.** `update.py` writes its record timestamp (`created_at`) in local
  time, and the `ingestion_date` default is a UTC date while `update.py`'s document
  date is local. The rule (decided 2026-11-17): query time is local, record time is
  UTC, document time is local.
- **Winner and displayed date disagree.** The projection winner is chosen on
  `_doc_valid_from` but shows the fact's `_valid_from`, so a later fact stated in
  an older document can never win.
- **Wrong skill text.** The `artmind-query` temporal reference calls
  `entity-history --asOf` "a genuine point-in-time" filter. It is a valid-time
  floor that ignores `_valid_to`.

### 1.6 Happenings carry the dates; the model hides them

brain01 (journal):

- **Happenings are extracted and dated.**
  - 2,659 EVENT entities, 99% dated by their own property;
  - 1,477 ACTIVITY entities, 95% dated by their own property;
  - 340 of 2,068 chunks have no event or activity.
- **Relationship dates add almost nothing.** Of 8,757 dated relationship
  instances:
  - 3,486 attach to an event or activity, which holds the same date;
  - of the 5,271 that do not (mainly person→person `SPENDS_TIME_WITH` and
    person→place `WENT_TO`/`VISITS`), 262 carry a date other than the entry's.

  That is 3% of dated relationships with information beyond the happenings and
  the document date.
- **Three modelling choices hide the happenings' dates:**
  1. **ACTIVITY is `kind: recurrent`, so its dates fold.** Its schema calls it "a
     recurring or single purposeful activity". 286 multi-mention activities
     fold 1,078 dated days into entities that show one date (one activity
     holds 18 different days). The days survive only on observations, reached
     through `entity-history`, which the agent does not use for this.
  2. **`kind` does not decide identity.** The aggregate key is name + class +
     domain for both kinds (`observations.aggregate_key`). Making ACTIVITY
     occurrent alone would keep the 18 mentions as one entity and turn its 18
     dates into 17 conflicts.
  3. **Dates sit in event names.** 2,011 of 2,659 EVENT names contain a date
     ("<happening> - 2024-03-30"). So each mention is its own entity (2,545
     events have one observation), and one recurring happening is spread over
     many entities (one sport over 3, one festival over 19, another over 13)
     with nothing joining them but a name search.
- **`graph timeline` sees only occurrent classes.** For one festival window it
  returned 4 of 6 dated happenings. It missed an ACTIVITY, and an EVENT whose
  name does not mention the festival.
- **The route exists but is unused.**
  - Two cross-year festival questions ran about 55 times, never through events,
    always partial.
  - A name-and-description match over event/activity observations reaches 41
    and 28 dated days for those two festivals, against 20 and 15 journal
    entries that name them. The graph has recall to spare; it needs grouping
    and noise control, which a per-year listing gives the agent.
- **Banking's happenings are often undated.** TRANSACTION (83) and SAR (24)
  declare no date property, and ACTION_ITEM's date (32 of 149) is a deadline.
  §4 covers both.

## 2. The model

Two axes, named once and used everywhere:

| axis | question it answers | fields |
|---|---|---|
| **valid time** | when was this true in the world? | `_valid_from`, `_valid_to`, `_valid_precision`, `_valid_time_of_day`, `_valid_time_source` |
| **record time** | when did artmind hold it? | `_recorded_at`, `_retired_at`, `_retired_by` |

Rules:

- **Valid time is a date.**
  - `_valid_from` / `_valid_to` are ISO dates, always written by one function
    (§3.1), so text comparison is correct.
  - `_valid_to` is exclusive.
  - A partial date is stored as its first day, with `_valid_precision` recording
    how much was stated: `day`, `month`, `quarter` or `year`. `2026-Q1` is stored
    as `2026-01-01`, precision `quarter`, with an implied end of `2026-04-01`.
- **Time of day is kept, not compared.** `_valid_time_of_day` holds the time or
  qualifier text the source gave, such as `09:18 UTC` or `evening`.
- **Record time is a UTC timestamp.**
  - `_recorded_at` is set when a node or edge is first written for a document
    version.
  - `_retired_at` is set when it leaves the current graph (relabelled History,
    retired, superseded). It is the time artmind stopped holding it, not the
    time it stopped being true.
  - `_retired_by` names why: `version:<n>`, `retire`, `supersede:<record id>`,
    or `retract:<observation id>`. A same-as fold relabels nothing (members stay
    current), so it has no reason of its own.
- **The document's date stays distinct.** `_doc_valid_from` is kept on
  observations. The unprefixed `valid_from` / `valid_to` on documents are folded
  into the prefixed names (§7.1).
- **The default date is not a date.** A `default_ingestion` date still sets
  `_valid_from` (sorting needs a value), but `_valid_precision` is `none`. Every
  date filter treats it as undated unless asked otherwise (§6).

## 3. Extraction

### 3.1 One parser: `temporal.parse_valid`

`parse_valid(value) -> ValidTime | None`, with
`ValidTime(start, end, precision, time_of_day)`, replaces `parse_iso` for every
write path. `parse_iso` stays, as a thin wrapper returning `start`.

- **Full date:** `start`, precision `day`.
- **Range:** `A to B`, `A - B`, `A – B`, `A/B` (where `B` may be a day only, e.g.
  `2026-01-27/28`) and `from A until B` give `start = A`, `end = B + 1 day`.
  The precision is that of the coarser end.
- **Partial dates:** `YYYY`, `YYYY-MM`, `YYYY-Qn`, `Qn YYYY`, `Month YYYY` and
  `H1/H2 YYYY` set `start` to the first day and `precision` accordingly.
- **Time and qualifier:** a time (`HH:MM`, optionally with a zone) or a
  parenthesised or trailing qualifier goes to `time_of_day` (≤ 40 characters).
  It never changes the date.
- **No date:** `Ongoing`, `TBD`, `next review` with no date and similar return
  None, as today, but are counted (§3.4).

Every place that writes a valid-time field calls `parse_valid`, and anything it
cannot parse is never stored as `_valid_from`:

- `_document_valid_time`;
- `lift_document_dates`;
- `_fact_valid_time`;
- `retime`;
- `table2graph`;
- `update`.

### 3.2 Mapping onto the fact

`_fact_valid_time` uses the whole `ValidTime`:

- the `valid_from` property's `start` sets `_valid_from`;
- its `end`, when the value was a range, sets `_valid_to`, unless the class also
  tags a `valid_to` property, which wins;
- `precision` and `time_of_day` are stored with them.

The raw property value stays on the observation as extracted, so nothing the
model wrote is lost.

### 3.3 Prompts

- **Properties prompt.** The DATES section (`_render_anchor_date_section`)
  states the accepted forms: a date, a range `YYYY-MM-DD to YYYY-MM-DD`, a month
  `YYYY-MM`, a quarter `YYYY-Qn`, then the original wording. It asks for a
  range whenever the text gives one.
- **Relationships prompt.** Unchanged: it is not asked for dates (§1.6).
  Fiction, which tags temporal properties but has no `relative_anchor`, gets the
  DATES section without an anchor.
- **Entities prompt.** Gains the occurrent naming rule (§5.2): no date, weekday
  or "today" in a happening's name. With the schema-bound properties spec F3
  (defining keys folded into the entities step), the class's `temporal:` key
  would move there with its DATES section.

### 3.4 Reporting

The per-document extraction log line and `job-status` count:

- dated facts;
- ranges kept;
- unparsed temporal values (the value is never logged).

`ingest empty-extractions`' sibling `ingest date-audit` (read-only, no LLM) lists
per class: the share dated by property, precision mix, ranges, and unparsed
values. That gives schema authors the evidence the property audit gives for
keys.

## 4. Schema

- **Temporal flags carry meaning.** `temporal:` on a property takes `valid_from`
  or `valid_to` as today, plus two new values:
  - `due`: a deadline or target date. It is stored as `_due` with the same
    parsing, and **does not** set `_valid_from`.
  - `review`: a next-review date, stored as `_review`.

  Today deadlines are used as `valid_from`:

  - banking `ACTION_ITEM.deadline`;
  - banking `REMEDIATION_ACTION.due_date`;
  - project_governance `ACTION.due_date`;
  - project_governance `STATE_CHANGE.due_date`.

  That dates an action by when it is due, not when it was raised.
- **Every `occasion` class declares one valid-time property.** `domains validate`
  WARNs on an `occasion` class with none. Shipped schemas fixed in this change:
  - banking `TRANSACTION` and `SAR`;
  - general `EVENT` and `CLAIM` (general gains a `temporal:` block with
    `default: ingestion_date`);
  - project_governance `DECISION`;
  - technical_paper `FINDING`.
- **Duplicate date keys are folded with `property_aliases`.** For example,
  banking `CASE.opened` / `opened_date` and project_governance
  `STATE_CHANGE.due_date` beside `date_or_time`.
- **Document labels name one meaning.** `temporal.document` labels map to
  `valid_from` (effective, issued, dated) or to a new `revised` key (Last
  Updated, Version date). Today "Last Updated" is read as the effective date;
  `revised` is stored as `_revised_at` (a valid-time date) and is never used as
  `_valid_from` when an effective label is present.
- **`valid_to` labels.** A document's own end (Expiry, Valid until, Superseded
  on) maps to `valid_to`. No shipped schema maps one today.
- These rules extend the property audit (schema-bound spec §3): it reports a
  class's temporal key fill alongside the date-audit counts.

## 5. Dating what retrieval returns

### 5.1 Chunks, passages, summaries

- **What they carry.** `:DocChunk`, `:DocPassage` and `:DocSummary` carry:
  - `_doc_valid_from` / `_doc_valid_to`, copied from their document at commit;
  - a chunk's own `_valid_from`, when the chunk has a dated header or (journal)
    starts with a date line, else the document's.
- **Where it is set.** `summary_store.summary_rows` reads `_valid_from` (fixing
  the unprefixed read), and `passages` copies the chunk's fields.
- **The filters work.** With this, the `--asOf` filters on `vector-text` and
  `chunks` (and `--from/--to` on `survey`, which today reads the document) mean
  something.
- **A retired version's date.** Supersession and retirement update these fields
  with their document, in the same transaction that sets its `_valid_to` (§7.2).

### 5.2 Dated happenings

A happening is an entity whose class declares `identity: occasion` (§5.3). This
section makes every mention of one a dated, reachable record, and gives
questions one route to them.

**Schema.**

- The journal schema's ACTIVITY is a happening: `identity: occasion`, and
  `kind: occurrent`. Its description drops "recurring or single": an activity
  is one occasion of doing something.
- `domains validate` WARNs on a class that tags a `temporal: valid_from`
  property but declares `identity: name`, since its dates will fold.

**Identity: one happening per day, the happening's name shared.**

- For an `occasion` class, the aggregate key gains the observation's
  `_valid_from` day: `name|class|domain|YYYY-MM-DD` (`aggregate_key(...,
  occurs_on=)`). Mentions of the same happening on the same day merge; the same
  name on two days is two entities.
- An undated mention keys on its document's date, as today's observations
  already carry it.
- This changes ids for those entities. The migration pack (§8) rewrites
  same-as and curation records for existing vaults.
- A multi-day range (§3.1) keys on its start day and keeps its `_valid_to`.
- **Accepted limit:** two different happenings written with the same name on
  the same day merge (two people's "Tennis" on one day). In brain01 one day of
  27 for the commonest recurring happening has two mentions. Keying on
  participants too was rejected: it would split one happening whenever a
  mention leaves someone out.

**Naming: no date in the name.**

- The entities prompt's naming rule for `occasion` classes says: name the
  happening as the text does ("Tennis", "Lantern festival lights", "Lake trip").
  Never add a date, weekday or "today"; the date goes in the class's
  `temporal:` property.
- Where keys are built (`_build_observations`, `retime`), a trailing or
  leading date, weekday or ordinal is stripped from an `occasion` name before
  keying (pure code, no LLM). New extractions can still write one, so this is
  permanent behaviour; existing staged names fold the first time `retime` runs
  (through the migration pack, §8).
- Mentions of one recurring happening then share a name across days, so
  grouping them is a name match at query time. Variant names on one day
  ("Tennis" and "Tennis match") are linked by same-as review (§5.3).

**Query: one command for "every X", "how many times", "when did I".**

- `artmind query happenings --domain d --match "<term>" [--entityClass C]
  [--from] [--to] [--by year|month] [--with <entity id>] [--shape brief]`
  (read-only, proxied, traced):
  - finds entities of `occasion` classes whose name, aliases or `type` match
    the term (the `entity_name_ft` index), plus their same-as group;
  - returns one row per happening: date, name, class, `type`, the
    participants and places one hop away, and its chunk ids;
  - groups rows by period with `--by`;
  - carries `rows_total` and `truncated`, and `coverage` (keyword-matching
    documents in the window), so the agent can see when text mentions exceed
    the happenings found.
- `--with` limits to happenings linked to an entity, e.g. a person for "how
  many times did I see X".
- `graph timeline` keeps its meaning (everything that happened in a window) and
  lists every `occasion` class; with ACTIVITY an occasion, it gains activities.

**Skill.** The `artmind-query` route table gains a row: a question asking for
each time, every year, how many times, or when something happened goes to
`query happenings` first. `chunks --idList` on the rows' chunk ids gives the
detail, and `survey` fills in only what the happenings miss (`coverage`
larger than the rows).

### 5.3 Identity is declared per class

Whether two mentions with the same name are one thing is a property of the
class, declared in the schema. It is separate from `kind`:

| axis | decides | values |
|---|---|---|
| `kind` | how differing values are read | `recurrent`: change over time is history; `occurrent`: a disagreement is a conflict |
| `identity` | what makes two mentions one entity | `name`, `occasion`, `category` |

`kind` does not answer the identity question. In brain01, recurrent classes
fold occasions as well as things:

- **One thing:** a person over five years.
- **Occasions:**
  - one emotion node holds 75 mentions across six years;
  - one plan holds trips planned in three different years;
  - one object holds a different instance each time it is mentioned.
- **What `kind` gates today, and in which direction:**
  - name vocabulary (`canonicalize.recurrent_classes`) and the insights
    same-as detector run only over recurrent classes;
  - so occurrent classes are kept out of name matching, and recurrent classes
    that hold occasions are kept in.

**The values.**

- **`identity: name`.** Same normalised name, class and domain is one entity, as
  today. Every identity tool runs over it:
  - same-as proposers (`refine_graph.propose_merges`, `conflicts`' cross-domain
    candidates, the insights detector);
  - `entity-resolve` twins;
  - the extraction name vocabulary.

  For: people, organisations, places, ideas, banking products, policies and
  roles.
- **`identity: occasion`.** Keyed by name and day (§5.2).
  - Same-as proposers never pair two entities whose days differ, so they
    propose only same-day variants.
  - Twins does not fire.
  - The name vocabulary is not offered.

  Grouping across days happens at query time (`query happenings`). For:
  events, activities, state changes, banking transactions and SARs.
- **`identity: category`.** Folded by name as today, but it is a label many
  occasions share, not a thing:
  - same-as proposers and twins skip it;
  - the name vocabulary is offered, so the label stays one spelling.

  For a class kept as one node per label because the folded properties answer
  questions, e.g. an emotion whose `trigger` list answered the probe questions
  in the schema-bound properties spec (§1.3 there).

**Defaults and validation.**

- Absent `identity`: `name` for `kind: recurrent`, `occasion` for
  `kind: occurrent`. This is a permanent rule of the schema language, not a
  migration aid. Shipped schemas declare it on every class anyway (contracts
  AMENDMENT, a document-entity class, is `name`). An upgraded vault cannot mix
  old and new keys under the default, because the data-model gate (§8.1) stops
  writes until the migration pack has run.
- A class the schema does not declare (extractor drift) is `occasion`.
- One reader: `schema_validate.class_identity(schema, cls)`. These read it:
  - `aggregate_key`;
  - `canonicalize.recurrent_classes`, renamed `named_classes` (`name` +
    `category`);
  - the three same-as proposers;
  - `identity.twins`;
  - `query happenings`;
  - `graph timeline`.
- `domains validate`:
  - ERROR on a value outside the three;
  - WARN on `occasion` with `kind: recurrent` (its changes become separate
    entities, never history);
  - WARN on `name` for a class with a `temporal: valid_from` property.

**Shipped schemas.**

| schema | class | identity |
|---|---|---|
| personal_journal | PERSON, LOCATION, ORGANIZATION, IDEA | `name` |
| personal_journal | EVENT, ACTIVITY, STATE_CHANGE, PLAN | `occasion` |
| personal_journal | EMOTION | `category` |
| personal_journal | OBJECT | `name` |
| banking.* | TRANSACTION, SAR, INCIDENT_EVENT, GOVERNANCE_DECISION, AUDIT_FINDING | `occasion` |
| banking.* | every other class | `name` (default) |
| other schemas | every class | default from `kind` |

Changes to existing classes:

- **PLAN** moves to `identity: occasion` and `kind: occurrent`. It is keyed on the
  day the plan is first stated, unless it carries a date of its own (`timeframe`
  tagged `temporal: valid_from`). A plan restated on later days is then several
  entities; `query happenings --match` groups them, and same-day variants go to
  same-as review.
- **Undeclared classes.** brain01 holds 11 HEALTH_INCIDENT entities, a class the
  schema does not declare (extractor drift). An undeclared class keeps today's
  default (`occurrent`, so `occasion`).
- **EMOTION** stays one node per label (`category`), so its `trigger` and
  `resolution` lists keep working.

**OBJECT stays one class; happenings leave it.** In brain01, OBJECT holds 866
entities (1,230 mentions): possessions, documents and devices, but also food,
medicine and some happenings.

- **Happenings misfiled as objects.**
  - One OBJECT entity "Flight" holds 26 mentions, while "Flight Travel" (37)
    and "Air Travel" (31) are ACTIVITY entities for the same kind of happening.
  - Cab, train and ticket mentions land in OBJECT too.
  - The extractor already treats travel as an activity most of the time;
    OBJECT's description ("any physical item … or tangible thing that plays a
    role") lets the rest in.
- **OBJECT's description changes to:** a physical thing that is owned, used,
  kept, given, eaten or taken. It adds the rule: "a trip, flight, ride, meal or
  appointment is an ACTIVITY or EVENT, not an object; the vehicle, ticket or
  document involved is an object only when the text is about the thing itself".
- **Food and medicine stay OBJECT** under `identity: name`.
  - Most appear once: food is 156 entities over 206 mentions, medication 34
    over 59.
  - The few repeats fold into one node, which is what `category` would do.
  - The cost is a few same-as proposals between food variants, which review
    rejects.
  - A separate class was considered and rejected: it changes nothing for
    retrieval, because `type` already filters.
- **`type` becomes a declared list.**
  - OBJECT's `type_examples` become `types:`, a closed list: device, document,
    vehicle, clothing, household_item, possession, gift, food, medication,
    medical_item, decoration, pet.
  - The entities prompt asks for one of them.
  - `domains validate` checks the list is non-empty.
  - The property audit (schema-bound spec §3) reports `type` values outside the
    list, which today number over 20 ad-hoc ones (`medical_specimen`,
    `vehicle_part` and so on).
  - Queries filter on `type` ("which medicines", "what devices").
- **Existing data.** `retime` does not re-classify. A one-time step in the
  migration pack (§8) moves OBJECT observations whose name names a trip,
  flight, ride or booking to ACTIVITY, dropping their `type` values (`flight`,
  `travel_ticket`). The word list lives in the pack, not in schemas or core
  code.
- **Kept in reserve, not built.** An `identity` override per `type` value
  (`food: category`), if same-as noise on food or medicine proves a burden.

## 6. Record time and the query surface

### 6.1 Writes

- **Writing.** `_commit_document_tx` sets `_recorded_at` (UTC, one timestamp per
  commit) on the document version's new or revived nodes and edges.
  A revived observation keeps its first `_recorded_at` and gains `_revived_at`.
- **Retiring.** Every relabel to History sets `_retired_at` and `_retired_by`:
  - version supersession (`_retract_prior_version`);
  - `docs retire`;
  - supersession;
  - retractions (`projection.apply_retractions`).
- **Curation records.** Supersession and lifecycle records gain `recorded_at`.
  Supersession records gain the `effective` date derived from §4 labels where
  the documents state one.
- **Clocks** (decided 2026-11-17):
  - **record time is UTC:** `_recorded_at`, `_retired_at`, `ingested_at`,
    `created_at` (`update.py` moves its `datetime.now()` to UTC);
  - **document time is local:** a date artmind assigns as valid time when the
    source gives none — the `ingestion_date` default, `update.py`'s document date,
    the SCD2 batch date — is the machine's local date (the `ingestion_date`
    default moves off UTC);
  - **query time is local:** `--asOf today` is the local date, and a
    `--knownAsOf` date means the end of that local day, converted to UTC before it
    is compared with record time.

### 6.2 Reads

- **Valid-time filters.** One function, `graph_query.valid_predicate(var,
  asOf, *, include_undated=True)`, replaces `asof_predicate`.
  - It compares against `_valid_from` and `_valid_to` only.
  - A year or month `--asOf` is widened to its last day, which fixes §1.5.
  - It treats precision `none` as undated.
- **Record-time filter.** `--knownAsOf <date|datetime>` (a date is the end of that
  local day; a datetime without a zone is local; both converted to UTC) on `entity-history`,
  `document-history` and `pattern10` reads record time: a node counts when
  `_recorded_at <= T` and (`_retired_at` IS NULL OR `_retired_at > T`).
  - Both labels are searched (current and History), so this answers "what did
    the graph say on T".
  - Nodes written before this change get approximate record time from the
    migration pack (§8), flagged `_record_time_approx`. Core never meets a
    node without `_recorded_at`; the payload warns when a row's record time
    is approximate.
- **`entity-history --asOf` keeps its meaning,** now a proper valid-time point
  that honours `_valid_to`. Its skill text is corrected.
- **Projection winner** becomes max of (`_valid_from`, `_doc_valid_from`,
  `doc_version`, `id`) for a **recurrent** class's scalar properties. The latest
  fact wins, then the latest document. Occurrent classes keep raising a conflict
  on disagreement. This removes the §1.5 mismatch. It changes winners only where
  a fact's own date is later than its document's, which §1.1 measured at under
  2% of observations.

## 7. Ends of validity

### 7.1 One name

Every writer uses `_valid_to` / `_valid_from` and every reader reads only
them; `coalesce(…)` reads are removed. Existing documents' unprefixed
`valid_to` / `valid_from` are folded once by the migration pack (§8).

### 7.2 Supersession and retirement

- **Superseding.** When document B supersedes A with effective date E (from the
  record, else B's `_valid_from`), A's facts gain `_valid_to = E` where they have
  none, and so do A's chunks, passages and summaries. A gets `_valid_to = E` and
  `_retired_at = now`.
- **Retiring.** `docs retire` sets only `_retired_at`; validity is unknown, so
  `_valid_to` stays empty.
- **Undoing.** `unsupersede` restores both.

### 7.3 Tables

`table2graph` passes an SCD2 row's `_valid_to` to the observation, so a closed
row version ends in the graph too.

## 8. Existing data: the migration pack

Core code knows only the new model. Everything that converts what a vault
already holds lives in one pack, `artmind/migrations/temporal_v1/`, run once per
vault and deleted when every vault has run it (§8.3).

### 8.1 What stays in core

These are permanent behaviour, not migration, even though their first run also
fixes old data:

- `ingest retime` re-derives staged observations from the current schema: dates
  through `parse_valid`, chunk dates, occasion day keys and name stripping.
- `sameas.propose` refuses cross-day `occasion` pairs and `category` pairs.
- `meta.yaml` must carry `identity_naming_rules.occasion`; there is no copy of
  the rule in code. The pack adds it to a vault's `meta.yaml`.
- **One generic gate, `artmind/data_model.py`.** `CURRENT = 1`; the run folder's
  `data_model.yaml` records the version its data is at (committed with the
  vault). An absent file means 0 when the data dir already holds a registry DB,
  and `CURRENT` (written at once) when it holds nothing. `require_current()` is
  called by every write path (`ingest sync|async|retime|…`, `projection
  rebuild`, `vault sync`, `update`, same-as and curation writes) and raises one
  error: "this vault's data is at model 0; run `artmind migrate status`". Read
  paths warn once per process and proceed. The gate is not temporal-specific:
  a later migration pack bumps `CURRENT` and reuses it. An unmigrated vault
  therefore stops instead of mixing old and new keys.

### 8.2 What the pack does

`artmind migrate temporal [--domain d] [--dryRun]` (tier-1 write, no LLM, git-clean
vault required, ingest worker stopped). The dry run reports the whole plan; the
real run applies the steps in this order, each idempotent:

1. **Vault files.** Add `identity_naming_rules.occasion` to the vault's
   `meta.yaml` when absent, as a textual block at the end, so comments and order
   survive. Undo: `git restore`. Vault schemas are not edited: the kind default
   applies, and content changes to shipped schemas arrive through `init --force`.
2. **Graph.** Fold documents' unprefixed `valid_to` / `valid_from` into the
   prefixed names (§7.1). Backfill record time: current nodes get
   `_recorded_at = Document.ingested_at`; History nodes get
   `_retired_at = ingested_at` and `_recorded_at` set to the epoch
   (`1970-01-01T00:00:00+00:00`, "known before the backfill"). Both are flagged
   `_record_time_approx`. Only nodes without the field are touched.
3. **Staging and curation.** Plan `retime` in memory under the migrated
   schemas, then:
   - move OBJECT trips and rides to ACTIVITY (the pack's word list, shown in the
     dry run with a sample of class counts);
   - compute the old→new key map;
   - rewrite `same_as.yaml` (one group per day on which two or more members
     have observations; `category` groups dropped);
   - close open same-as proposals that pair different days or `category`
     entities as `rejected` with reason `identity:<value>`;
   - re-point synthesis, conflict and mart dimension records;
   - write and commit the staging, then one `projection rebuild`.
   Undo: `git restore` of the listed files, then `ingest retime`.

4. **Stamp.** Write `data_model.yaml` `version: 1`. A run with `--domain`
   stamps only when no other domain is pending.

`artmind migrate status` (read-only) reports the recorded version and, per step
and domain, done or pending from facts (schemas lacking `identity`, documents
with unprefixed dates, nodes without `_recorded_at`, staging whose keys would
move, `meta.yaml` lacking the rule). Because each step is idempotent and judged by facts, an interrupted run
is resumed by running it again.

Content changes to shipped schemas (journal ACTIVITY/PLAN/OBJECT, banking
deadlines) reach a vault through `artmind init --force --schema NAME` before the
pack runs.

### 8.3 Sunset

The pack is self-contained: no core module imports it, and a test enforces this.
It may import core (staging readers, `retime_observations`, the same-as writer,
`rebuild_projection`). The only core references to it are one `cli.add_command`
line. Once `migrate status` reports done in every vault, delete the folder and
that line; `data_model.py` and its gate stay.

A later change of a class's `identity` in a vault that has already migrated is
not this pack's job: `retime` re-keys it, and catalogue health
(`same_as_dangling`) lists the same-as members left behind.

## 9. Rollout

The code lands in this order (one branch, merged once). Shared order with the
schema-bound properties spec:

1. Defect fixes:
   - year-only `--asOf`;
   - the `summary_rows` key;
   - unparsed values kept out of `_valid_from`;
   - UTC clocks;
   - the skill text.
2. `parse_valid` and §3.2 mapping, plus `retime` over existing staging.
3. Schema rules (§4), shipped schema fixes and `domains validate` checks.
4. Dated chunks, passages and summaries (§5.1), and one valid-time predicate (§6.2).
5. Declared identity (§5.3) and dated happenings (§5.2):
   - `identity` in schemas and its one reader;
   - day keys for `occasion` classes;
   - the naming rule and name stripping;
   - same-as and twins gated by identity;
   - journal OBJECT: a declared `type` list;
   - `query happenings` and the skill route.
6. Record time (§6.1) and `--knownAsOf`.
7. Ends of validity (§7) and the projection winner change.
8. The migration pack (§8).

Per vault, after the upgrade: `init --force` for shipped schemas whose content
changed (re-apply the vault's own edits), `migrate temporal --dryRun`, then the
real run, then `insights refresh` and `mart build`.

## 10. Acceptance

- **Parsing.** In brain01, 0 unparsed temporal values become document-date
  fallbacks silently: each is counted in `ingest date-audit`. All 13 ranges keep
  their end.
- **Retrieval filters.**
  - `vector-text --asOf` and `chunks --asOf` return fewer rows than without the
    flag on a date inside the corpus.
  - `--asOf 2026` includes 2026 dates.
- **Happenings.**
  - In brain01, no `occasion` entity spans observations on two different days.
  - No open same-as proposal pairs two `occasion` entities on different days, or
    two `category` entities.
  - Under 5% of `occasion` entity names contain a date.
  - `query happenings --match` for the four recurring-happening questions in
    the brain01 classic suite returns, per year, at least the dated days the
    expected answers name.
- **Answers on happenings.** The brain01 classic questions about recurring
  happenings (festivals every year, how many times a sport was played) move from
  partial towards correct: at least 2 of 4 correct in 2 of 3 runs, with the
  current-skill runs as the baseline.
- **Record time.** `entity-history --knownAsOf T` before a note's last re-ingest
  returns the observations the earlier version stated, and after it the current
  ones (test with a synthetic vault).
- **Answers.** The classic benchmark suites on both vaults show no drop in grade
  bands (3 runs per side). The brain01 "when did X" questions hold their
  correct rate.

## 11. Follow-ups

- **F4. Deadlines in queries.** `_due` and `_review` are stored (§4) but no query
  filters on them. A TASK or TODO class (`kind: recurrent`, `identity: name`, a
  `temporal: due` property, a completion date tagged `temporal: valid_to`) works
  with this design as it stands; "what is due before X" would need a `--dueBefore`
  filter on `entity-listing`/`pattern7` or a saved view.

- **F1. Per-period recall.** A dated `survey --by year|month` should guarantee a
  minimum of ranked documents per period that has keyword hits (today 2 of 5
  entries in one year).
- **F2.** Banking's undated happenings (TRANSACTION, SAR): once §4 gives them a
  date property, backfill it with the schema-bound spec's `backfill-properties`.
- **F3.** A `--knownAsOf` on `vector-text`/`survey`, which needs record time on
  passages through their history labels.

---

## Amendment (implementation, 2026-11-18)

Notes recorded while implementing; the sections above are left as written.

- **§5.1 last bullet vs §7.2.** "Supersession and retirement update these fields"
  is narrowed to supersession, as §7.2 says (retiring closes nothing).
  `_doc_valid_to` is the document's *stated* end, copied at commit and not
  touched by supersession; a chunk, its passages and its summaries also carry it as `_valid_to`. A supersession end lives in `_valid_to`, marked
  `_valid_to_by` (`supersede:<id>`), set only where `_valid_to` was empty and
  reopened only where that marker names the record. On the Document, a stated end
  is kept and restored by `unsupersede`. **Chunk-end question — decided 2026-11-18:**
  chunks, passages and summaries take the document's stated end as their own `_valid_to`
  at stage/commit time, so a supersession (which fills only a missing end) cannot push
  theirs past the document's: a chunk of a document with stated end D earlier than E ends
  at D; a document without a stated end takes E. A re-commit leaves the stated end;
  `restate_validity` closes open nodes only. **Q5 — decided 2026-11-18:** a document
  superseded twice ends at its *first* replacement: `restate_validity` closes from the
  edges by the end each one writes, earliest first: the edge's `effective`, else the
  newer document's stated `_valid_from` (an edge with no end last; ties by newer id).
  A newer document's commit restates the validity of every document it
  supersedes, so the `_valid_from` fallback for E is the same on every machine.
- **§8.1 gate has two markers (beyond §8.1 — needs the user's confirmation).** `<run folder>/data_model.yaml` (committed; staging,
  `same_as.yaml`, curation) and a `(:DataModel {id:'artmind', version})` node in
  each machine's Neo4j (each graph is migrated separately). A write needs both
  current; reads check the file only and warn. "Fresh" means no model file, no
  registry DB, no staged folders, no `same_as.yaml`, no curation records; a graph
  with no Document or Entity nodes is fresh. The pack's last step stamps both.
  Reason: a vault cloned to a second machine would otherwise be stamped current
  over old graph data, and a migrated file pulled by git would pass the gate over
  an unmigrated local graph.
- **Record time.** `_retired_by` has two values beyond §6.1's list: `removed` (a
  note vanished from the vault, `retract_document`) and `resplit` (a chunk
  dropped by a re-chunking). A re-commit keeps a chunk's first `_recorded_at` but
  takes the new text, and a retire-then-revive window is invisible to
  `--knownAsOf` (a revive clears `_retired_at`). `document-history --knownAsOf`
  does not filter Document nodes.
- **Dates.** A document stores `_valid_precision` (`none` for the ingestion
  default); a default-dated document counts as undated in filters and in the
  projection's temporal grouping. A document's ranged `_valid_from` sets its
  `_valid_to` when none is explicit. **Q1 — decided 2026-11-18:** a fact dated
  by its own property keeps only its own end (possibly none); a fact dated by its document
  (no own date) inherits the document's `_valid_to` at build time and in `retime`.
  A single-day fact in a document with an end is therefore open-ended, not a range;
  `date-audit` counts only a fact's own ranges. table2graph's row end for the row's
  own subject is a separate rule. `parse_valid` never raises; an end at or after 9999-12-31 is
  open. `ingest table2graph --asOf` still refuses a year or month.
- **Identity.** Occasion observation ids include the day (a chunk may hold two
  days of one stripped name). `retime` keeps existing ids, so a re-extract after
  a retime replaces occasion observations instead of moving them. Twins skip only
  classes declared `occasion` or `category`. Banking IMPACT_ASSESSMENT and
  RETENTION_DECISION are `occasion`, not `name` (they are occurrent with a
  valid_from property, so `name` trips the Q20 warning). **Q2 — decided 2026-11-18:**
  REGULATORY_UPDATE is `kind: recurrent`, `identity: name`. **Q3 — decided 2026-11-18: precision-shaped
  tokens.** An occasion keys on its date at the date's own precision, not on the
  period's first day (which merged a month mention with a real mention on the 1st
  and claimed a day the text never gave): day `tennis~2024-03-30`, month
  `~2024-03`, quarter `~2024-Q1`, half `~2024-H1`, year `~2024`
  (`observations.occasion_token`; the spellings `parse_valid` reads; precision
  `none` still gets no token). This amends §5.2's `YYYY-MM-DD` and "a coarse date
  keys on its start day". `split_occasion` and `entity_history --name` read all
  five shapes (`OCCASION_TOKEN_PATTERN`). Two occasion members are one occasion
  only when their tokens are equal: a month token and a day token in that month
  are different occasions, and `sameas` refuses the pair (`identity:occasion
  (different dates)`); the insights same-as detector and `refine_graph` group by
  token likewise. Tokens begin with the year, so they sort by year as strings,
  but not chronologically within a year across shapes.
- **Conflicts.** Default-dated observations no longer form temporal instants in
  the projection, so disagreeing recurrent scalars across default-dated notes
  become conflicts: expect more open conflicts in vaults with many default-dated
  notes (measure in Task 30 alongside Q11).
- **Known limits.** `graph timeline` keeps undated rows under `--from/--to` (survey
  and happenings drop them) and has no cap.
- **Migration pack: chunk ends.** A `chunk_ends` step (after `record_effective`, before
  `supersession_records` and `supersession_ends`) gives an old chunk, passage or summary
  (and their History rows) with no end its document's stated end, so core's rule (a
  stated end is the node's own) holds for migrated data before supersession ends are
  restated. Core has no old-data handling.
