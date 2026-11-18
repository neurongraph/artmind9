# Temporal model: decisions needed before implementation

Companion to the plan `2026-11-15-temporal-model.md` ("Questions for review") and the
spec `../specs/2026-11-15-temporal-model-design.md`. Question 2 already has an
answer and is not repeated. Question 13 also has one, but it is restated because the
migration pack changes where it lives. For each of the others, this file gives the
background, what the plan proposes, the realistic alternative, and a recommendation.

## Revised 2026-11-17: the migration pack

You asked to keep one-time migration logic out of the main code. The spec (§8) and
the plan (new Part 9, Tasks 31–36) now work like this:

- **Core code knows only the new model.** It never reads the old unprefixed
  `valid_to`, never handles a node without `_recorded_at`, and carries no copy of
  `meta.yaml` rules.
- **One permanent gate, `artmind/data_model.py`.** A vault records its data-model
  version in `.artmind/data_model.yaml`. An existing vault without the file is at
  version 0. While a vault is behind, every write command refuses with "run
  `artmind migrate status`", and reads warn. A fresh vault starts current. The gate
  is generic, so future migrations reuse it.
- **One temporary pack, `artmind/migrations/temporal_v1/`.** It provides
  `artmind migrate status` and `artmind migrate temporal [--domain] [--dryRun]`.
  Each step is judged done or pending from the data itself, so a second run does
  nothing and an interrupted run resumes. The steps:
  1. add the occasion naming rule to the vault's `meta.yaml`;
  2. fold documents' old date names and backfill approximate record time;
  3. move OBJECT trips to ACTIVITY, re-key occasions and rewrite same-as and
     curation records, then rebuild once;
  4. stamp version 1.
- **One-way imports.** No core module imports the pack (a test enforces it); the
  only link is one `cli.add_command` line. The sunset is deleting the folder, its
  tests and that line once every vault reports done.
- **What stays permanent in core:** `retime` re-deriving dates and keys,
  `strip_occasion_date` (new extractions can still put dates in names), and the
  same-day guard on same-as proposals.

Six questions changed because of this. Each is marked **(revised)** below: Q4, Q6,
Q9, Q12, Q13 and Q14. Q3 is unchanged, but its risk is now covered by the gate.

**How to answer:** reply with the numbers you want changed and how, for example
"3: keep AMENDMENT on the default; 9: no backfill". Any number you don't mention keeps
the plan's proposal.

---

## Background in five paragraphs

**Entity keys.** Every extracted mention becomes an *observation*. Observations are
grouped into one `:Entity` by an *aggregate key*, a string with exactly three
`|`-separated parts: `normalised name|CLASS|domain`, for example
`tennis|ACTIVITY|personal_journal`. Today every mention of "Tennis" across six years
folds into one entity, so the entity shows one date and the other days survive only on
the observations.

**`kind` versus the new `identity`.** Each schema class already has a `kind`:
`recurrent` (a thing whose values change over time, so differences are history) or
`occurrent` (a happening, so differing values for one key are a *conflict*). The spec
adds a second, separate setting, `identity`, which decides what makes two mentions one
entity:
- `name`: same name means the same thing, as today (people, places, products);
- `occasion`: same name **and same day** (a game of tennis, a transaction); the key
  gains the day;
- `category`: a label many occasions share (an emotion such as "anxiety"), folded by
  name but kept out of duplicate detection.

**Same-as.** `same_as.yaml` records "these keys are one entity" (merges reviewed by a
person). Proposers suggest pairs, and twins are an `entity-resolve` warning about
same-name entities. All of them currently work by name, so `occasion` classes need
gating, or they would propose merging Tennis on Monday with Tennis on Friday.

**Valid time versus record time.** *Valid time* (`_valid_from`, `_valid_to`) means
"when was this true in the world". *Record time* (`_recorded_at`, `_retired_at`) means
"when did artmind hold this". Today only valid time exists, only its start is used,
and `_valid_to` is set on nothing.

**Vaults.** banking_corpus is public test data. brain01 is a private journal, and most
of the measured numbers below come from it.

---

## A. Keys and identity

### Q1. Where the day goes in an occasion's key

- **Background.** The spec writes the key as `name|class|domain|YYYY-MM-DD`, a fourth
  part. Ten separate parsers (projection, same-as, refine-graph, commit, conflicts,
  synthesis, snapshot, curate UI, update) reject any key that does not have exactly
  three parts. A four-part key would make the projection skip every occasion entity
  without raising an error.
- **Proposal.** Put the day inside the name part, joined by `~`:
  `tennis~2024-03-30|ACTIVITY|personal_journal`. The `~` separator is already used for
  document keys. A small helper splits it back out. Only name-matching queries need to
  know about it.
- **Alternative.** Keep four parts and update all ten parsers. That touches far more
  code, and a parser that gets missed fails silently.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

### Q3. Which classes become `occasion` in the shipped schemas

- **Background.** The spec's default is `identity: occasion` for every `kind: occurrent`
  class. In practice, any schema that never mentions `identity` would get day keys on
  all its occurrent classes. Measured on banking: almost nothing splits (2 TRANSACTION
  keys and 3 AUDIT_FINDING keys span two days). The risky case is contracts' AMENDMENT:
  it is occurrent, but it is also the *document's own entity* (one amendment per
  document, by design). If two documents state its effective date differently, day
  keys would split one amendment into two.
- **Proposal.** Write `identity:` explicitly on every class of every shipped schema, so
  nothing depends on the default. Set contracts AMENDMENT to `identity: name`. Make
  `domains validate` skip its "dates will fold" warning for document-entity classes.
- **Alternative.** Rely on the default and accept the risk on AMENDMENT, or make the
  default `name` everywhere and opt classes into `occasion` one by one. The second
  option is safer for vaults with custom schemas, but it changes the spec's default.
- **Recommendation.** Keep the proposal. A vault's custom schema without `identity`
  gets the default from `kind`. That is a permanent rule of the schema language, not
  a migration aid. The data-model gate stops an upgraded vault from writing new keys
  until the pack has re-keyed its existing data, so old and new keys never mix.
- **Response** - Agreed

### Q20. When `domains validate` warns "this class's dates will fold"

- **Background.** The spec says: warn on any `identity: name` class that has a
  `temporal: valid_from` property. But 20 recurrent classes in the shipped schemas have
  one (banking RATE_ENTRY, PRODUCT, POLICY, contracts TERM, and so on). They are
  *things that change over time*, and folding them into one entity with history is
  exactly right for them. The warning would fire 20 times on correct schemas.
- **Proposal.** Warn only when the class is `identity: name` **and** `kind: occurrent`
  **and** has a `valid_from` property, which describes a happening whose days would
  fold. Never warn for document-entity classes.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

### Q21. Rows from structured tables

- **Background.** `ingest table2graph` turns table rows into entities, and its mapping
  file states each entity's identity explicitly (for example, a transaction id).
  Adding the day to such a key would split something the mapping says is one entity.
- **Proposal.** Never add a day to observations that come from a table, whatever the
  class's `identity` says.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

---

## B. Naming, same-as, OBJECT, happenings

### Q4. Telling the model not to put dates in happening names (revised)

- **Background.** `meta.yaml` holds the naming rules the entities prompt uses. Its rule
  for occurrent classes currently says "a date or identifier that distinguishes one
  occurrence from another belongs IN the name". That is why 1,958 of 2,822 staged EVENT
  observations in brain01 have names like "Tennis - 2024-03-30". With day keys the date
  belongs in the key, not the name. A vault's `meta.yaml` is the user's own copy, and
  `setup` does not refresh it.
- **Proposal (revised).** Add a new rule, `identity_naming_rules.occasion` ("name it as
  the text does; never add a date, weekday or 'today'"), to the packaged `meta.yaml`.
  For occasion classes it replaces the old occurrent rule. The old rule stays for
  occurrent classes with `identity: name`. There is **no copy of the rule in code**:
  the migration pack appends it to a vault's `meta.yaml`, keeping comments and order.
  If it is still missing, core stops with "run `artmind migrate status`".
- **Alternative.** Keep a code fallback (the earlier proposal). That puts migration
  logic in core, which is what you asked to avoid.
- **Recommendation.** Keep the revision.
- **Response** - Agreed

### Q5. Removing dates from names already extracted

- **Background.** Existing staged names already contain dates. About 1,060 of the
  1,958 dated names have a form like "Tennis - 2024-03-30", and an existing
  normalisation rule already strips that tail, so all those days currently fold into
  the single key `tennis`. The other ~900 look like "Tennis on 2024-03-30", "Tennis
  Saturday" or "Tennis 3rd March" and stay as separate entities. The spec says
  `canonicalize` (an LLM step) should strip dates.
- **Proposal.** A pure Python function, `strip_occasion_date(name)`, covering the
  no-separator, weekday and ordinal forms. It is applied where keys are built (at
  ingest and in `retime`), with no LLM, so it also fixes existing staging. The
  observation's raw `name` keeps the model's wording; only the canonical name and key
  change.
- **Alternative.** Strip in `canonicalize`, as the spec says. That only helps new
  extractions, so existing data would need a re-extraction with LLM cost.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

### Q12. What happens to existing same-as groups of occasion entities (revised)

- **Background.** A reviewed group such as {"tennis", "tennis match"} today means
  "these two names are the same activity". After day keys, each name becomes many
  entities (one per day). The old group cannot simply be copied over.
- **Proposal (revised: a pack step; the rule is unchanged).** For each day on which at
  least two of the group's names have observations, create one group for that day
  ("tennis~2024-03-30" plus "tennis match~2024-03-30"). Drop members that have no
  same-day partner, and report the counts. Groups of `category` classes are dropped,
  because those classes are excluded from same-as. The rewrite runs once, in the
  pack. The "same day only" rule on new proposals stays in core (`sameas.propose`),
  because it is permanent behaviour.
- **Alternative.** Make the group a query-time alias ("tennis match" is a variant name
  of "tennis") instead of per-day merges. It would be cleaner for `query happenings`,
  but it is a new concept that the spec does not describe.
- **Recommendation.** Keep the proposal. `query happenings` already groups across days
  by name match, so per-day groups are enough.
- **Response** - Agreed

### Q13. One command for the re-key (revised)

- **Background.** You answered "one command": re-keying occasions must also rewrite
  `same_as.yaml`, close stale proposals and re-point synthesis, conflict and mart
  records, all before one rebuild.
- **Proposal (revised).** That command is `artmind migrate temporal`, not a new phase
  of `ingest retime`. `retime` stays a permanent "re-derive staging from the current
  schema" command with no record rewriting. The pack's step copies every file it
  writes to `logs/migrate-temporal/<run_id>/` first. If a record rewrite fails, it
  restores those files and commits nothing.
- **Recommendation.** Keep it. It is still one command with one dry run.
- **Response** - Agreed

### Q14. Moving travel mentions from OBJECT to ACTIVITY (revised)

- **Background.** In brain01, OBJECT holds happenings that were misfiled: "Flight" (26
  mentions) is an OBJECT, while "Flight Travel" and "Air Travel" are ACTIVITY. The spec
  wants existing OBJECT observations that name a trip, flight, ride or booking moved to
  ACTIVITY, with no LLM.
- **Proposal (revised).** No schema keyword. The move is a one-time step of the pack,
  with its word list inside the pack: flight, train, cab, taxi, ride, trip, journey,
  booking, matched as whole words in the name. OBJECT's new description (Task 19)
  keeps new extractions right, so nothing permanent is needed.
- **Alternative.** The earlier `retime_reclassify:` schema block. That would add a
  keyword to the schema language for a one-time fix.
- **Recommendation.** Keep the revision. One caution remains: "train ticket" or
  "booking confirmation" would also move. The dry run shows the count before anything
  changes, and the list can be trimmed then.
**Response** - Agreed

### Q16. How `query happenings` matches a term

- **Background.** The spec says match on name, aliases or `type` through the
  `entity_name_ft` full-text index. That index covers name, description and aliases,
  but not `type`. Matching on description would pull in every happening whose
  description merely mentions the word (noise).
- **Proposal.** Search only the name and aliases fields of the existing index, plus an
  exact, case-insensitive match on `type`. No index change.
- **Alternative.** Also search description, which finds more and is noisier; the spec
  measured 41 dated days from name+description against 20 journal entries that name
  the festival. Or add `type` to the index, which needs a `setup` migration.
- **Recommendation.** Keep the proposal. If recall turns out short in Task 30, an
  opt-in `--inDescription` is a small follow-up.
**Response** - Agreed
---

## C. Valid-time dates

### Q6. When the old unprefixed `valid_to` is renamed (revised)

- **Background.** A superseded document gets an unprefixed `valid_to` (without the
  underscore) from its SUPERSEDES edge. The spec renames it to `_valid_to` in Part 7,
  but switches every date filter to read only `_valid_to` in Part 4. Between the two
  parts, every superseded document would look open-ended to filters.
- **Proposal (revised).** Core writes and reads only `_valid_to` from Part 4 on. The
  rename of existing documents is step 2 of the migration pack. The ordering problem
  disappears, because the gate keeps a vault from being written until the pack has
  run.
- **Recommendation.** Keep it.
- **Response** - Agreed

### Q7. project_governance STATE_CHANGE's two date keys

- **Background.** STATE_CHANGE has both `date_or_time` (when the change happened) and
  `due_date`. The spec lists `due_date` twice: once as a deadline (a new
  `temporal: due`, which does not date the fact) and once as a duplicate of
  `date_or_time` to merge. It cannot be both.
- **Proposal.** `date_or_time` becomes the fact's date (`temporal: valid_from`) and
  `due_date` becomes a deadline (`temporal: due`), with no merging.
- **Alternative.** Treat `due_date` as a misspelling of `date_or_time` and merge it.
  This is only right if your governance documents use "due date" to mean the date the
  state changed.
- **Recommendation.** Keep the proposal, unless you know the documents use it the other
  way.
**Response** - Agreed. Would this design work for a TASK or TODO class (when that comes in)?

**Reply (2026-11-17).** Yes, with no further change. A TASK class would be:
- `kind: recurrent`, because its status changes over time (open → done), and that
  is history, not a conflict;
- `identity: name`, because one task is one thing across the notes that mention
  it. A chore repeated on several days ("water the plants") would instead be
  `identity: occasion`, with one entity per day;
- `due_date: {temporal: due}`, stored as `_due`. It never dates the task.
- The task's own date (`_valid_from`) is when it was raised: a `raised_on`
  property tagged `temporal: valid_from`, or else the note's date.
- A completion date tagged `temporal: valid_to` closes it, so `--asOf` after
  completion no longer shows it as open.

One gap: no query filters on `_due` yet ("what is due before Friday"). The spec
records it as follow-up F4: a `--dueBefore` filter, or a saved view.

### Q15. When a chunk gets its own date

- **Background.** Chunks, passages and summaries will carry their document's date. The
  spec also wants a chunk's *own* date when it "has a dated header or starts with a
  date line". Read loosely, a chunk whose heading is "Plans for 2027" or "Q3 review"
  would be dated wrongly.
- **Proposal.** A chunk gets its own date only when its last heading, or its first
  non-blank line, is **only** a date, readable to the day. Otherwise it takes the
  document's date. In brain01 every entry is one day, so nothing changes there. The
  rule matters for multi-day notes.
- **Alternative.** Also accept month or year headings ("## March 2026"), which suits
  monthly logs but carries a higher risk of false dates.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

### Q18. Which clock `--asOf today` uses

- **Background.** The spec moves record time to UTC. `--asOf today` is a valid-time
  filter that currently uses the local date. In India (UTC+5:30), between midnight and
  05:30 local time "today" in UTC is still yesterday.
- **Proposal.** Use UTC there too, so the code has a single clock.
- **Alternative.** Keep local time for `--asOf today`, since it is a user-facing
  calendar word. This is arguably what a person means by "today".
- **Recommendation.** This one is a judgement call. The plan says UTC; I lean slightly
  to **local** for `--asOf today` only, since valid time is a calendar date with no
  timezone (spec non-goal), while record time stays UTC. Tell me which you prefer.
**Response** - Query time is local, record time is UTC, document time is assumed local

**Applied (2026-11-17).** `--asOf today` stays the local date (Task 2). Record
timestamps (`_recorded_at`, `_retired_at`, `update`'s `created_at`) are UTC. Dates
artmind assigns to a document (the `ingestion_date` default, `update`'s document
date, the SCD2 batch date) are local (Task 4). A `--knownAsOf` date means the end
of that local day, converted to UTC before comparing with record time (Task 24).

### Q19. A precision value for half-years

- **Background.** The parser keeps how much of a date was stated: `day`, `month`,
  `quarter`, `year` (and `none` for the ingestion-day default). It also accepts
  "H1 2026" / "H2 2026", which have no precision value of their own.
- **Proposal.** Add `half`.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

---

## D. Record time and the projection

### Q8. What `_retired_by` can say

- **Background.** When a node leaves the current graph it is relabelled History and
  stamped with why (`_retired_by`). The spec lists `sameas:<id>` as one reason. But a
  same-as merge relabels nothing: member observations stay current, and only the merged
  entity node is rebuilt. There is no node to stamp. The spec also misses retractions
  (`projection.apply_retractions`), which *do* move an observation to History.
- **Proposal.** Drop `sameas:` and add `retract:<observation id>`.
- **Recommendation.** Keep the proposal.
- **Response** - Agreed

### Q9. Record time for data that existed before this change (revised)

- **Background.** `--knownAsOf T` asks "what did the graph say on date T". Old nodes
  have no `_recorded_at`, and the only evidence is `Document.ingested_at`, which holds
  the **latest** extraction time, not the first.
- **Proposal (revised).** The backfill is step 2 of the pack. On current nodes it sets
  `_recorded_at = ingested_at`. On History nodes (older versions) it sets
  `_retired_at = ingested_at` and `_recorded_at` to the epoch (1970-01-01), meaning
  "known before the backfill". Both are flagged `_record_time_approx`. Every node then
  has `_recorded_at`, so core's `--knownAsOf` needs no special branch for missing
  values; it only warns when a row is approximate. "Before the last re-ingest" still
  returns the earlier version.
- **Alternative.** Recover first-recorded times from the vault's git history. That is
  more accurate, but slow and complex.
- **Recommendation.** Keep the revision.
- **Response** - Agreed

### Q10. New underscore fields and the projection winner

- **Background.** When observations are merged into an entity, any field that is not
  on a "system keys" list is treated as a domain property, and differing values become
  conflicts. The new fields (`_valid_precision`, `_recorded_at`, `_due` and so on)
  differ between mentions all the time. Separately, choosing a property's "winner" by
  latest date would let the ingestion-day default date beat every real date.
- **Proposal.** Add every new field to the system-key list. In the winner sort, treat a
  date with precision `none` as empty.
- **Recommendation.** Keep the proposal. Without it, the change breaks things.
- **Response** - Agreed

### Q11. Extra conflicts from ACTIVITY and PLAN becoming occurrent

- **Background.** For an occurrent class, two same-key mentions that disagree on a
  property raise a `:Conflict`, and open conflicts block `projection synthesize`.
  Once ACTIVITY and PLAN are occurrent, two mentions of "Tennis" on the same day with
  different `emotional_valence` will conflict. Day keys make each entity smaller, and
  the schema-bound properties work already trimmed properties, so the number should be
  modest, but it is not zero.
- **Proposal.** Accept it, and measure open conflicts before and after in Task 30.
- **Alternative.** Keep ACTIVITY/PLAN `recurrent` but `identity: occasion`. They would
  still get day keys, and disagreements would read as history, not conflicts. `domains
  validate` would warn about that combination (spec §5.3).
- **Recommendation.** Keep the proposal; the measurement will show whether it matters.
- **Response** - Agreed

### Q17. Which nodes get `_recorded_at`

- **Background.** Some nodes are rebuilt from observations on every projection rebuild:
  `:Entity`, `RELATES_TO` edges and `:Conflict`. Stamping them on every rebuild would
  make "recorded at" mean "last rebuilt at".
- **Proposal.** Stamp `_recorded_at` on Document, DocChunk, DocPassage, DocSummary,
  Observation and `ASSERTS_RELATION`. Stamp `:Entity` only when the entity is first
  created. Never stamp `RELATES_TO` or `:Conflict`; their record time comes from their
  observations.
- **Recommendation.** Keep the proposal.
**Response** - Agreed
---

## Summary of my recommendations

Keep every proposal as written (including the six revised for the migration pack),
except **Q18**: I suggest local time for
`--asOf today`, while record time stays UTC. Worth a second look from you, because
they depend on your data rather than on code:

- **Q3**: whether custom vault schemas should default occurrent classes to `occasion`;
- **Q7**: what "due date" means in your governance documents;
- **Q14**: the travel word list;
- **Q11**: whether new conflicts on journal activities are acceptable.
