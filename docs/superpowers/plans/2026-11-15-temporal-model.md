# Temporal model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every stored date mean one thing (valid time with start, end, precision and time of day; record time with `_recorded_at` / `_retired_at` / `_retired_by`), date the units retrieval returns, make dated happenings (`identity: occasion` classes, keyed by name and day) the route for "every year / how many times / when did I" questions, and close validity on supersession. Spec: `docs/superpowers/specs/2026-11-15-temporal-model-design.md`.

**Architecture:** One parser, `temporal.parse_valid`, feeds every valid-time write (`_document_valid_time`, `lift_document_dates`, `_fact_valid_time`, `retime`, `table2graph`, `update`). One reader, `schema_validate.class_identity`, decides per class whether same-name mentions are one entity (`name`), one per day (`occasion`) or one label (`category`); `observations.aggregate_key(..., occurs_on=)` adds the day to the key's name part for `occasion` classes. One predicate, `graph_query.valid_predicate`, replaces `asof_predicate`. `ingest retime` (no LLM) carries every change to existing staging, and gains a re-key phase that rewrites same-as and curation records before its single rebuild. Record time is stamped inside `_commit_document_tx` and every relabel to History.

**Tech Stack:** Python 3.14, Click, pytest + `CliRunner`, `test/_neo4j_recorder.py`, Neo4j (real-run verification only), `uv`.

**Companion:** `docs/superpowers/plans/2026-11-15-schema-bound-properties.md` (below, "SBP"). Shared files: `artmind/retime.py`, `artmind/schema_validate.py`, `artmind/prompt_builder.py`, `artmind/ingest.py` (`_build_observations`). Neither plan blocks the other except where a task here says **Blocked by SBP Task N**. Rules for the overlap:

- `retime.retime_observations`: SBP Task 6 changes its return to `(observations, dropped)`. Tasks 6, 15, 17 and 19 here add to the observation dict it builds. Whichever plan lands second keeps the other's return shape and adds its own lines; the tests here unpack with `_obs(retime_observations(...))`, a helper that accepts both shapes (Task 6, Step 5).
- `prompt_builder._render_anchor_date_section`: SBP Task 3 scopes it to the chunk's classes; Task 8 here changes its text. Text and scoping are separate lines; rebase onto whichever landed.
- `schema_validate`: both plans add checks in new functions; neither edits the other's.
- Reused, not repeated: the property audit (SBP Tasks 8–9) gains the date columns in Task 11 here (**Blocked by SBP Task 8**); OBJECT's `type` values outside the declared list (Task 19) are reported by the same audit (**Blocked by SBP Task 8** for that column only); banking's newly declared happening dates are filled by `backfill-properties` (SBP Task 10) in the per-vault rollout, not by a task here.

---

## Questions for review

Each item is something in the spec that is wrong against the code, unclear, or that I would change. The plan follows my proposal; if you decide otherwise, change the task named.

1. **The day must go in the key's name part, not a fourth part (§5.2).** The spec writes `name|class|domain|YYYY-MM-DD`. Ten parsers treat a key with other than three parts as malformed and skip it: `projection._parse_key` (used by `all_keys`, `keys_for_document`, the same-as and retraction paths), `same_as._parse_key`, `sameas._parse_key`, `refine_graph._parse_key`, `ingest._commit_document_tx` (`o["key"].count("|") == 2`), `conflicts` (`split("|")` into a 3-tuple), `synthesize`, `graph_snapshot`, `webui/curate_routes`, `update._stored_identity`. A four-part key would make `full_rebuild` skip every `occasion` entity without an error. **Proposal (Task 15):** `aggregate_key(..., occurs_on="2024-03-30")` returns `("tennis~2024-03-30", "ACTIVITY", "personal_journal")`. `~` is the separator `document_identity.document_key` already uses. `observations.split_occasion(name_part) -> (name, day | None)` reads it back. Only the name-matching queries (`entity_history --name`, `query happenings`) need to know.

2. **An undated mention whose document date is only the default (§5.2).** "An undated mention keys on its document's date" is right for a journal entry, but a document dated by `default_ingestion` would key its happenings on the ingestion day. **Proposal (Task 15):** when the observation's `_valid_precision` is `none`, no day is added; the mention keys on its name, as today.

3. **The default `occasion` for every `occurrent` class changes ids in every schema (§5.3).** Measured on banking_corpus's staging: only 2 TRANSACTION keys and 3 AUDIT_FINDING keys span two days, so banking barely changes. contracts' AMENDMENT is `occurrent`, is a `document_entity` class (one name per document by design), and tags `effective_date: valid_from`; day keys could split one amendment when two documents state its date differently. **Proposal (Task 14):** declare `identity` explicitly in every shipped schema, set contracts AMENDMENT to `identity: name`, and make `domains validate` skip the "`name` with a `temporal: valid_from` property" WARN for `document_entity` classes.

4. **The meta-schema tells the model to put dates IN occurrent names.** `meta.yaml` `kind_naming_rules.occurrent` says "a date or identifier that distinguishes one occurrence from another belongs IN the name". That rule is why 1,958 of 2,822 staged EVENT observations in brain01 carry a date in their canonical name. A vault's `meta.yaml` is the user's copy and is not refreshed by `setup` (SBP question 3). **Proposal (Task 17):** a new `identity_naming_rules.occasion` in `meta.yaml`, with the same text as a code default in `prompt_builder` (`OCCASION_NAMING_RULE`) used when a vault's `meta.yaml` lacks the key; for an `occasion` class it replaces the kind rule. The occurrent kind rule stays for `identity: name` occurrent classes.

5. **Half of the dated event names already fold across days.** `normalize_name`'s measurement-tail rule strips a dash or colon tail that holds a digit, so "Tennis - 2024-03-30" and "Tennis - 2024-04-02" already share the key `tennis`: about 1,060 of the 1,958 dated names have this form. About 900 put the date after a word with no separator ("Tennis on 2024-03-30") and stay separate. So the stripping rule (§5.2) must cover the no-separator, weekday and ordinal forms, and the day key is what splits the already-folded ones. **Proposal (Task 17):** `observations.strip_occasion_date(name)`, pure, applied where keys are built (`_build_observations`, `retime`), not in `canonicalize` (the LLM step). The spec's "under 5% of occasion names contain a date" is checked on `canonical_name`, which this also rewrites; the raw `name` keeps the model's wording.

6. **The unprefixed `valid_to` fold (§7.1) must move to step 4.** Superseded documents carry only the unprefixed `valid_to` (`supersession_records._RECOMPUTE`). Step 4's `valid_predicate` "compares against `_valid_from` and `_valid_to` only", so between steps 4 and 7 every superseded document would read as open. **Proposal (Task 13):** the fold migration, and `_RECOMPUTE` writing `_valid_to`, ship with the predicate in Part 4.

7. **STATE_CHANGE.due_date appears twice in §4.** It is listed as a deadline (→ `temporal: due`) and as a duplicate of `date_or_time` to fold with `property_aliases`. It cannot be both. **Proposal (Task 10):** `date_or_time` becomes `temporal: valid_from`, `due_date` becomes `temporal: due`, no alias. CASE's `opened_date` → `opened` alias stays.

8. **Same-as folds relabel nothing (§6.1).** A same-as merge happens in the projection: member observations stay `:Observation`, and only the member's `:Entity` node is deleted. There is no node to stamp `_retired_by: sameas:<id>` on. The spec also misses `projection.apply_retractions`, which does relabel an observation to History. **Proposal (Task 23):** drop `sameas:`; add `retract:<observation id>`.

9. **Record-time backfill for History nodes (§6.2, §8).** `Document.ingested_at` is the latest extraction, so it is later than when a History observation was first recorded. **Proposal (Task 25):** current nodes get `_recorded_at = ingested_at` and `_record_time_approx = true`; History nodes get `_retired_at = ingested_at` (approximate) and no `_recorded_at`; `--knownAsOf` counts a node with no `_recorded_at` as recorded before the backfill, and warns. Then the spec's acceptance test ("before a note's last re-ingest returns the earlier version") passes on backfilled data.

10. **New underscore fields must be system keys in the projection.** `projection._OBSERVATION_SYSTEM_KEYS` lists the observation keys the merge skips; any other key is merged as a domain property, and differing values become conflicts. `_valid_precision`, `_valid_time_of_day`, `_due`, `_review`, `_revised_at`, `_recorded_at`, `_revived_at`, `_retired_at`, `_retired_by` and `_record_time_approx` must join it (Tasks 6, 10, 22). The winner sort (§6.2) must treat a `_valid_from` with precision `none` as empty, or the ingestion date beats every real date (Task 28).

11. **ACTIVITY and PLAN becoming occurrent adds conflicts.** For an occurrent class, two same-key observations that disagree on a scalar property raise a `:Conflict`. Day keys make keys smaller, but same-day mentions with different `emotional_valence` or `purpose` will now conflict, and an open conflict blocks `projection synthesize`. SBP's property trim reduces this. Accept, and measure the open-conflict count before and after (Task 30).

12. **Same-as migration semantics (§5.2, §8).** An old group such as `[tennis|ACTIVITY|pj, tennis match|ACTIVITY|pj]` names two folds that each span many days. **Proposal (Task 20):** expand each `occasion` member into its new day keys; keep one group per day on which at least two members have observations, canonical = the old canonical's key for that day; drop members with no same-day partner; report the counts. `category` members' groups are dropped. The same rule guards new proposals centrally in `sameas.propose`, so `--fromFile` proposals cannot reintroduce cross-day pairs.

13. **One command for the migration.** The spec says "`retime` … then one `projection rebuild` re-keys … and migrates same-as records", but nothing rewrites `same_as.yaml` or curation records today. **Proposal (Task 20):** `ingest retime` gains a re-key phase (`artmind/rekey.py`): it compares each staged observation's key before and after, rewrites `same_as.yaml`, closes open proposals, and re-points synthesis, conflict and mart records, all before its one rebuild. The tier-1 dry run lists all of it.

14. **The OBJECT → ACTIVITY move needs a rule somewhere (§5.3).** **Proposal (Task 19):** a declarative schema block, `retime_reclassify: [{from: OBJECT, to: ACTIVITY, name_words: [...]}]`, read only by `retime` and checked by `domains validate`. It keeps vault-specific words out of code and lets a vault adjust them. The shipped list is generic travel words (flight, train, cab, taxi, ride, trip, journey, booking).

15. **A chunk's own date (§5.1) needs a narrow rule.** **Proposal (Task 12):** a chunk gets its own `_valid_from` only when its `header_path`'s last heading, or its first non-blank line, is a single date that `parse_valid` reads at day precision; otherwise the document's. In brain01 every entry is one day, so this changes nothing there; it matters for multi-day notes.

16. **`query happenings` and `type` (§5.2).** `entity_name_ft` indexes `name`, `description` and `aliases`, not `type`, and matching on `description` would pull in every happening whose description mentions the term. **Proposal (Task 21):** a field-scoped Lucene query (`name:(…) OR aliases:(…)`) on the existing index, plus `toLower(e.type) = toLower($term)`. No index change.

17. **Record time on derived nodes (§6.1).** `:Entity`, `RELATES_TO` and `:Conflict` are rebuilt from observations on every rebuild. **Proposal (Task 22):** stamp `_recorded_at` on Document, DocChunk, DocPassage, DocSummary, Observation and `ASSERTS_RELATION`; on `:Entity` only `ON CREATE`; never on `RELATES_TO` or `:Conflict`.

18. **`resolve_as_of('today')` uses local time.** §6.1 moves record time to UTC; `--asOf today` is valid time, but one clock is simpler. **Proposal (Task 2):** UTC there too.

19. **`H1/H2 YYYY` needs a precision value.** §2 lists `day`, `month`, `quarter`, `year`; §3.1 accepts halves. **Proposal (Task 6):** add `half`.

20. **The "`identity: name` with a `temporal: valid_from` property" WARN is too broad (§5.2, §5.3).** 20 recurrent classes in shipped schemas tag a `valid_from` property (banking RATE_ENTRY, PRODUCT, POLICY, contracts TERM, journal ACTIVITY before this change, …). They are state that changes over time, which is exactly what `kind: recurrent` + `identity: name` is for; the WARN would fire on all of them. **Proposal (Task 14):** WARN only on `identity: name` with `kind: occurrent` and a `valid_from` property (a happening whose days will fold), and never for `document_entity` classes.

21. **Tables keep their declared identity.** `table2graph` keys entities by its mapping's rendered name (`identity=`). Day keys would split a declared identity. **Proposal (Task 15):** observations with `source_kind: table` are never day-keyed, whatever the class's `identity`.

---

## File structure

| File | Responsibility | Tasks |
|---|---|---|
| `artmind/temporal.py` | `ValidTime`, `parse_valid`; `parse_iso` becomes a wrapper; `lift_document_dates` (no raw values, `revised`, `valid_to` labels); `widen_as_of`; supersession effects | 2, 5, 6, 10, 23, 26 |
| `artmind/graph_query.py` | `resolve_as_of` (widen, UTC); `valid_predicate` replaces `asof_predicate`; `known_predicate`; timeline by identity; `entity_history`/`document_history`/`pattern10` `known_as_of`; `happenings` | 2, 13, 15, 16, 21, 24 |
| `artmind/vector_query.py` | passage legs call `valid_predicate` | 13 |
| `artmind/survey.py`, `artmind/topics/grid.py`, `artmind/topics/members.py`, `artmind/conflicts.py`, `artmind/webui/curate_routes.py` | prefixed date reads (no `coalesce` with unprefixed) | 13 |
| `artmind/ingest.py` | `FactTime`, `_fact_valid_time`; `_build_observations` (precision, time of day, due/review, day key, name strip, counts); chunk dates; record-time stamps in `_commit_document_tx`, `_retract_prior_version`, `_merge_relabeled` | 6, 7, 10, 12, 15, 17, 22, 23 |
| `artmind/observations.py` | `build_observation` new fields; `aggregate_key(occurs_on=)`; `split_occasion`; `strip_occasion_date` | 6, 15, 17 |
| `artmind/projection.py` | `_OBSERVATION_SYSTEM_KEYS`; entity copies precision/time of day; `_winner_sort_key(kind)` | 6, 22, 28 |
| `artmind/retime.py` | `parse_valid`; `_kind` re-derived; chunk dates; day re-key; name strip; reclassify; re-key phase | 6, 12, 15, 17, 19, 20 |
| `artmind/rekey.py` (new) | old→new key map from staging; `same_as.yaml`, proposals, synthesis/conflict/mart records | 20 |
| `artmind/date_audit.py` (new) | `ingest date-audit` rows | 9, 11 |
| `artmind/schema_validate.py` | `class_identity`; temporal-value, identity, `types`, `retime_reclassify` checks | 10, 14, 19 |
| `artmind/prompt_builder.py`, `artmind/domains/meta.yaml` | DATES section forms; occasion naming rule; `types` rendering | 8, 17, 19 |
| `artmind/canonicalize.py` | `recurrent_classes` → `named_classes` | 18 |
| `artmind/sameas.py`, `artmind/refine_graph.py`, `artmind/conflicts.py`, `artmind/insights/detectors/same_as.py`, `artmind/identity.py`, `artmind/cli.py` (entity-resolve) | identity gate | 18 |
| `artmind/summary_store.py`, `artmind/passages.py`, `artmind/embed_sweep.py` | prefixed and document dates on summaries and passages | 3, 12 |
| `artmind/supersession_records.py`, `artmind/lifecycle.py`, `artmind/lifecycle_records.py` | `_valid_to`; validity close and restore; record stamps; `recorded_at` | 13, 23, 26 |
| `artmind/table2graph.py` | `parse_valid`; SCD2 `_valid_to` | 6, 27 |
| `artmind/update.py`, `artmind/structured/pipeline.py` | UTC; `parse_valid`; occasion day | 4, 6, 15 |
| `artmind/migrations/document_valid_time_names.py` (new), `artmind/migrations/record_time_backfill.py` (new), `artmind/setup.py` | §7.1 fold; record-time backfill | 13, 25 |
| `artmind/cli.py`, `artmind/cli_conventions.py`, `artmind/traces/path.py` | `ingest date-audit`; `query happenings`; `--knownAsOf`; `COMMAND_GROUPS`; `EFFECT_KINDS` | 9, 20, 21, 24 |
| `artmind/domains/schemas/*.yaml`, `artmind/skills/artmind-create-schema/assets/personal_journal_schema.yaml` | §4 fixes; identity; journal ACTIVITY/PLAN/EMOTION/OBJECT | 10, 14, 19 |
| Skills, `justfile`, `CLAUDE.md`, plugin fixtures | docs | 1, 21, 29 |

## How to verify beyond the hermetic suite

- **Run this worktree's code, not the global command.** The global `artmind` is an editable install of the main checkout. Inside a vault:
  - `export WT=/Users/surjitdas/Projects/artmind9/.claude/worktrees/plan-temporal-model-ab0bc4`
  - `ARTMIND_NO_PROXY=1 uv run --project $WT artmind …`
- **Always `ARTMIND_NO_PROXY=1`**: a running `serve`, chat-ui or admin-ui answers with old code.
- **`env -u ANTHROPIC_BASE_URL`** in front of any command that calls the LLM. In this plan that is only Task 30's re-extraction sample and benchmark runs; every other task is LLM-free.
- **brain01** (private, local Neo4j :7689, configured in the vault's `config.env`): print counts, class names and key shapes only — never names, values or text. Use **banking_corpus** (public) for anything that prints values.
- **Never run `artmind setup` or `artmind init` from the checkout.** A migration that `setup` runs is exercised inside a vault, with the user's yes.
- **Ask before any real write to a vault.** `--dryRun` is always safe; a real `retime` rewrites committed staging and the graph.

## What breaks if a vault runs Part 5's code before its migration

Part 5 changes entity ids for `occasion` classes. Code that computes keys (`_build_observations`, `update`) starts producing day keys at once, while the graph and the vault's curation still hold old ones. Until `ingest retime --domain d` runs (Task 20):

- **Mixed keys.** A note re-ingested after the upgrade writes `tennis~2024-03-30|ACTIVITY|pj`; untouched notes still have `tennis|ACTIVITY|pj`. Both show as entities; `query happenings` finds both (it matches on the name part), but `entity-context` on the old entity shows fewer days each time a note is re-ingested.
- **Same-as groups stop merging.** `same_as.yaml` members name old keys; a re-ingested note's observations no longer carry them, so the group silently merges less (same_as.py logs nothing for a key with no observations; catalogue health `same_as_dangling` lists them).
- **Curation records orphan.** `syntheses/<entity id>.json` and `conflicts/<id>.json` name old ids; the synthesis for a re-keyed entity is not applied (the entity gets the winner's description), and a closed conflict re-opens as a new id.
- **Mart dimension records** (`mart_dimension`, `member_key = name|CLASS|domain`) stop matching; `mart build` treats re-keyed happenings as unenriched.
- **Insights snapshots** keep old `_id`s; the next `insights refresh` reports old findings as gone and re-keyed ones as new (a noisy diff, no data loss).
- **Caches** (`logs/plan-catalog/`, `logs/topic-freshness/`, `logs/results/`) hold old ids; the catalogue revalidates on the `catalogue.state` generation, which every commit bumps, so these self-heal.
- **Saved views and topic plans** in brain01 hold no `occasion` entity id (checked: `domains/views`, `domains/topic_plans` contain none); a view that passes an entity param by id would get "no entity with _id" for a re-keyed happening.

Guard (Task 20, Step 7): `projection rebuild`, `vault sync` and `ingest sync|async` warn once, with the `ingest retime --domain d --dryRun` command, when the domain's schema declares `occasion` classes and the graph's `:ProjectionState` records no `identity_version` ≥ 1 for it. The rebuild after `retime` writes it. The warning never blocks.

---

## Part 1 — Defect fixes (spec §1.5, rollout step 1)

### Task 1: Correct the `--asOf` skill and help text

**Files:**
- Modify: `artmind/skills/artmind-query/references/temporal.md` lines ~19–30.
- Modify: `artmind/skills/artmind-query/SKILL.md` line ~341 (the `entity-history` route row).
- Test: `test/test_temporal_skill_text.py` (new).

- [ ] **Step 1: Write the failing test.**

```python
"""The query skill describes entity-history --asOf as a valid-time floor (spec 2026-11-15 §1.5)."""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"


def test_temporal_reference_does_not_call_entity_history_a_point_in_time_filter():
    text = (SKILL / "references" / "temporal.md").read_text(encoding="utf-8")
    assert "genuine" not in text
    assert "entity-history --asOf" in text and "floor" in text


def test_entity_history_help_says_floor():
    from artmind.cli import cli

    cmd = cli.commands["query"].commands["entity-history"]
    help_text = next(p.help for p in cmd.params if p.name == "as_of")
    assert "floor" in help_text
```

- [ ] **Step 2: Run it and confirm it fails.** `uv run --group dev pytest test/test_temporal_skill_text.py -v` — expected: the first test FAILs on `"genuine"`.

- [ ] **Step 3: Edit the text.** In `temporal.md`, replace the sentence "`entity-history` is the one command with a genuine point-in-time `--asOf`, because it's reading the fact-level valid-time axis directly." with:

```markdown
`entity-history --asOf` is a valid-time floor too: it keeps observations whose own
`_valid_from` is on or before the date and ignores `_valid_to`. Read the rows' dates
yourself when the question is about one moment.
```

In `SKILL.md` line ~341, change `[--asOf <date>]` to `[--asOf <date>]` (a floor: facts dated on or before it). The CLI help already says "floor"; the second test guards it.

- [ ] **Step 4: Run it and confirm it passes.** Same command — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/skills/artmind-query test/test_temporal_skill_text.py
git commit -m "docs(skill): entity-history --asOf is a floor, not a point in time"
```

Task 24 rewrites this text again once `--asOf` honours `_valid_to`.

---

### Task 2: Year- and month-only `--asOf` widen to the period's last day

**Files:**
- Modify: `artmind/graph_query.py` — `resolve_as_of` (lines ~170–190).
- Modify: `artmind/temporal.py` — add `widen_as_of`.
- Test: `test/test_temporal.py`, `test/test_query_layer.py` (append).

- [ ] **Step 1: Write the failing tests.** Append to `test/test_temporal.py`:

```python
from artmind.temporal import widen_as_of


@pytest.mark.parametrize("value, expected", [
    ("2026", "2026-12-31"),
    ("2026-02", "2026-02-28"),
    ("2024-02", "2024-02-29"),
    ("2026-05-01", "2026-05-01"),
    ("2026-05-01T10:00", "2026-05-01T10:00"),
])
def test_widen_as_of_reaches_the_last_day(value, expected):
    assert widen_as_of(value) == expected
```

Append to `test/test_query_layer.py`:

```python
def test_year_only_as_of_includes_that_years_dates():
    from artmind.graph_query import resolve_as_of

    assert "2026-05-01" <= resolve_as_of("2026")          # text comparison, as the Cypher does


def test_today_resolves_in_utc(monkeypatch):
    import datetime as dt

    from artmind import graph_query

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 1, 1, 23, 30, tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(graph_query, "datetime", Frozen)
    assert graph_query.resolve_as_of("today") == "2026-01-01"
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_temporal.py test/test_query_layer.py -k "widen or as_of" -v` — expected: ImportError for `widen_as_of`, then the year test FAILs.

- [ ] **Step 3: Implement.** In `artmind/temporal.py`, below `find_date`:

```python
import calendar


def widen_as_of(value: str) -> str:
    """A year or month `--asOf` as its last day, so `'2026-05-01' <= asOf` holds for 2026.

    A day, or a day with a time, is returned unchanged. Valid-time strings compare as text,
    and `'2026-05-01' <= '2026'` is false -- the defect this fixes (spec 2026-11-15 §1.5).
    """
    if re.fullmatch(r"\d{4}", value):
        return f"{value}-12-31"
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return f"{value}-{calendar.monthrange(year, month)[1]:02d}"
    return value
```

In `artmind/graph_query.py`, import `datetime, timezone` beside the existing `date` import, and change `resolve_as_of`:

```python
    if v.lower() in ("today", "now"):
        return datetime.now(timezone.utc).date().isoformat()
    if not _ASOF_RE.match(v):
        raise ValueError(
            f"--asOf must be an ISO date (YYYY[-MM[-DD]]) or 'today'/'now'; got {value!r}"
        )
    from artmind.temporal import widen_as_of

    return widen_as_of(v)
```

Docstring: a year or month is widened to its last day.

- [ ] **Step 4: Run and confirm.** Same command, then `uv run --group dev pytest test/test_query_layer.py test/test_text2sql.py test/test_valid_from_property.py -v` — expected: PASS. A test that asserted `resolve_as_of("2026") == "2026"` is updated to `"2026-12-31"`.

- [ ] **Step 5: Commit.**

```bash
git add artmind/temporal.py artmind/graph_query.py test/test_temporal.py test/test_query_layer.py
git commit -m "fix(query): a year or month --asOf covers the whole period; today is UTC"
```

**Beyond the suite:** in banking_corpus, `ARTMIND_NO_PROXY=1 uv run --project $WT artmind docs list --domain banking --asOf 2026 --compact | python3 -c "import json,sys; print(len(json.load(sys.stdin)['rows']))"` returns more rows than the same command with the global `artmind` (which drops 2026-dated documents).

---

### Task 3: Summaries read the document's prefixed dates

**Files:**
- Modify: `artmind/summary_store.py` — `summary_rows` (lines ~38–60).
- Test: `test/test_summary_store.py` (append).

- [ ] **Step 1: Write the failing test.**

```python
def test_summary_rows_carry_the_documents_prefixed_valid_time():
    from artmind.summary_store import summary_rows

    data = {"document": {"summary": "S", "key": "k"}, "chunks": [{"chunk_id": "c1", "summary": "C", "key": "k1"}]}
    document = {"id": "d1", "_domain": "pj", "_valid_from": "2024-03-30", "_valid_to": "2024-04-01"}
    for row in summary_rows(data, document):
        assert row["props"]["_valid_from"] == "2024-03-30"
        assert row["props"]["_valid_to"] == "2024-04-01"
        assert "valid_from" not in row["props"]
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_summary_store.py -v` — expected: FAIL, no `_valid_from`.

- [ ] **Step 3: Implement.** In `summary_rows`, replace the field loop:

```python
    for field in ("_valid_from", "_valid_to"):
        if document.get(field) is not None:
            common[field] = document[field]
```

Task 12 adds `_doc_valid_from`/`_doc_valid_to` and per-chunk dates on top of this.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_summary_store.py test/test_summary_build.py -v` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/summary_store.py test/test_summary_store.py
git commit -m "fix(summaries): read the document's _valid_from, not an unprefixed key it never has"
```

---

### Task 4: One clock for record dates: UTC

**Files:**
- Modify: `artmind/update.py` lines ~369–370 (`write_user_chat`).
- Modify: `artmind/structured/pipeline.py` lines ~153 and ~549.
- Test: `test/test_utc_clocks.py` (new).

- [ ] **Step 1: Write the failing test.** Both dates are computed inline, so it scans the modules' source.

```python
"""Record dates use UTC everywhere (spec 2026-11-15 §1.5, §6.1)."""
import inspect

from artmind import update
from artmind.structured import pipeline


def test_no_local_date_today_in_record_paths():
    for module in (update, pipeline):
        source = inspect.getsource(module)
        assert "date.today()" not in source, module.__name__
        assert "datetime.now().isoformat()" not in source, module.__name__
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_utc_clocks.py -v` — expected: FAIL for both modules.

- [ ] **Step 3: Implement.** In `update.py`:

```python
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    today = now_dt.date().isoformat()
```

(add `timezone` to its `datetime` import). In `structured/pipeline.py`, both `date.today().isoformat()` become `datetime.now(timezone.utc).date().isoformat()`.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_utc_clocks.py test/test_update.py $(rg -l "structured.pipeline|from artmind.structured import pipeline" test) -v` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/update.py artmind/structured/pipeline.py test/test_utc_clocks.py
git commit -m "fix: update and SCD2 batch dates use UTC"
```

---

### Task 5: Unparsed values never reach `_valid_from`

**Files:**
- Modify: `artmind/temporal.py` — `lift_document_dates` (line ~219: `out[canon] = iso if iso else raw`).
- Modify: `artmind/ingest.py` — `_document_valid_time` copies frontmatter `_valid_from` as `str(...)` (lines ~3549–3556).
- Check: `artmind/table2graph.py` `_iso` (line ~885).
- Test: `test/test_temporal.py`, `test/test_document_dates.py` (append).

- [ ] **Step 1: Write the failing tests.**

```python
# test/test_temporal.py
def test_unreadable_header_date_is_not_stored_as_valid_from():
    out = lift_document_dates("| Effective Date | next review |\n", {}, {"valid_from": ["Effective Date"]})
    assert "valid_from" not in out
    assert out.get("unparsed") == ["valid_from"]
```

```python
# test/test_document_dates.py
def test_unreadable_frontmatter_valid_from_falls_through(tmp_path):
    md = tmp_path / "n.md"
    md.write_text("---\n_valid_from: sometime soon\n---\nBody\n", encoding="utf-8")
    out = ing._document_valid_time(md, {"_valid_from": "sometime soon"}, {"temporal": {}}, filename="n.md")
    assert "_valid_from" not in out
```

If `_find_header_date` already refuses the header value (it calls `find_date`), make the first test's header value one `find_date` reads but `parse_iso` does not; check with `uv run python -c "from artmind.temporal import _find_header_date as f; print(f('| Effective Date | next review |', ['Effective Date']))"`. The frontmatter branch (`frontmatter.get(lbl)`) is the one that stores raw text today.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_temporal.py test/test_document_dates.py -k unreadable -v` — expected: both FAIL.

- [ ] **Step 3: Implement.** In `lift_document_dates`, replace the `else:` branch of `if canon == "version"`:

```python
        else:
            iso = parse_iso(raw)
            if iso:
                out[canon] = iso
            else:
                # Never store text as a date: it sorts wrongly against ISO dates (spec §1.5).
                out.setdefault("unparsed", []).append(canon)
```

and set `out["time_source"]` only when a dated key or version was stored (`if any(k in out for k in ("valid_from", "valid_to", "version"))`); the default-date branch runs when none was. In `_document_valid_time`:

```python
    from artmind.temporal import lift_document_dates, parse_iso

    out: dict = {}
    for key in ("_valid_from", "_valid_to"):
        if frontmatter.get(key):
            iso = parse_iso(str(frontmatter[key]))
            if iso:
                out[key] = iso
```

Confirm `table2graph._iso` returns `None` for unreadable values (`rg -n "def _iso" -A10 artmind/table2graph.py`); if it returns the raw value, apply the same rule.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_temporal.py test/test_document_dates.py test/test_table2graph.py -v` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/temporal.py artmind/ingest.py artmind/table2graph.py test/test_temporal.py test/test_document_dates.py
git commit -m "fix(temporal): an unreadable date is counted, never stored as _valid_from"
```

**Beyond the suite** (no LLM, read-only): in banking_corpus, `ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain banking.cases --dryRun --compact` — the effect lists only folders whose document date was stored as text.

---

## Part 2 — `parse_valid`, the fact mapping and `retime` (spec §3, rollout step 2)

### Task 6: `temporal.parse_valid` and the fact mapping

**Files:**
- Modify: `artmind/temporal.py` — `ValidTime`, `parse_valid`; `parse_iso` becomes a wrapper.
- Modify: `artmind/ingest.py` — `FactTime`; `_fact_valid_time` (lines ~3598–3618); `_document_precision`; `_build_observations` (lines ~3645–3720).
- Modify: `artmind/observations.py` — `build_observation` gains `valid_precision`, `valid_time_of_day`.
- Modify: `artmind/projection.py` — `_OBSERVATION_SYSTEM_KEYS` (lines ~40–54); `merge_observations` copies precision and time of day (lines ~406–411).
- Modify: `artmind/retime.py` — `_OBSERVATION_TIME_KEYS` (line ~45); `retime_observations` (lines ~67–99).
- Modify: `artmind/table2graph.py` lines ~1086–1093; `artmind/update.py` where it derives a fact's dates.
- Test: `test/test_parse_valid.py` (new), `test/test_observation_valid_time.py` (append), `test/test_retime_valid_time.py` (new).

- [ ] **Step 1: Write the failing parser tests.** `test/test_parse_valid.py`:

```python
"""`temporal.parse_valid` (spec 2026-11-15 §3.1). Invented values only."""
import pytest

from artmind.temporal import ValidTime, parse_iso, parse_valid


@pytest.mark.parametrize("value, expected", [
    ("2026-01-07", ValidTime("2026-01-07", None, "day", None)),
    ("2026-01-07 (evening)", ValidTime("2026-01-07", None, "day", "evening")),
    ("2026-07-02 09:18 UTC", ValidTime("2026-07-02", None, "day", "09:18 UTC")),
    ("2023-09-07 to 2023-09-08 (multi-day)", ValidTime("2023-09-07", "2023-09-09", "day", "multi-day")),
    ("2026-01-27/28", ValidTime("2026-01-27", "2026-01-29", "day", None)),
    ("2026-01-27 – 2026-02-03", ValidTime("2026-01-27", "2026-02-04", "day", None)),
    ("from 2026-03-01 until 2026-03-05", ValidTime("2026-03-01", "2026-03-06", "day", None)),
    ("2026-06 (planned)", ValidTime("2026-06-01", None, "month", "planned")),
    ("March 2026", ValidTime("2026-03-01", None, "month", None)),
    ("2026-Q1", ValidTime("2026-01-01", None, "quarter", None)),
    ("Q3 2026", ValidTime("2026-07-01", None, "quarter", None)),
    ("H2 2026", ValidTime("2026-07-01", None, "half", None)),
    ("2026 (coming)", ValidTime("2026-01-01", None, "year", "coming")),
    ("15 March 2026", ValidTime("2026-03-15", None, "day", None)),
    ("15/03/2026", ValidTime("2026-03-15", None, "day", None)),
])
def test_parse_valid(value, expected):
    assert parse_valid(value) == expected


def test_range_precision_is_the_coarser_end():
    got = parse_valid("2026-01-15 to 2026-03")
    assert (got.precision, got.end) == ("month", "2026-04-01")


@pytest.mark.parametrize("value", ["Ongoing", "TBD", "next review", "", None, "today"])
def test_no_date(value):
    assert parse_valid(value) is None


def test_time_of_day_is_capped():
    assert len(parse_valid("2026-01-07 (" + "x" * 80 + ")").time_of_day) == 40


@pytest.mark.parametrize("value, end", [("2026-Q1", "2026-04-01"), ("2026-02", "2026-03-01"),
                                        ("2026", "2027-01-01"), ("2026-02-03", "2026-02-04"),
                                        ("H2 2026", "2027-01-01")])
def test_implied_end_of_a_partial_date(value, end):
    assert parse_valid(value).implied_end == end


def test_parse_iso_is_the_start():
    assert parse_iso("2023-09-07 to 2023-09-08") == "2023-09-07"
    assert parse_iso("2026-Q1") == "2026-01-01"
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_parse_valid.py -v` — expected: ImportError.

- [ ] **Step 3: Implement `parse_valid`.** In `artmind/temporal.py`:

```python
from dataclasses import dataclass
from datetime import date, timedelta

#: How much of a date the source stated. `none`: a default date, not a real one (spec §2).
PRECISIONS = ("day", "month", "quarter", "half", "year", "none")
_COARSENESS = {p: i for i, p in enumerate(PRECISIONS)}
TIME_OF_DAY_MAX = 40

_RANGE_RE = re.compile(
    r"^(?:from\s+)?(?P<a>.+?)\s*(?:\bto\b|\buntil\b|\s[-–—]\s|–|—)\s*(?P<b>.+)$", re.IGNORECASE
)
_SLASH_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{1,2})$")
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:[A-Z]{2,5}|[+-]\d{2}:?\d{2}))?)")
_QUALIFIER_RE = re.compile(r"\(([^()]*)\)\s*$")
_QUARTER_RE = re.compile(r"^(?:(\d{4})-?Q([1-4])|Q([1-4])\s+(\d{4}))$", re.IGNORECASE)
_HALF_RE = re.compile(r"^H([12])\s+(\d{4})$", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")


@dataclass(frozen=True)
class ValidTime:
    """A parsed valid-time value: `start`/`end` ISO dates (`end` exclusive), how much of the
    date was stated, and the time or qualifier text the source gave (never compared)."""

    start: str
    end: str | None
    precision: str
    time_of_day: str | None

    @property
    def implied_end(self) -> str:
        """The exclusive end the precision implies: the next day, month, quarter, half or year."""
        y, m, d = (int(x) for x in self.start.split("-"))
        if self.precision == "day":
            return (date(y, m, d) + timedelta(days=1)).isoformat()
        months = {"month": 1, "quarter": 3, "half": 6, "year": 12}.get(self.precision)
        if not months:
            return self.start
        total = (y * 12 + m - 1) + months
        return date(total // 12, total % 12 + 1, 1).isoformat()


def _parse_day(v: str) -> str | None:
    """A day-precision date: today's `parse_iso` body, minus the partial-ISO branch
    (now in `_one_date`)."""
    m = _NUMERIC_RE.match(v)
    if m:
        first, second, year = (int(g) for g in m.groups())
        day, month = (second, first) if first <= 12 < second else (first, second)
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    m = _ISO_FULL_RE.match(v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = _DMY_RE.match(v)
    if m and m.group(2).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = _MDY_RE.match(v)
    if m and m.group(1).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def _one_date(text: str) -> tuple[str, str] | None:
    """(start, precision) of a single full or partial date, or None."""
    v = text.strip().rstrip(".,;")
    m = _QUARTER_RE.match(v)
    if m:
        year = int(m.group(1) or m.group(4))
        quarter = int(m.group(2) or m.group(3))
        return f"{year:04d}-{3 * (quarter - 1) + 1:02d}-01", "quarter"
    m = _HALF_RE.match(v)
    if m:
        return f"{int(m.group(2)):04d}-{1 if m.group(1) == '1' else 7:02d}-01", "half"
    m = _MONTH_YEAR_RE.match(v)
    if m and m.group(1).lower() in _MONTHS:
        return f"{int(m.group(2)):04d}-{_MONTHS[m.group(1).lower()]:02d}-01", "month"
    m = _ISO_PARTIAL_RE.match(v)
    if m:
        return (f"{m.group(1)}-{m.group(2)}-01", "month") if m.group(2) else (f"{m.group(1)}-01-01", "year")
    iso = _parse_day(v)
    return (iso, "day") if iso else None


def parse_valid(value) -> ValidTime | None:
    """Parse one valid-time value (spec 2026-11-15 §3.1). Pure; None when it holds no date.

    A range (`A to B`, `A - B`, `A – B`, `A/B` with a day-only `B`, `from A until B`) gives
    `end = B`'s implied end, exclusive. A time (`HH:MM`, optional zone) or a trailing
    parenthesised qualifier goes to `time_of_day` (≤ 40 chars) and never moves the date.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    notes: list[str] = []
    q = _QUALIFIER_RE.search(text)
    if q:
        if q.group(1).strip():
            notes.append(q.group(1).strip())
        text = text[: q.start()].strip()
    t = _TIME_RE.search(text)
    if t:
        notes.insert(0, t.group(1).strip())
        text = (text[: t.start()] + text[t.end():]).strip()

    parts = None
    slash = _SLASH_DAY_RE.match(text)
    if slash:
        start = slash.group(1)
        parts = [(start, "day"), (f"{start[:8]}{int(slash.group(2)):02d}", "day")]
    else:
        r = _RANGE_RE.match(text)
        if r:
            a, b = _one_date(r.group("a")), _one_date(r.group("b"))
            if a and b:
                parts = [a, b]
    if parts is None:
        single = _one_date(text)
        if single is None:
            head, _, tail = text.partition(" ")
            single = _one_date(head)
            if single is None:
                return None
            if tail:
                notes.append(tail)
        parts = [single]

    start, precision = parts[0]
    end = None
    if len(parts) == 2:
        precision = max((parts[0][1], parts[1][1]), key=_COARSENESS.__getitem__)
        end = ValidTime(parts[1][0], None, parts[1][1], None).implied_end
    time_of_day = " ".join(notes)[:TIME_OF_DAY_MAX] or None
    return ValidTime(start, end, precision, time_of_day)


def parse_iso(value: str | None) -> str | None:
    """The start of `parse_valid(value)`, for readers that need only a date."""
    parsed = parse_valid(value)
    return parsed.start if parsed else None
```

`parse_iso` used to return a partial value unchanged (`"2026"` → `"2026"`); it now returns `"2026-01-01"`. Every caller (`rg -n "parse_iso\(" artmind`: `lift_document_dates`, `canonical_entity_dates`, `relative_anchor_date`, supersession notice parsing, `table2graph._to_date`) wants an ISO date, so the change is the fix.

- [ ] **Step 4: Run the parser tests.** `uv run --group dev pytest test/test_parse_valid.py test/test_temporal.py -v` — expected: PASS. Update any `test_temporal.py` case that asserted a partial date passes through unchanged.

- [ ] **Step 5: Write the failing mapping tests.** Append to `test/test_observation_valid_time.py`:

```python
from artmind import ingest as ing


def _schema(with_end=False):
    props = {"date_or_time": {"temporal": "valid_from"}}
    if with_end:
        props["ends"] = {"temporal": "valid_to"}
    return {"entity_types": {"EVENT": {"kind": "occurrent", "properties": props}}}


def _build(props, schema, document=None):
    entities = [{"id": "e1", "name": "Lake trip", "entity_class": "EVENT", "chunk_id": "c1", "_domain": "pj"}]
    document = document or {"id": "d1", "_valid_from": "2023-09-01", "_valid_time_source": "filename"}
    return ing._build_observations(entities, [{"id": "e1", "properties": props}], {}, schema, document)[0]


def test_a_range_sets_valid_to_precision_and_time_of_day():
    obs = _build({"date_or_time": "2023-09-07 to 2023-09-08 (multi-day)"}, _schema())
    assert (obs["_valid_from"], obs["_valid_to"], obs["_valid_precision"], obs["_valid_time_of_day"]) == (
        "2023-09-07", "2023-09-09", "day", "multi-day")
    assert obs["date_or_time"] == "2023-09-07 to 2023-09-08 (multi-day)"   # the raw value is kept


def test_a_tagged_valid_to_property_wins_over_the_range_end():
    obs = _build({"date_or_time": "2023-09-07 to 2023-09-08", "ends": "2023-09-20"}, _schema(with_end=True))
    assert obs["_valid_to"] == "2023-09-20"


def test_a_month_value_no_longer_falls_back_to_the_document_date():
    obs = _build({"date_or_time": "2026-06 (planned)"}, _schema())
    assert (obs["_valid_from"], obs["_valid_precision"], obs["_valid_time_source"]) == ("2026-06-01", "month", "property")


def test_a_default_document_date_has_precision_none():
    schema = {**_schema(), "temporal": {"defaults": {"time_source": "default_ingestion"}}}
    obs = _build({}, schema, {"id": "d1", "_valid_from": "2026-11-15", "_valid_time_source": "default_ingestion"})
    assert (obs["_valid_from"], obs["_valid_precision"]) == ("2026-11-15", "none")
```

and `test/test_retime_valid_time.py`:

```python
"""`ingest retime` re-reads raw temporal values with parse_valid (spec 2026-11-15 §8). Synthetic."""
from artmind.retime import retime_observations


def _obs(result):
    """A list today; (list, dropped) once SBP Task 6 lands."""
    return result[0] if isinstance(result, tuple) else result


SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}}},
          "temporal": {"defaults": {"time_source": "default_ingestion"}}}


def test_retime_fills_end_precision_and_time_of_day_from_the_stored_raw_value():
    staged = [{"id": "o1", "key": "lake trip|EVENT|pj", "entity_class": "EVENT", "_domain": "pj",
               "canonical_name": "Lake trip", "date_or_time": "2023-09-07 to 2023-09-08 (multi-day)",
               "_valid_from": "2023-09-07"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2023-09-01", "_valid_time_source": "filename"}, SCHEMA))
    assert (new[0]["_valid_to"], new[0]["_valid_time_of_day"]) == ("2023-09-09", "multi-day")


def test_retime_marks_a_default_document_date_as_none():
    staged = [{"id": "o1", "key": "k|EVENT|pj", "entity_class": "EVENT", "_domain": "pj"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2026-11-15", "_valid_time_source": "default_ingestion"}, SCHEMA))
    assert new[0]["_valid_precision"] == "none"
```

- [ ] **Step 6: Run and confirm failure.** `uv run --group dev pytest test/test_observation_valid_time.py test/test_retime_valid_time.py -v` — expected: FAIL (no `_valid_precision`).

- [ ] **Step 7: Implement the mapping.** In `artmind/ingest.py`:

```python
@dataclass(frozen=True)
class FactTime:
    """A fact's valid time from its class's `temporal:` properties (spec 2026-11-15 §3.2)."""
    valid_from: str | None = None
    valid_to: str | None = None
    precision: str | None = None
    time_of_day: str | None = None
    due: str | None = None
    review: str | None = None
    unparsed: int = 0


def _fact_valid_time(entity_dates: dict, entity_class: str, props: dict) -> FactTime:
    """Shared by `_build_observations`, `artmind.retime` and `table2graph` (staged properties
    stay flattened). A tagged `valid_to` wins over a range's end."""
    from artmind.temporal import parse_valid

    found: dict = {}
    unparsed = 0
    for canon_key, prop_name in (entity_dates.get(entity_class) or {}).items():
        raw = props.get(prop_name)
        if raw in (None, ""):
            continue
        parsed = parse_valid(raw)
        if parsed is None:
            unparsed += 1
        else:
            found[canon_key] = parsed
    start, end = found.get("valid_from"), found.get("valid_to")
    return FactTime(
        valid_from=start.start if start else None,
        valid_to=end.start if end else (start.end if start else None),
        precision=start.precision if start else None,
        time_of_day=start.time_of_day if start else None,
        due=found["due"].start if "due" in found else None,
        review=found["review"].start if "review" in found else None,
        unparsed=unparsed,
    )


def _document_precision(document: dict, schema: dict) -> str:
    """The precision of a fact dated by its document: `none` for a default date (spec §2)."""
    from artmind.temporal import date_is_only_the_default

    if not document.get("_valid_from") or date_is_only_the_default(schema, document):
        return "none"
    return document.get("_valid_precision") or "day"
```

`due` and `review` are read now and written in Task 10; until then no schema tags them. In `_build_observations`, replace the `fact_valid_from, fact_valid_to = …` lines and `build_observation`'s date arguments:

```python
        fact = _fact_valid_time(entity_dates, entity.get("entity_class") or "", domain_props)
        observation = build_observation(
            entity,
            canonical_name=canonical,
            domain_props=domain_props,
            doc_id=doc_id,
            doc_version=int(doc_version) if str(doc_version).isdigit() else 1,
            chunk_id=chunk_id,
            kind=class_kind(schema, entity.get("entity_class") or ""),
            doc_valid_from=doc_valid_from,
            valid_from=fact.valid_from,
            valid_to=fact.valid_to or doc_valid_to,
            valid_time_source=("property" if fact.valid_from else document.get("_valid_time_source")),
            valid_precision=fact.precision if fact.valid_from else _document_precision(document, schema),
            valid_time_of_day=fact.time_of_day,
        )
```

In `observations.build_observation`, add `valid_precision: str | None = None, valid_time_of_day: str | None = None` and, after `_valid_time_source`:

```python
    if valid_precision:
        props["_valid_precision"] = valid_precision
    if valid_time_of_day:
        props["_valid_time_of_day"] = valid_time_of_day
```

In `artmind/projection.py`, add to `_OBSERVATION_SYSTEM_KEYS`: `"_valid_precision", "_valid_time_of_day", "_due", "_review", "_revised_at"` (Task 22 adds the record-time keys). In `merge_observations`, after `_valid_time_source`:

```python
    for field in ("_valid_precision", "_valid_time_of_day"):
        if winner.get(field):
            props[field] = winner[field]
```

In `artmind/retime.py`, `_OBSERVATION_TIME_KEYS` gains `"_valid_precision", "_valid_time_of_day"`, and `retime_observations` builds `times` the same way:

```python
        fact = ingest._fact_valid_time(entity_dates, cls, obs)
        times = {
            "_valid_from": fact.valid_from or doc_from,
            "_valid_to": fact.valid_to or doc_to,
            "_valid_time_source": "property" if fact.valid_from else document.get("_valid_time_source"),
            "_valid_precision": fact.precision if fact.valid_from else ingest._document_precision(document, schema),
            "_valid_time_of_day": fact.time_of_day,
            "_doc_valid_from": doc_from,
        }
```

In `table2graph.build_staged` (lines ~1086–1093), replace the `parse_iso` loop with `fact = ingest._fact_valid_time(entity_dates, built.entity_class, {k: _display(v) for k, v in built.props.items() if v is not None})` and pass `valid_from=fact.valid_from, valid_to=fact.valid_to, valid_precision=fact.precision or "day", valid_time_of_day=fact.time_of_day`. In `update.py`, wherever it derives a fact's date (`rg -n "parse_iso|_valid_from|temporal" artmind/update.py`), call `_fact_valid_time` the same way.

- [ ] **Step 8: Run and confirm.** `uv run --group dev pytest test/test_parse_valid.py test/test_observation_valid_time.py test/test_retime_valid_time.py test/test_document_dates.py test/test_table2graph.py test/test_update.py test/test_projection_merge.py -v && just dev-test` — expected: PASS.

- [ ] **Step 9: Commit.**

```bash
git add artmind/temporal.py artmind/ingest.py artmind/observations.py artmind/projection.py artmind/retime.py artmind/table2graph.py artmind/update.py test/
git commit -m "feat(temporal): parse_valid keeps range ends, precision and time of day on every fact"
```

**Beyond the suite** (no LLM, no write): `cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain personal_journal --dryRun --compact | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['scanned'], r['changed'])"` — `changed` is close to the number of entries with a qualified or partial date (counts only). Then, with the user's yes, run it without `--dryRun`; this is rollout step 2.

---

### Task 7: Count dated facts, ranges and unparsed values per document

**Files:**
- Modify: `artmind/ingest.py` — `_build_observations(..., counts=)`; `_date_counts_line`; `extract_kg`'s per-document log line and job-file update.
- Modify: `artmind/jobs.py` — `update_job_file(..., date_counts=)` (line ~125); `job-status` file rows (line ~305).
- Modify: `artmind/db.py` — `ALTER TABLE ingestion_job_files ADD COLUMN date_counts TEXT` beside the migrations at lines ~249–260.
- Test: `test/test_date_counts.py` (new).

- [ ] **Step 1: Write the failing test.**

```python
"""Per-document date counts (spec 2026-11-15 §3.4): numbers only, never values."""
from artmind import ingest as ing

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}}}}


def test_counts_dated_ranges_and_unparsed():
    entities = [{"id": f"e{i}", "name": f"E{i}", "entity_class": "EVENT", "chunk_id": "c1", "_domain": "pj"} for i in range(3)]
    props = [{"id": "e0", "properties": {"date_or_time": "2026-01-07"}},
             {"id": "e1", "properties": {"date_or_time": "2026-01-07 to 2026-01-09"}},
             {"id": "e2", "properties": {"date_or_time": "soonish"}}]
    counts: dict = {}
    ing._build_observations(entities, props, {}, SCHEMA, {"id": "d1", "_valid_from": "2026-01-07"}, counts=counts)
    assert counts == {"dated": 2, "ranges": 1, "unparsed": 1}


def test_the_log_line_carries_no_value():
    assert ing._date_counts_line({"dated": 2, "ranges": 1, "unparsed": 1}) == \
        "dates: 2 dated by property, 1 range, 1 unparsed"
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_date_counts.py -v` — expected: TypeError on `counts=`.

- [ ] **Step 3: Implement.** `_build_observations(..., counts: dict | None = None)`: at the top, `if counts is not None: counts.update({"dated": 0, "ranges": 0, "unparsed": 0})`; after `fact = …`:

```python
        if counts is not None:
            counts["dated"] += 1 if fact.valid_from else 0
            counts["ranges"] += 1 if fact.valid_from and fact.valid_to else 0
            counts["unparsed"] += fact.unparsed
```

and:

```python
def _date_counts_line(counts: dict) -> str:
    n = counts.get("ranges", 0)
    return (f"dates: {counts.get('dated', 0)} dated by property, {n} range{'s' if n != 1 else ''}, "
            f"{counts.get('unparsed', 0)} unparsed")
```

In `extract_kg`, pass `counts=date_counts` to `_build_observations`, `logger.info` the line beside the existing per-document summary, and pass `date_counts=date_counts` to the job-file update. In `jobs.update_job_file`, store `json.dumps(date_counts)`; in the `job-status` file rows add `"date_counts"`, parsed with the module's existing JSON helper.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_date_counts.py $(rg -l "update_job_file|job_status" test) -v`, then `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py` and `just obsidian-plugin-test` (the `ingest-job-status.*.json` fixtures gain `date_counts`). Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/ingest.py artmind/jobs.py artmind/db.py test/test_date_counts.py obsidian/artmind-obsidian/test/fixtures
git commit -m "feat(ingest): count dated facts, ranges and unparsed dates per document"
```

---

### Task 8: The DATES section states the accepted forms

**Files:**
- Modify: `artmind/prompt_builder.py` — `_render_anchor_date_section` (lines ~187–224).
- Test: the file that tests it (`rg -l "_render_anchor_date_section" test`), append.

- [ ] **Step 1: Write the failing tests.**

```python
from artmind.prompt_builder import _render_anchor_date_section

TYPES = {"EVENT": {"properties": {"date_or_time": {"temporal": "valid_from"}}}}


def test_dates_section_lists_ranges_months_and_quarters():
    text = _render_anchor_date_section(TYPES, "2026-01-07")
    for form in ("YYYY-MM-DD to YYYY-MM-DD", "YYYY-MM", "YYYY-Qn"):
        assert form in text
    assert "whenever the text gives one" in text and "2026-01-07" in text


def test_dates_section_without_an_anchor_still_lists_the_forms():
    text = _render_anchor_date_section(TYPES, None)
    assert "YYYY-Qn" in text and "document dated" not in text
```

- [ ] **Step 2: Run and confirm failure.** Expected: FAIL (no forms; a `None` anchor returns `""`).

- [ ] **Step 3: Implement.** Replace the function's early `if not anchor_date: return ""` and its return:

```python
    names = [...]   # unchanged
    if not names:
        return ""
    anchor = (
        f"This passage is from a document dated {anchor_date}. Relative time\n"
        'expressions in it ("today", "this evening", "yesterday", "last Tuesday")\n'
        "are relative to that date.\n"
    ) if anchor_date else ""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "DATES:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{anchor}"
        f"Only these properties take a date: {', '.join(names)}.\n"
        "Do not add a date property to any other entity or class -- a person,\n"
        "place or object is not dated by appearing in this passage.\n"
        "Start each value with one of these, then the original wording if it adds\n"
        'anything, e.g. "2026-01-07 (evening)":\n'
        "  - a date: YYYY-MM-DD\n"
        "  - a range: YYYY-MM-DD to YYYY-MM-DD -- use it whenever the text gives one\n"
        "  - a month: YYYY-MM\n"
        "  - a quarter: YYYY-Qn\n"
        "Use a date the passage states or implies; never invent one. Omit the\n"
        "property when the passage gives no time reference at all."
    )
```

Check the caller (`rg -n "_render_anchor_date_section" artmind`) passes `None` through rather than skipping the call (fiction has `temporal:` properties but no `relative_anchor`). If SBP Task 3 landed, keep its class scoping.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest $(rg -l "_render_anchor_date_section|anchor_date" test) -v` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/prompt_builder.py test/
git commit -m "feat(prompts): the DATES section asks for ranges, months and quarters"
```

---

### Task 9: `ingest date-audit` (read-only, no LLM)

**Files:**
- Create: `artmind/date_audit.py`.
- Modify: `artmind/cli.py` — `ingest date-audit` after `ingest empty-extractions` (line ~1408); `"date-audit"` in the "Graph building" list of `COMMAND_GROUPS` (line ~392).
- Test: `test/test_date_audit.py` (new).

- [ ] **Step 1: Write the failing tests.**

```python
"""`ingest date-audit` (spec 2026-11-15 §3.4): counts per class from staging; no values."""
import json

from click.testing import CliRunner

from artmind import date_audit
from artmind.cli import cli

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}},
                           "PERSON": {"kind": "recurrent", "properties": {}}}}


def _stage(root):
    folder = root / "d1"
    folder.mkdir(parents=True)
    obs = [
        {"entity_class": "EVENT", "date_or_time": "2026-01-07", "_valid_time_source": "property", "_valid_precision": "day"},
        {"entity_class": "EVENT", "date_or_time": "2026-01 to 2026-03", "_valid_time_source": "property",
         "_valid_precision": "month", "_valid_to": "2026-04-01"},
        {"entity_class": "EVENT", "date_or_time": "someday", "_valid_time_source": "filename", "_valid_precision": "day"},
        {"entity_class": "PERSON", "_valid_time_source": "filename"},
    ]
    (folder / "observations.json").write_text(json.dumps(obs))
    return folder


def test_rows_per_dated_class(tmp_path):
    rows = date_audit.audit_folders([_stage(tmp_path)], SCHEMA)
    assert rows == [{"class": "EVENT", "observations": 3, "dated_by_property": 2, "share": 0.667,
                     "precision": {"day": 1, "month": 1}, "ranges": 1, "unparsed": 1}]


def test_no_value_reaches_the_payload(tmp_path):
    dumped = json.dumps(date_audit.audit_folders([_stage(tmp_path)], SCHEMA))
    assert "someday" not in dumped and "2026" not in dumped


def test_cli_envelope(tmp_path, monkeypatch):
    folder = _stage(tmp_path)
    monkeypatch.setattr(date_audit, "staging_folders", lambda domain: [folder])
    monkeypatch.setattr(date_audit, "load_schema", lambda domain: SCHEMA)
    result = CliRunner().invoke(cli, ["ingest", "date-audit", "--domain", "pj", "--compact"])
    payload = json.loads(result.stdout)
    assert payload["command"] == "ingest date-audit" and payload["rows_total"] == 1
    assert payload["next"] == "artmind ingest retime --domain pj --dryRun"
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_date_audit.py -v` — expected: ImportError.

- [ ] **Step 3: Implement** `artmind/date_audit.py`:

```python
"""`ingest date-audit`: how a domain's staged facts are dated, per class (spec 2026-11-15 §3.4).

Read-only: staging only, no registry, Neo4j or LLM. Counts and shares, never values.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from artmind.retime import staging_folders
from artmind.temporal import _entity_temporal_mapping, load_schema, parse_valid


def audit_folders(folders: list[Path], schema: dict) -> list[dict]:
    mapping = _entity_temporal_mapping(schema)
    stats: dict[str, dict] = {}
    for folder in folders:
        path = folder / "observations.json"
        if not path.exists():
            continue
        for obs in json.loads(path.read_text(encoding="utf-8")) or []:
            cls = obs.get("entity_class") or ""
            props = mapping.get(cls)
            if not props:
                continue
            s = stats.setdefault(cls, {"observations": 0, "dated": 0, "precision": Counter(), "ranges": 0, "unparsed": 0})
            s["observations"] += 1
            if obs.get("_valid_time_source") == "property":
                s["dated"] += 1
                s["precision"][obs.get("_valid_precision") or "day"] += 1
                s["ranges"] += 1 if obs.get("_valid_to") else 0
            s["unparsed"] += sum(1 for p in props.values()
                                 if obs.get(p) not in (None, "") and parse_valid(obs.get(p)) is None)
    return [
        {"class": cls, "observations": s["observations"], "dated_by_property": s["dated"],
         "share": round(s["dated"] / s["observations"], 3) if s["observations"] else 0.0,
         "precision": dict(sorted(s["precision"].items())), "ranges": s["ranges"], "unparsed": s["unparsed"]}
        for cls, s in sorted(stats.items())
    ]


def audit_domain(domain: str) -> dict:
    rows = audit_folders(staging_folders(domain), load_schema(domain))
    return {"domain": domain, "rows": rows, "rows_total": len(rows), "truncated": False,
            "next": f"artmind ingest retime --domain {domain} --dryRun" if any(r["unparsed"] for r in rows) else None}
```

In `artmind/cli.py`:

```python
@ingest.command("date-audit")
@click.option("--domain", required=True, help="Domain whose staged facts to audit")
@compact_option
def ingest_date_audit(domain: str, compact: bool) -> None:
    """Per class: share of facts dated by their own property, precision mix, ranges, unparsed dates.

    Read-only: staging only, no LLM, no Neo4j. Counts only; no value is printed.
    """
    from artmind import date_audit

    _echo_json(date_audit.audit_domain(domain), compact)
```

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_date_audit.py test/test_cli_conventions.py test/test_cli_guide.py -v` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/date_audit.py artmind/cli.py test/test_date_audit.py
git commit -m "feat(ingest): date-audit reports how each class is dated"
```

**Beyond the suite** (read-only): `cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest date-audit --domain personal_journal --compact` — EVENT `share` ≈ 0.98, ACTIVITY ≈ 0.95 (spec §1.6); after the Task 6 retime, `ranges` ≥ 13 and every `unparsed` value is counted (acceptance §10 "Parsing").

---
## Part 3 — Schema rules and `domains validate` (spec §4, rollout step 3)

### Task 10: `temporal: due|review`, document `revised` and `valid_to` labels, shipped fixes

**Files:**
- Modify: `artmind/ingest.py` — `_build_observations` writes `_due` / `_review` from `FactTime` (Task 6 already reads them).
- Modify: `artmind/retime.py` — `_OBSERVATION_TIME_KEYS` gains `_due`, `_review`; `_DOCUMENT_TIME_KEYS` gains `_revised_at`; `retime_observations` writes them.
- Modify: `artmind/temporal.py` — `lift_document_dates` handles a `revised` mapping key (stored `revised`), never letting it set `valid_from` when an effective label answered; `valid_to` labels already work through the same loop.
- Modify: `artmind/ingest.py` — `_document_valid_time` maps `lifted["revised"]` → `_revised_at`, `lifted["valid_to"]` → `_valid_to`.
- Create: `artmind/schema_validate.py` `temporal_warnings(schema, name)`; ERROR in `validate_schema` for a `temporal:` value outside `valid_from|valid_to|due|review`.
- Modify: `artmind/cli.py` `domains validate` (line ~738) prints `temporal_warnings` with the other WARNs; `schema_validate.warn_all` includes them.
- Modify shipped schemas (and the skills-assets journal copy where the class exists):
  - `banking.risk_governance` `ACTION_ITEM.deadline`: `temporal: due`;
  - `banking.cases` `REMEDIATION_ACTION.due_date`: `temporal: due`;
  - `project_governance` `ACTION.due_date`: `temporal: due`; `STATE_CHANGE.date_or_time`: `temporal: valid_from`, `.due_date`: `temporal: due` (question 7); `DECISION.date`: `temporal: valid_from`;
  - banking `TRANSACTION`: new `transaction_date: {hint: "date of the transaction, YYYY-MM-DD", temporal: valid_from}`; `SAR`: new `filed_on: {hint: "date the SAR was filed", temporal: valid_from}` — in all nine banking schemas that declare them;
  - `general`: a `temporal:` block (`defaults: {valid_from: ingestion_date, time_source: default_ingestion}`), `EVENT.date_or_period: {temporal: valid_from}`, `CLAIM`: new `stated_on: {temporal: valid_from}`;
  - `technical_paper` `FINDING`: new `reported_on: {temporal: valid_from}`;
  - banking schemas with `Last Updated` under `temporal.document.valid_from` (communications, organization, sop_guides, risk_governance, products): move `Last Updated` to `temporal.document.revised`.
- Test: `test/test_temporal_flags.py` (new), `test/test_shipped_schema_temporal.py` (new).

- [ ] **Step 1: Write the failing tests.** `test/test_temporal_flags.py`:

```python
"""`temporal: due|review` and document `revised` (spec 2026-11-15 §4). Synthetic."""
from artmind import ingest as ing
from artmind import schema_validate as sv
from artmind.temporal import lift_document_dates

SCHEMA = {"entity_types": {"ACTION": {"kind": "recurrent", "properties": {
    "due_date": {"temporal": "due"}, "next_check": {"temporal": "review"}}}}}


def test_due_and_review_never_set_valid_from():
    entities = [{"id": "e1", "name": "Fix the gate", "entity_class": "ACTION", "chunk_id": "c1", "_domain": "pg"}]
    props = [{"id": "e1", "properties": {"due_date": "2026-Q2", "next_check": "2026-05-01"}}]
    obs = ing._build_observations(entities, props, {}, SCHEMA, {"id": "d", "_valid_from": "2026-01-10",
                                                                  "_valid_time_source": "header"})[0]
    assert (obs["_valid_from"], obs["_due"], obs["_review"]) == ("2026-01-10", "2026-04-01", "2026-05-01")
    assert obs["_valid_time_source"] == "header"


def test_revised_label_does_not_become_the_effective_date():
    md = "| Effective Date | 2026-01-01 |\n| Last Updated | 2026-03-04 |\n"
    out = lift_document_dates(md, {}, {"valid_from": ["Effective Date"], "revised": ["Last Updated"]})
    assert (out["valid_from"], out["revised"]) == ("2026-01-01", "2026-03-04")


def test_revised_alone_leaves_valid_from_to_the_default():
    out = lift_document_dates("| Last Updated | 2026-03-04 |\n", {}, {"valid_from": ["Effective Date"], "revised": ["Last Updated"]},
                              {"valid_from": "ingestion_date"})
    assert out["revised"] == "2026-03-04" and out["time_source"] == "header"
    assert "valid_from" not in out


def test_unknown_temporal_value_is_an_error():
    bad = {"entity_types": {"X": {"kind": "recurrent", "properties": {"d": {"temporal": "deadline"}}}}}
    assert any("temporal" in e for e in sv.validate_schema(bad, {"kinds": {"recurrent": "", "occurrent": ""}}))


def test_occasion_without_a_date_property_warns():
    schema = {"entity_types": {"SAR": {"kind": "occurrent", "properties": {"filing_obligation": {}}}}}
    assert any("SAR" in w and "valid_from" in w for w in sv.temporal_warnings(schema, "b"))
```

Leave `revised` with no `valid_from` out of the document's `_valid_from`: a "Last Updated" date is not when the document took effect. The default date applies, which `_valid_precision: none` marks. If you prefer `revised` as the fallback date, that is a one-line change in `lift_document_dates`; say so in review.

`test/test_shipped_schema_temporal.py`:

```python
"""Shipped schemas follow the temporal rules (spec 2026-11-15 §4)."""
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

SCHEMAS = Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"
DEADLINES = [("banking.risk_governance", "ACTION_ITEM", "deadline"), ("banking.cases", "REMEDIATION_ACTION", "due_date"),
             ("project_governance", "ACTION", "due_date"), ("project_governance", "STATE_CHANGE", "due_date")]


def _schema(name):
    return yaml.safe_load((SCHEMAS / f"{name}_schema.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name, cls, prop", DEADLINES)
def test_deadlines_are_due_not_valid_from(name, cls, prop):
    assert _schema(name)["entity_types"][cls]["properties"][prop]["temporal"] == "due"


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_no_temporal_warning_in_shipped_schemas(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert sv.temporal_warnings(schema, path.stem) == []


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("banking*_schema.yaml")), ids=lambda p: p.stem)
def test_last_updated_is_revised_not_effective(path):
    doc = (yaml.safe_load(path.read_text(encoding="utf-8")).get("temporal") or {}).get("document") or {}
    assert "Last Updated" not in (doc.get("valid_from") or [])
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_temporal_flags.py test/test_shipped_schema_temporal.py -v` — expected: FAIL.

- [ ] **Step 3: Implement the code.** In `_build_observations`, after the `build_observation` call:

```python
        if fact.due:
            observation["_due"] = fact.due
        if fact.review:
            observation["_review"] = fact.review
```

(same three lines in `retime_observations`, inside `times`). In `lift_document_dates`, the loop treats `revised` like `valid_from` for parsing; after the loop, nothing copies `revised` into `valid_from`. In `_document_valid_time`, after `valid_to`:

```python
    if lifted.get("revised"):
        out["_revised_at"] = lifted["revised"]
```

In `schema_validate`:

```python
TEMPORAL_VALUES = ("valid_from", "valid_to", "due", "review")


def temporal_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """WARNs for the temporal rules (spec 2026-11-15 §4, §5.3)."""
    out = []
    for cls, decl in (schema.get("entity_types") or {}).items():
        props = (decl or {}).get("properties") or {}
        tags = {(p or {}).get("temporal") for p in props.values() if isinstance(p, dict)}
        if class_identity(schema, cls) == "occasion" and "valid_from" not in tags:
            out.append(f"{schema_name}.entity_types.{cls}: an occasion class declares no "
                       "`temporal: valid_from` property; its facts are dated by their document only")
    return out
```

`class_identity` arrives in Task 14. Until then, define it here as the default rule (`occasion` for `kind: occurrent`, else `name`) and let Task 14 extend it. In `validate_schema`'s property loop:

```python
        for prop, prop_decl in (decl.get("properties") or {}).items():
            _check_reserved(prop, prefix, f"{where}.properties", errors)
            tag = prop_decl.get("temporal") if isinstance(prop_decl, dict) else None
            if tag is not None and tag not in TEMPORAL_VALUES:
                errors.append(f"{where}.properties.{prop}: temporal={tag!r} is not one of {list(TEMPORAL_VALUES)}")
```

`temporal.document` mapping keys are checked the same way (`valid_from`, `valid_to`, `revised`, `version`).

- [ ] **Step 4: Edit the shipped schemas** as listed under **Files**. For `general`, add the `temporal:` block after `persona:`; keep its other keys. `contracts` and `fiction` are unchanged.

- [ ] **Step 5: Run and confirm.** `uv run --group dev pytest test/test_temporal_flags.py test/test_shipped_schema_temporal.py test/test_schema_validate.py test/test_document_dates.py -v && just dev-test` — expected: PASS. `domains validate` must still exit 0 on every shipped schema: `uv run artmind domains validate` from `~` uses `~/.artmind`, so run the test file instead.

- [ ] **Step 6: Commit.**

```bash
git add artmind/ingest.py artmind/retime.py artmind/temporal.py artmind/schema_validate.py artmind/cli.py artmind/domains/schemas artmind/skills/artmind-create-schema/assets test/test_temporal_flags.py test/test_shipped_schema_temporal.py
git commit -m "feat(schemas): deadlines are temporal: due; Last Updated is revised; happenings declare a date"
```

**Beyond the suite** (no LLM): in banking_corpus, after `artmind init --force --schema banking.risk_governance` (inside the vault, with the user's yes), `ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain banking.risk_governance --dryRun --compact` lists the documents whose ACTION_ITEM facts move off their deadline. TRANSACTION/SAR dates stay empty until the per-vault `backfill-properties` step (SBP Task 10).

---

### Task 11: The property audit reports temporal fill (Blocked by SBP Task 8)

**Files:**
- Modify: `artmind/property_audit.py` (created by SBP Task 8) — each row with a `temporal` flag gains `date_audit` columns from `date_audit.audit_folders`.
- Test: `test/test_property_audit.py` (append).

- [ ] **Step 1: Write the failing test.**

```python
def test_temporal_key_rows_carry_date_audit_columns(tmp_path):
    kg = _stage(tmp_path, [_obs(1, date_or_time="2026-01-07", _valid_time_source="property", _valid_precision="day"),
                           _obs(2, date_or_time="soon")], "Some text.")
    row = next(r for r in pa.audit(kg / "d", SCHEMA)["rows"] if r["key"] == "date_or_time")
    assert row["dates"] == {"dated_by_property": 1, "precision": {"day": 1}, "ranges": 0, "unparsed": 1}
```

(`_stage`, `_obs`, `SCHEMA` and `pa.audit`'s signature are SBP Task 8's; adjust the call to what it shipped.)

- [ ] **Step 2: Run, implement, run.** In the row builder, for a `temporal` key: `row["dates"] = {k: v for k, v in date_row.items() if k in ("dated_by_property", "precision", "ranges", "unparsed")}` where `date_row` is the class's row from `date_audit.audit_folders([...], schema)`. Run `uv run --group dev pytest test/test_property_audit.py -v` — expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add artmind/property_audit.py test/test_property_audit.py
git commit -m "feat(audit): temporal keys show their date fill"
```

---

## Part 4 — Dated chunks, passages and summaries; one valid-time predicate (spec §5.1, §6.2, §7.1; rollout step 4)

### Task 12: Chunks, passages and summaries carry dates

**Files:**
- Modify: `artmind/ingest.py` — new `_chunk_valid_time(chunk, document)`; `extract_kg` and `retime` stamp each staged chunk with `_doc_valid_from`, `_doc_valid_to` and `_valid_from`.
- Modify: `artmind/passages.py` — `passage_rows` copies `_valid_from`, `_valid_to`, `_doc_valid_from`, `_doc_valid_to`, `_valid_precision` (lines ~134–136).
- Modify: `artmind/embed_sweep.py` — `_CHUNKS_NEEDING_PASSAGES` returns the prefixed fields (lines ~38–40) and the row dict copies them (lines ~148–149).
- Modify: `artmind/summary_store.py` — `summary_rows` adds `_doc_valid_from`/`_doc_valid_to`, and each chunk summary takes its chunk's `_valid_from`.
- Modify: `artmind/ingest.py` `_commit_document_tx` — passes the staged chunks to `summary_rows`.
- Modify: `artmind/retime.py` — `retime_folder` rewrites `chunks.json` dates too (a `chunks.json` change marks the folder changed).
- Test: `test/test_chunk_dates.py` (new), `test/test_passages.py`, `test/test_summary_store.py` (append).

- [ ] **Step 1: Write the failing tests.** `test/test_chunk_dates.py`:

```python
"""Chunk dates (spec 2026-11-15 §5.1, question 15). Invented text."""
from artmind import ingest as ing

DOC = {"_valid_from": "2026-01-01", "_valid_to": "2026-02-01", "_valid_time_source": "header"}


def test_a_chunk_under_a_dated_heading_has_its_own_date():
    chunk = {"id": "c", "header_path": "Notes > 2026-01-14", "text": "Planted the bulbs."}
    assert ing._chunk_valid_time(chunk, DOC) == {
        "_valid_from": "2026-01-14", "_doc_valid_from": "2026-01-01", "_doc_valid_to": "2026-02-01"}


def test_a_chunk_starting_with_a_date_line_has_its_own_date():
    chunk = {"id": "c", "header_path": "", "text": "\n2026-01-20\nRain all day."}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-20"


def test_otherwise_the_documents_date():
    chunk = {"id": "c", "header_path": "Notes", "text": "On 2026-01-20 it rained."}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-01"


def test_a_month_heading_is_not_a_chunk_date():
    chunk = {"id": "c", "header_path": "January 2026", "text": "x"}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-01"
```

Append to `test/test_passages.py`:

```python
def test_passages_copy_the_chunks_prefixed_dates():
    from artmind.passages import passage_rows

    chunk = {"id": "c1", "doc_id": "d1", "_domain": "pj", "text": "A short passage of text.",
             "_valid_from": "2026-01-14", "_doc_valid_from": "2026-01-01", "_doc_valid_to": None}
    props = passage_rows(chunk, "T")[0]["props"]
    assert (props["_valid_from"], props["_doc_valid_from"]) == ("2026-01-14", "2026-01-01")
    assert "valid_from" not in props and "_doc_valid_to" not in props
```

Append to `test/test_summary_store.py`:

```python
def test_chunk_summaries_take_their_chunks_date():
    from artmind.summary_store import summary_rows

    data = {"document": {"summary": "S"}, "chunks": [{"chunk_id": "c1", "summary": "C"}]}
    doc = {"id": "d1", "_domain": "pj", "_valid_from": "2026-01-01"}
    rows = summary_rows(data, doc, chunks=[{"id": "c1", "_valid_from": "2026-01-14"}])
    by_level = {r["props"]["level"]: r["props"] for r in rows}
    assert by_level["chunk"]["_valid_from"] == "2026-01-14"
    assert by_level["document"]["_valid_from"] == "2026-01-01"
    assert by_level["chunk"]["_doc_valid_from"] == "2026-01-01"
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_chunk_dates.py test/test_passages.py test/test_summary_store.py -v` — expected: FAIL.

- [ ] **Step 3: Implement.** In `artmind/ingest.py`:

```python
def _chunk_valid_time(chunk: dict, document: dict) -> dict:
    """A chunk's dates: its document's window, plus its own day when its last heading or its
    first non-blank line is one day-precision date (spec 2026-11-15 §5.1, question 15)."""
    from artmind.temporal import parse_valid

    out = {"_doc_valid_from": document.get("_valid_from"), "_doc_valid_to": document.get("_valid_to")}
    own = None
    heading = (chunk.get("header_path") or "").rsplit(">", 1)[-1].strip()
    first_line = next((line.strip() for line in (chunk.get("text") or "").splitlines() if line.strip()), "")
    for candidate in (heading, first_line):
        parsed = parse_valid(candidate) if candidate else None
        if parsed and parsed.precision == "day" and parsed.end is None and not parsed.time_of_day:
            own = parsed.start
            break
    out["_valid_from"] = own or document.get("_valid_from")
    return {k: v for k, v in out.items() if v}
```

`_valid_to` on a chunk stays unset (the document's end is `_doc_valid_to`) until Task 26 closes it.

In `extract_kg`, where the staged `chunks.json` is written, update each chunk with `_chunk_valid_time(chunk, document)`; in `retime_folder`, load `chunks.json`, apply the same, and include it in `write_staging` and in `changed`.

`passages.passage_rows`:

```python
        for field in ("_valid_from", "_valid_to", "_doc_valid_from", "_doc_valid_to"):
            if chunk.get(field) is not None:
                props[field] = chunk[field]
```

`embed_sweep._CHUNKS_NEEDING_PASSAGES` returns `c._valid_from AS _valid_from, c._valid_to AS _valid_to, c._doc_valid_from AS _doc_valid_from, c._doc_valid_to AS _doc_valid_to`, and the row dict copies those four keys.

`summary_store.summary_rows(data, document, chunks=None)`:

```python
    common = {"doc_id": document["id"], "_domain": document.get("_domain")}
    for src, dst in (("_valid_from", "_valid_from"), ("_valid_to", "_valid_to"),
                     ("_valid_from", "_doc_valid_from"), ("_valid_to", "_doc_valid_to")):
        if document.get(src) is not None:
            common[dst] = document[src]
    chunk_from = {c["id"]: c.get("_valid_from") for c in chunks or [] if c.get("id")}
    ...
    # in the chunk loop:
        props = {"id": sid, "level": "chunk", "chunk_id": entry["chunk_id"], **common,
                 "text": entry["summary"], "key": entry.get("key")}
        if chunk_from.get(entry["chunk_id"]):
            props["_valid_from"] = chunk_from[entry["chunk_id"]]
```

In `_commit_document_tx` step 3c, pass `chunks=staged["chunks"]`.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_chunk_dates.py test/test_passages.py test/test_summary_store.py test/test_embed_sweep.py test/test_document_dates.py test/test_dry_run_ingest_retime.py -v && just dev-test` — expected: PASS. `test_dry_run_ingest_retime.py`'s "nothing to retime" test still passes because the second run finds chunk dates unchanged.

- [ ] **Step 5: Commit.**

```bash
git add artmind/ingest.py artmind/passages.py artmind/embed_sweep.py artmind/summary_store.py artmind/retime.py test/
git commit -m "feat(retrieval): chunks, passages and summaries carry their dates"
```

---

### Task 13: One valid-time predicate, prefixed names only

**Files:**
- Modify: `artmind/graph_query.py` — `valid_predicate(var, param="asOf", *, include_undated=True)` replaces `asof_predicate` (lines ~137–148); callers at ~559, ~1390; `supersedes_list` (line ~153); timeline's `coalesce` reads (lines ~2250–2257); every other `coalesce(x._valid_from, x.valid_from)` / `coalesce(x.valid_to, x._valid_to)` (11 in this file).
- Modify: `artmind/vector_query.py` lines ~366–390 (`_passage_vector_cypher`, `_passage_text_cypher`) and its one `coalesce`.
- Modify: `artmind/survey.py` (5 reads, lines ~61–100), `artmind/topics/grid.py` (`_MATCHING`), `artmind/topics/members.py`, `artmind/conflicts.py` (3), `artmind/webui/curate_routes.py` (1), `artmind/temporal.py` (1): `d._valid_from` only; survey's `--from/--to` and `--by` skip documents whose `_valid_precision` is `none`.
- Modify: `artmind/supersession_records.py` `_RECOMPUTE` (lines ~74–84): `SET older._valid_to = …` and `REMOVE older.valid_to`.
- Create: `artmind/migrations/document_valid_time_names.py`; call it from `setup.setup_all` beside the other migrations (lines ~1116–1123), and add its result to the returned summary.
- Test: `test/test_valid_predicate.py` (new), `test/test_migration_document_valid_time_names.py` (new), `test/test_supersession_records.py` (append), plus updates to tests that assert the old predicate text.

- [ ] **Step 1: Write the failing tests.** `test/test_valid_predicate.py`:

```python
"""`graph_query.valid_predicate` (spec 2026-11-15 §6.2). Pure string checks plus a Cypher-param check."""
from _neo4j_recorder import Recorder

from artmind import graph_query as gq


def test_predicate_reads_prefixed_names_only():
    text = gq.valid_predicate("p")
    assert "p._valid_from" in text and "p._valid_to" in text
    assert "p.valid_from" not in text and "p.valid_to" not in text and "coalesce" not in text


def test_predicate_treats_precision_none_as_undated():
    assert "p._valid_precision = 'none'" in gq.valid_predicate("p")


def test_excluding_undated_negates_the_undated_branch():
    assert "NOT (p._valid_from IS NULL OR p._valid_precision = 'none')" in gq.valid_predicate("p", include_undated=False)


def test_vector_text_sends_the_widened_as_of(monkeypatch):
    rec = Recorder(lambda c, p: [])
    monkeypatch.setattr(gq, "neo4j_session", lambda: rec)
    from artmind import vector_query as vq

    monkeypatch.setattr(vq, "neo4j_session", lambda: rec, raising=False)
    monkeypatch.setattr(vq, "embed_query", lambda *a, **k: [0.0] * 4, raising=False)
    try:
        vq.vector_text_search(["pj"], "lake", as_of="2026")
    except Exception:
        pass
    sent = [p for c, p in rec.calls if "DocPassage" in c]
    assert sent and all(p.get("asOf") == "2026-12-31" for p in sent)
```

(Adjust the last test's entry point to `vector_query`'s public search function — `rg -n "^def " artmind/vector_query.py` — and the embedding stub to the function it calls; the assertion stays on the `asOf` param sent.)

`test/test_migration_document_valid_time_names.py`:

```python
from _neo4j_recorder import Recorder

from artmind.migrations import document_valid_time_names as m


def test_folds_unprefixed_names_on_documents_and_reports_counts():
    rec = Recorder(lambda c, p: [{"n": 3}])
    out = m.run(rec)
    fold = rec.queries_containing("SET d._valid_to")
    assert fold and "coalesce(d._valid_to, d.valid_to)" in fold[0][0] and "REMOVE d.valid_to" in fold[0][0]
    assert all(label in fold[0][0] for label in ("Document", "DocumentHistory"))
    assert out == {"documents": 3}


def test_second_run_is_a_no_op():
    rec = Recorder(lambda c, p: [{"n": 0}])
    assert m.run(rec) == {"documents": 0}
```

Append to `test/test_supersession_records.py`:

```python
def test_recompute_writes_the_prefixed_valid_to():
    from artmind import supersession_records as sr

    assert "older._valid_to" in sr._RECOMPUTE and "older.valid_to =" not in sr._RECOMPUTE
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_valid_predicate.py test/test_migration_document_valid_time_names.py test/test_supersession_records.py -v` — expected: FAIL.

- [ ] **Step 3: Implement the predicate.**

```python
def valid_predicate(var: str, param: str = "asOf", *, include_undated: bool = True) -> str:
    """Valid-time filter over `_valid_from` / `_valid_to` only (spec 2026-11-15 §6.2).

    `_valid_to` is exclusive. A node with no `_valid_from`, or with `_valid_precision = 'none'`
    (a default date, not a real one), is undated: shown when `include_undated`, else hidden.
    `$asOf` is already widened by `resolve_as_of` (a year means its last day).
    """
    undated = f"({var}._valid_from IS NULL OR {var}._valid_precision = 'none')"
    dated = (f"({var}._valid_from <= ${param} "
             f"AND ({var}._valid_to IS NULL OR {var}._valid_to > ${param}))")
    body = f"({undated} OR {dated})" if include_undated else f"(NOT {undated} AND {dated})"
    return f"(${param} IS NULL OR {body})"
```

Delete `asof_predicate`; replace its callers (`rg -n "asof_predicate" artmind`). Replace every `coalesce(x._valid_from, x.valid_from)` with `x._valid_from` and every `coalesce(x.valid_to, x._valid_to)` with `x._valid_to` in the files listed (`rg -n "coalesce\(\w+\._valid_from, \w+\.valid_from\)|coalesce\(\w+\.valid_to, \w+\._valid_to\)" artmind`). In `survey.py`'s `--from/--to` clause and `--by` grouping, add `AND coalesce(d._valid_precision, 'day') <> 'none'`. The help text of `--asOf` on `vector-text`, `chunks` and `docs list` drops "valid_to is rarely set, so this is a floor": it is now a point.

- [ ] **Step 4: Implement the migration.** `artmind/migrations/document_valid_time_names.py`:

```python
"""Temporary (spec 2026-11-15 §7.1): fold unprefixed `valid_from`/`valid_to` on documents into
`_valid_from`/`_valid_to`. Run by `setup`; idempotent. Delete once every graph reports 0."""

_FOLD = """
MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND (d.valid_to IS NOT NULL OR d.valid_from IS NOT NULL)
SET d._valid_to = coalesce(d._valid_to, d.valid_to), d._valid_from = coalesce(d._valid_from, d.valid_from)
REMOVE d.valid_to, d.valid_from
RETURN count(d) AS n
"""


def run(session) -> dict:
    rec = session.run(_FOLD).single()
    return {"documents": int(rec["n"]) if rec else 0}
```

and in `setup.setup_all`, after the other two:

```python
        # Temporary (spec 2026-11-15 §7.1): delete with artmind/migrations/document_valid_time_names.py.
        from artmind.migrations import document_valid_time_names

        valid_time_names = document_valid_time_names.run(session)
```

returning `"document_valid_time_names": valid_time_names`. In `_RECOMPUTE`, `SET older.superseded_by = …, older._valid_to = head([...])` and append `REMOVE older.valid_to`. `catalogue.state.bump` is not needed for the migration: it changes no catalogue part's inputs (`time` reads `_valid_from`, which the fold only fills where it was empty); confirm with `test_catalogue_bump_sites.py`, and add a bump there if a part reads `_valid_to`.

- [ ] **Step 5: Run and confirm.** `uv run --group dev pytest test/test_valid_predicate.py test/test_migration_document_valid_time_names.py test/test_supersession_records.py test/test_vector_query.py test/test_query_layer.py $(rg -l "survey" test | head -20) test/test_catalogue_bump_sites.py -v && just dev-test`. Tests that assert the old predicate's text are updated to `valid_predicate`'s. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add artmind/graph_query.py artmind/vector_query.py artmind/survey.py artmind/topics artmind/conflicts.py artmind/webui/curate_routes.py artmind/temporal.py artmind/supersession_records.py artmind/migrations/document_valid_time_names.py artmind/setup.py test/
git commit -m "feat(query): one valid-time predicate over prefixed names; fold documents' unprefixed dates"
```

**Beyond the suite** (read-only until the migration): in banking_corpus, with the user's yes, `cd ~/artmind_vaults/banking_corpus && artmind setup` (inside the vault, with the worktree code: `uv run --project $WT artmind setup`) reports `document_valid_time_names.documents` = the superseded-document count; a second run reports 0. Then, after a retime commit has dated passages (Task 12): `ARTMIND_NO_PROXY=1 uv run --project $WT artmind query vector-text "interest rate" --domain banking --asOf 2025-06-30 --compact` returns fewer rows than without `--asOf` (acceptance §10 "Retrieval filters"), and `--asOf 2026` includes 2026-dated rows.

---

## Part 5 — Declared identity and dated happenings (spec §5.2, §5.3; rollout step 5)

Part 5's tasks change ids. Read "What breaks if a vault runs Part 5's code before its migration" above. Tasks 14–19 land on one branch and merge together with Task 20; a release that has Task 15 without Task 20 must not reach a vault.

### Task 14: `identity` in schemas, its one reader, its checks

**Files:**
- Modify: `artmind/schema_validate.py` — `IDENTITIES`, `class_identity(schema, cls)`; ERROR on a value outside the three; WARNs in `identity_warnings(schema, name)`; `warn_all` and `domains validate` include them.
- Modify: `artmind/domains/meta.yaml` — an `identities:` block describing the three values (for schema authors; `domains validate` reads its keys like `kinds`).
- Modify: every shipped schema — explicit `identity:` on each class per spec §5.3's table and question 3:
  - `personal_journal` (and its skills-assets copy): PERSON, LOCATION, ORGANIZATION, IDEA, OBJECT `name`; EVENT, ACTIVITY, STATE_CHANGE, PLAN `occasion`; EMOTION `category`; ACTIVITY and PLAN `kind: occurrent`; ACTIVITY's description drops "recurring or single" and the "things people do regularly" sentence ("One occasion of doing something: a task, pursuit or outing on a given day."); PLAN's `timeframe` gains `temporal: valid_from`.
  - `banking.*`: TRANSACTION, SAR, INCIDENT_EVENT, GOVERNANCE_DECISION, AUDIT_FINDING `occasion`; every other class `name`.
  - `contracts`: AMENDMENT `name` (document entity), all others `name`.
  - other schemas: identity from kind, written explicitly.
- Test: `test/test_class_identity.py` (new), `test/test_shipped_schema_identity.py` (new).

- [ ] **Step 1: Write the failing tests.** `test/test_class_identity.py`:

```python
"""`schema_validate.class_identity` and its checks (spec 2026-11-15 §5.3)."""
import pytest

from artmind import schema_validate as sv

META = {"kinds": {"recurrent": "", "occurrent": ""}}


@pytest.mark.parametrize("decl, expected", [
    ({"kind": "recurrent"}, "name"),
    ({"kind": "occurrent"}, "occasion"),
    ({"kind": "recurrent", "identity": "category"}, "category"),
    ({"kind": "occurrent", "identity": "name"}, "name"),
])
def test_identity_defaults_from_kind(decl, expected):
    assert sv.class_identity({"entity_types": {"X": decl}}, "X") == expected


def test_an_undeclared_class_is_an_occasion():
    assert sv.class_identity({"entity_types": {}}, "HEALTH_INCIDENT") == "occasion"


def test_unknown_identity_is_an_error():
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "person"}}}
    assert any("identity" in e for e in sv.validate_schema(schema, META))


def test_occasion_with_recurrent_kind_warns():
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "occasion"}}}
    assert any("never history" in w for w in sv.identity_warnings(schema, "s"))


def test_name_class_with_a_valid_from_property_warns_unless_document_entity():
    props = {"properties": {"d": {"temporal": "valid_from"}}}
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "name", **props},
                               "Y": {"kind": "occurrent", "identity": "name", **props}},
              "document_entity": {"classes": ["Y"]}}
    warnings = sv.identity_warnings(schema, "s")
    assert any(".X" in w for w in warnings) and not any(".Y" in w for w in warnings)
```

`test/test_shipped_schema_identity.py`:

```python
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

SCHEMAS = Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"
JOURNAL = {"PERSON": "name", "LOCATION": "name", "ORGANIZATION": "name", "IDEA": "name", "OBJECT": "name",
           "EVENT": "occasion", "ACTIVITY": "occasion", "STATE_CHANGE": "occasion", "PLAN": "occasion",
           "EMOTION": "category"}


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_every_class_declares_identity(path):
    types = yaml.safe_load(path.read_text(encoding="utf-8")).get("entity_types") or {}
    assert [c for c, d in types.items() if "identity" not in (d or {})] == []


def test_journal_identities():
    schema = yaml.safe_load((SCHEMAS / "personal_journal_schema.yaml").read_text(encoding="utf-8"))
    assert {c: sv.class_identity(schema, c) for c in schema["entity_types"]} == JOURNAL
    assert schema["entity_types"]["ACTIVITY"]["kind"] == "occurrent"
    assert "recurring or single" not in schema["entity_types"]["ACTIVITY"]["description"]


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_no_identity_warning_in_shipped_schemas(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert sv.identity_warnings(schema, path.stem) == []
```

The WARN is the narrowed one from question 20: with the spec's wording the last test would fail on 20 recurrent classes.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_class_identity.py test/test_shipped_schema_identity.py -v` — expected: FAIL.

- [ ] **Step 3: Implement** in `schema_validate.py`:

```python
IDENTITIES = ("name", "occasion", "category")


def class_identity(schema: dict, entity_class: str) -> str:
    """What makes two same-name mentions one entity (spec 2026-11-15 §5.3). The one reader.

    Declared `identity`, else `name` for `kind: recurrent` and `occasion` otherwise -- an
    undeclared class is occurrent by `observations.class_kind`'s rule, so it is an occasion.
    """
    decl = ((schema or {}).get("entity_types") or {}).get(entity_class) or {}
    declared = decl.get("identity")
    if declared in IDENTITIES:
        return declared
    return "name" if decl.get("kind") == "recurrent" else "occasion"


def classes_with_identity(schema: dict, *identities: str) -> set[str]:
    return {c for c in (schema.get("entity_types") or {}) if class_identity(schema, c) in identities}


def identity_warnings(schema: dict, schema_name: str = "") -> list[str]:
    out = []
    doc_classes = set(((schema.get("document_entity") or {}).get("classes")) or [])
    for cls, decl in (schema.get("entity_types") or {}).items():
        decl = decl or {}
        ident = class_identity(schema, cls)
        where = f"{schema_name}.entity_types.{cls}"
        if ident == "occasion" and decl.get("kind") == "recurrent":
            out.append(f"{where}: identity: occasion with kind: recurrent -- its changes become "
                       "separate entities, never history")
        dated = any(isinstance(p, dict) and p.get("temporal") == "valid_from"
                    for p in (decl.get("properties") or {}).values())
        if ident == "name" and decl.get("kind") == "occurrent" and dated and cls not in doc_classes:
            out.append(f"{where}: a dated occurrent class with identity: name -- its mentions on "
                       "different days fold into one entity; declare identity: occasion")
    return out
```

In `validate_schema`'s class loop: `if decl.get("identity") is not None and decl["identity"] not in IDENTITIES: errors.append(...)`. Task 10's temporary `class_identity` is replaced by this one.

- [ ] **Step 4: Edit the shipped schemas** as listed. Put `identity:` on the line after `kind:` in every class.

- [ ] **Step 5: Run and confirm.** `uv run --group dev pytest test/test_class_identity.py test/test_shipped_schema_identity.py test/test_shipped_schema_temporal.py test/test_schema_validate.py test/test_journal_schema_guidance.py -v` — expected: PASS. No code reads `identity` yet except validation, so the suite is otherwise unchanged; ACTIVITY/PLAN's `kind` change does alter `class_kind` for new extractions, which Task 15 accounts for.

- [ ] **Step 6: Commit.**

```bash
git add artmind/schema_validate.py artmind/cli.py artmind/domains artmind/skills/artmind-create-schema/assets test/test_class_identity.py test/test_shipped_schema_identity.py
git commit -m "feat(schemas): declare identity per class; journal ACTIVITY and PLAN are occasions"
```

---

### Task 15: Day keys for `occasion` classes

**Files:**
- Modify: `artmind/observations.py` — `aggregate_key(..., occurs_on=None)`, `observation_id` unchanged (already chunk-scoped), `OCCASION_SEP = "~"`, `split_occasion(name_part)`; `build_observation(..., identity_kind=None)` passes `occurs_on` for an occasion.
- Modify: `artmind/ingest.py` — `_build_observations` passes `identity_kind=class_identity(schema, cls)`.
- Modify: `artmind/retime.py` — `retime_observations` re-derives `_kind` from the schema (`class_kind`) and recomputes `key` (`aggregate_key` with the day) for every non-table observation.
- Modify: `artmind/update.py` — `_find_existing_entity` (line ~209) and the confirm path (line ~432) pass the day for occasion classes; `_stored_identity` keeps working because the name part carries the day.
- Modify: `artmind/projection.py` — `merge_observations` uses `override_key` (always passed by `rebuild`) — no change; `_choose_name` is unchanged (the canonical name carries no day).
- Modify: `artmind/graph_query.py` — `entity_history --name` matches `split(o.key,'|')[0] = $nameKey OR split(o.key,'|')[0] STARTS WITH $nameKey + '~'`.
- Test: `test/test_occasion_keys.py` (new), `test/test_retime_valid_time.py` (append).

- [ ] **Step 1: Write the failing tests.** `test/test_occasion_keys.py`:

```python
"""Day keys for occasion classes (spec 2026-11-15 §5.2, questions 1-2). Invented names."""
from artmind import ingest as ing
from artmind.observations import aggregate_key, key_string, split_occasion

SCHEMA = {"entity_types": {
    "ACTIVITY": {"kind": "occurrent", "identity": "occasion", "properties": {"date_or_time": {"temporal": "valid_from"}}},
    "PERSON": {"kind": "recurrent", "identity": "name", "properties": {}}}}


def test_the_day_goes_in_the_name_part():
    key = aggregate_key("Tennis", "ACTIVITY", "pj", occurs_on="2024-03-30")
    assert key == ("tennis~2024-03-30", "ACTIVITY", "pj")
    assert key_string(key).count("|") == 2
    assert split_occasion(key[0]) == ("tennis", "2024-03-30")
    assert split_occasion("tennis") == ("tennis", None)


def _obs(name, cls, day_prop, doc_date="2024-03-30", source="filename"):
    entities = [{"id": "e1", "name": name, "entity_class": cls, "chunk_id": "c1", "_domain": "pj"}]
    props = [{"id": "e1", "properties": {"date_or_time": day_prop} if day_prop else {}}]
    doc = {"id": "d1", "_valid_from": doc_date, "_valid_time_source": source}
    schema = {**SCHEMA, "temporal": {"defaults": {"time_source": "default_ingestion"}}}
    return ing._build_observations(entities, props, {}, schema, doc)[0]


def test_same_name_two_days_two_keys():
    a, b = _obs("Tennis", "ACTIVITY", "2024-03-30"), _obs("Tennis", "ACTIVITY", "2024-04-02")
    assert a["key"] != b["key"] and a["key"].startswith("tennis~2024-03-30|")


def test_an_undated_mention_keys_on_its_documents_day():
    assert _obs("Tennis", "ACTIVITY", None, doc_date="2024-05-01")["key"] == "tennis~2024-05-01|ACTIVITY|pj"


def test_a_default_document_date_adds_no_day():
    obs = _obs("Tennis", "ACTIVITY", None, doc_date="2026-11-15", source="default_ingestion")
    assert obs["key"] == "tennis|ACTIVITY|pj"


def test_a_range_keys_on_its_start_day():
    assert _obs("Lake trip", "ACTIVITY", "2023-09-07 to 2023-09-08")["key"] == "lake trip~2023-09-07|ACTIVITY|pj"


def test_name_classes_are_unchanged():
    assert _obs("Alex", "PERSON", None)["key"] == "alex|PERSON|pj"
```

Append to `test/test_retime_valid_time.py`:

```python
def test_retime_rekeys_an_occasion_and_rederives_kind():
    schema = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion",
                                            "properties": {"date_or_time": {"temporal": "valid_from"}}}}}
    staged = [{"id": "o1", "key": "tennis|ACTIVITY|pj", "entity_class": "ACTIVITY", "_domain": "pj", "_kind": "recurrent",
               "canonical_name": "Tennis", "name": "tennis", "date_or_time": "2024-03-30"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2024-03-30", "_valid_time_source": "filename"}, schema))
    assert (new[0]["key"], new[0]["_kind"]) == ("tennis~2024-03-30|ACTIVITY|pj", "occurrent")
    assert new[0]["id"] == "o1"      # observation ids never change


def test_retime_leaves_table_observations_keys_alone():
    schema = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion"}}}
    staged = [{"id": "o1", "key": "row 1|ACTIVITY|pj", "entity_class": "ACTIVITY", "_domain": "pj", "source_kind": "table"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2024-03-30", "_valid_time_source": "filename"}, schema))
    assert new[0]["key"] == "row 1|ACTIVITY|pj"
```

Observation ids stay as they are: `observation_id` hashes chunk + name + class + domain, and one chunk is one day. Keeping ids stable is what lets the re-key move a node's `key` in place (`_merge_relabeled` replaces by id), so `ASSERTS_RELATION` edges survive.

Table observations: table2graph's identity is declared by its mapping (`identity=built.name`); day keys would split a declared identity, so tables keep today's key whatever the class's `identity`. Say so in review if a mapped occasion class should be day-keyed too.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_occasion_keys.py test/test_retime_valid_time.py -v` — expected: FAIL (`occurs_on` unknown).

- [ ] **Step 3: Implement.** In `observations.py`:

```python
#: Joins an occasion's name and day inside the key's name part: `tennis~2024-03-30`.
#: Never a fourth `|` part -- every key parser expects three (spec 2026-11-15 question 1).
OCCASION_SEP = "~"


def aggregate_key(
    canonical_name: str | None, entity_class: str, domain: str, *,
    identity: str | None = None, occurs_on: str | None = None,
) -> tuple[str, str, str]:
    """... (docstring as today, plus:) `occurs_on`, a `YYYY-MM-DD` day, is appended to the
    name part for an `identity: occasion` class: the same happening on two days is two entities."""
    name = normalize_identity(identity) if identity else normalize_name(canonical_name)
    if occurs_on and not identity:
        name = f"{name}{OCCASION_SEP}{occurs_on[:10]}"
    return (name, entity_class or "", domain or "")


def split_occasion(name_part: str) -> tuple[str, str | None]:
    """`("tennis", "2024-03-30")` for `tennis~2024-03-30`; `(name_part, None)` otherwise."""
    head, sep, day = (name_part or "").rpartition(OCCASION_SEP)
    if sep and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return head, day
    return name_part, None


def occasion_day(valid_from: str | None, precision: str | None) -> str | None:
    """The day an occasion keys on: its `_valid_from` unless that is only a default date."""
    if not valid_from or precision == "none":
        return None
    return valid_from[:10]
```

`build_observation(..., identity_kind: str | None = None)`: compute the effective `_valid_from` and precision first (they are already computed below; move those lines above `key = …`), then:

```python
    occurs_on = occasion_day(effective_from, valid_precision) if identity_kind == "occasion" else None
    key = aggregate_key(canonical_name, entity_class, obs_domain, identity=identity, occurs_on=occurs_on)
```

`_build_observations` passes `identity_kind=class_identity(schema, entity.get("entity_class") or "")`. `retime_observations`, for an observation without `source_kind == "table"`:

```python
        new["_kind"] = class_kind(schema, cls)
        name_part = obs.get("canonical_name") or obs.get("name") or ""
        occurs_on = (occasion_day(new.get("_valid_from"), new.get("_valid_precision"))
                     if class_identity(schema, cls) == "occasion" else None)
        new["key"] = key_string(aggregate_key(name_part, cls, obs.get("_domain") or "", occurs_on=occurs_on))
```

A key built from a stored identity (`update` link path) keeps today's form: `identity=` ignores `occurs_on`.

In `update.py`, `_find_existing_entity(session, name, entity_class, domain, occurs_on=None)` and the confirm path pass `occurs_on` from the fact's date for an occasion class (load the schema once with `_load_schema`).

In `graph_query.entity_history`'s name branch:

```python
      AND (split(o.key, '|')[0] = $nameKey
           OR split(o.key, '|')[0] STARTS WITH $nameKey + '~'
           OR toLower(o.name) = $nameLower
           OR toLower(o.canonical_name) = $nameLower){class_clause}"""
```

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_occasion_keys.py test/test_retime_valid_time.py test/test_projection_key.py test/test_update.py test/test_document_dates.py test/test_table2graph.py -v && just dev-test` — expected: PASS. Tests that build journal fixtures with ACTIVITY as recurrent and assert its key need `identity: name` in their inline schema, or the new key.

- [ ] **Step 5: Commit.**

```bash
git add artmind/observations.py artmind/ingest.py artmind/retime.py artmind/update.py artmind/graph_query.py test/
git commit -m "feat(identity): occasion classes key on name and day"
```

**Beyond the suite** (no write, counts only): the vault's own schema is not changed until Task 20, so load the worktree's shipped journal schema in a one-off script: `cd ~/artmind_vaults/brain01 && uv run --project $WT python -I -c "import json,glob,yaml; from artmind.retime import retime_observations; s=yaml.safe_load(open('$WT/artmind/domains/schemas/personal_journal_schema.yaml')); …"` over the first 50 staging folders, printing per class the number of distinct keys before and after. Expect ACTIVITY's count to rise and PERSON's to stay the same. Print no names.

---

### Task 16: `graph timeline` lists every `occasion` class

**Files:**
- Modify: `artmind/graph_query.py` — `timeline` (lines ~2211–2262) selects classes by `classes_with_identity(schema, "occasion")` instead of `kind == "occurrent"`; rows gain `_valid_precision`, `_valid_time_of_day`; precision `none` rows sort last.
- Test: `test/test_timeline_identity.py` (new).

- [ ] **Step 1: Write the failing test.**

```python
"""`graph timeline` covers occasion classes (spec 2026-11-15 §5.2)."""
from _neo4j_recorder import Recorder

from artmind import graph_query as gq


def test_timeline_labels_are_the_occasion_classes(monkeypatch):
    schema = {"entity_types": {"EVENT": {"kind": "occurrent"}, "ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
                               "EMOTION": {"kind": "recurrent", "identity": "category"},
                               "AMENDMENT": {"kind": "occurrent", "identity": "name"}}}
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: schema)
    sent = []
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: sent.append(params) or [])
    gq.timeline(["pj"], "2024-01-01", "2024-12-31")
    assert sent[0]["labels"] == ["ACTIVITY", "EVENT"]
```

(If `timeline` runs its query through another helper, patch that one; `rg -n "def timeline" -A60 artmind/graph_query.py`.)

- [ ] **Step 2: Run, implement, run.** Replace the `kind == "occurrent"` loop with `for cls in classes_with_identity(schema, "occasion"):`. Run `uv run --group dev pytest test/test_timeline_identity.py $(rg -l "timeline" test) -v` — expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add artmind/graph_query.py test/test_timeline_identity.py
git commit -m "feat(timeline): list every occasion class"
```

---

### Task 17: The naming rule and name stripping

**Files:**
- Modify: `artmind/domains/meta.yaml` — new `identity_naming_rules: {occasion: …}`.
- Modify: `artmind/prompt_builder.py` — `OCCASION_NAMING_RULE` default; `_render_class_block` uses the identity rule for an `occasion` class instead of the kind rule (lines ~66–79).
- Modify: `artmind/observations.py` — `strip_occasion_date(name) -> str`.
- Modify: `artmind/ingest.py` — `_build_observations` strips the canonical name of an `occasion` observation before `build_observation`.
- Modify: `artmind/retime.py` — `retime_observations` strips `canonical_name` of occasion observations (before re-keying, Task 15).
- Test: `test/test_occasion_names.py` (new), the prompt-builder test file (append).

- [ ] **Step 1: Write the failing tests.** `test/test_occasion_names.py`:

```python
"""No dates in happening names (spec 2026-11-15 §5.2, question 5). Invented names."""
import pytest

from artmind.observations import normalize_name, strip_occasion_date


@pytest.mark.parametrize("raw, expected", [
    ("Tennis - 2024-03-30", "Tennis"),
    ("Tennis on 2024-03-30", "Tennis"),
    ("Tennis 2024-03-30", "Tennis"),
    ("Tennis (2024-03-30)", "Tennis"),
    ("2024-03-30 Tennis", "Tennis"),
    ("Tennis on Saturday", "Tennis"),
    ("Tennis today", "Tennis"),
    ("Tennis, 30 March 2024", "Tennis"),
    ("Tennis on the 30th", "Tennis"),
    ("Lantern festival lights", "Lantern festival lights"),
    ("Q3 review", "Q3 review"),
    ("Flight to Lisbon", "Flight to Lisbon"),
])
def test_strip(raw, expected):
    assert strip_occasion_date(raw) == expected


def test_a_name_that_is_only_a_date_is_kept():
    assert strip_occasion_date("2024-03-30") == "2024-03-30"


def test_stripped_variants_share_one_normalised_name():
    names = ["Tennis - 2024-03-30", "Tennis on 2024-04-02", "tennis today"]
    assert {normalize_name(strip_occasion_date(n)) for n in names} == {"tennis"}
```

Append to the prompt-builder tests:

```python
def test_occasion_classes_get_the_no_date_naming_rule():
    from artmind.prompt_builder import OCCASION_NAMING_RULE, _render_class_block

    meta = {"kind_naming_rules": {"occurrent": "a date ... belongs IN the name."}}
    block = _render_class_block("ACTIVITY", {"kind": "occurrent", "identity": "occasion"}, meta)
    assert OCCASION_NAMING_RULE.split(".")[0] in block and "belongs IN the name" not in block
    other = _render_class_block("AMENDMENT", {"kind": "occurrent", "identity": "name"}, meta)
    assert "belongs IN the name" in other
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_occasion_names.py -v` and the prompt test — expected: ImportError / FAIL.

- [ ] **Step 3: Implement.** In `observations.py`:

```python
_WEEKDAYS = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_DAYWORDS = rf"(?:today|tonight|yesterday|tomorrow|this (?:morning|afternoon|evening)|{_WEEKDAYS})"
_DATE_TOKEN = (r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}"
               r"|[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|the\s+\d{1,2}(?:st|nd|rd|th))")
_OCCASION_TAIL_RE = re.compile(
    rf"(?:\s*[-–—:,]\s*|\s*\(\s*|\s+(?:on|at)\s+|\s+)(?:{_DATE_TOKEN}|{_DAYWORDS})\s*\)?\s*$", re.IGNORECASE)
_OCCASION_HEAD_RE = re.compile(rf"^\s*{_DATE_TOKEN}\s*[-–—:,]?\s+", re.IGNORECASE)


def strip_occasion_date(name: str | None) -> str:
    """An occasion's name without a trailing or leading date, weekday, ordinal or "today"
    (spec 2026-11-15 §5.2). Pure. A name that is only a date is returned unchanged."""
    text = (name or "").strip()
    for _ in range(3):
        before = text
        text = _OCCASION_TAIL_RE.sub("", text).strip()
        text = _OCCASION_HEAD_RE.sub("", text).strip()
        if text == before:
            break
    return text or (name or "").strip()
```

In `_build_observations`, for `class_identity(...) == "occasion"`: `canonical = strip_occasion_date(canonical)` before `build_observation`. In `retime_observations`, for occasion observations not from a table: `obs["canonical_name"] = strip_occasion_date(obs.get("canonical_name") or obs.get("name"))` before the key is recomputed. The verbatim `name` is never changed.

In `meta.yaml`:

```yaml
# Naming rules by declared identity (spec 2026-11-15 §5.2). Replaces the kind rule for the class.
identity_naming_rules:
  occasion: >-
    naming: name the happening as the text does ("Tennis", "Lake trip", "Lantern
    festival lights"). NEVER add a date, a weekday or "today" to the name -- the
    date goes in the class's date property.
```

In `prompt_builder.py`:

```python
#: Used when a vault's own meta.yaml predates `identity_naming_rules` (it is the user's copy).
OCCASION_NAMING_RULE = (
    'naming: name the happening as the text does ("Tennis", "Lake trip", "Lantern festival lights"). '
    'NEVER add a date, a weekday or "today" to the name -- the date goes in the class\'s date property.'
)
```

and in `_render_class_block`:

```python
    from artmind.schema_validate import class_identity

    if class_identity({"entity_types": {cls: decl}}, cls) == "occasion":
        kind_rule = ((meta or {}).get("identity_naming_rules") or {}).get("occasion") or OCCASION_NAMING_RULE
    else:
        kind_rule = ((meta or {}).get("kind_naming_rules") or {}).get(decl.get("kind"))
```

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_occasion_names.py $(rg -l "_render_class_block|assemble_entities_prompt" test) test/test_occasion_keys.py test/test_retime_valid_time.py -v && just dev-test` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/domains/meta.yaml artmind/prompt_builder.py artmind/observations.py artmind/ingest.py artmind/retime.py test/
git commit -m "feat(identity): happenings are named without dates; staged names are stripped"
```

**Beyond the suite** (read-only, counts only): in brain01, run `strip_occasion_date` over the staged EVENT/ACTIVITY/STATE_CHANGE/PLAN canonical names and print the share still containing `\d{4}-\d{2}-\d{2}` (target < 5%, acceptance §10). Never print a name.

---

### Task 18: Same-as, twins and the name vocabulary gated by identity

**Files:**
- Modify: `artmind/canonicalize.py` — rename `recurrent_classes` → `named_classes(schema)` returning `classes_with_identity(schema, "name", "category")`; callers at lines ~90 and ~519; keep a `recurrent_classes = named_classes` alias only if `rg` finds a caller outside `artmind/` (none expected).
- Modify: `artmind/sameas.py` — `propose` refuses (returns `None`, logs, counts) a group that pairs `category` members or `occasion` members whose days differ (`split_occasion`), or any member whose class is unknown to the schema and is not `name`.
- Modify: `artmind/refine_graph.py` — `propose_merges` counts `skipped_identity` from `sameas.propose`'s refusal; the clustering step (line ~326) fetches only `name`/`occasion` classes.
- Modify: `artmind/conflicts.py` — `candidate_pairs` (lines ~37–90) skips `category` sources and pairs `occasion` entities only on the same day.
- Modify: `artmind/insights/detectors/same_as.py` — `_plan` uses classes whose identity is `name` (and `occasion` with a same-day pairing rule in `candidate_pairs`) instead of `classes_of_kind("recurrent")` (line ~156); `RunContext` gains `identity(cls)` reading the schema.
- Modify: `artmind/identity.py` — `twin_groups(rows, schema=None)` skips rows whose class is `occasion` or `category`; `cli.py` line ~3990 passes the merged schema of the queried domains.
- Test: `test/test_identity_gates.py` (new); update `test/test_canonicalize.py`, `test/test_identity.py`, the same-as detector tests.

- [ ] **Step 1: Write the failing tests.**

```python
"""Identity gates (spec 2026-11-15 §5.3). Recorder-based: assert on what was sent."""
from _neo4j_recorder import Recorder

from artmind import canonicalize, identity, sameas

SCHEMA = {"entity_types": {"PERSON": {"kind": "recurrent"}, "EMOTION": {"kind": "recurrent", "identity": "category"},
                           "ACTIVITY": {"kind": "occurrent", "identity": "occasion"}}}


def test_named_classes():
    assert canonicalize.named_classes(SCHEMA) == {"PERSON", "EMOTION"}


def test_propose_refuses_occasions_on_different_days(monkeypatch):
    monkeypatch.setattr(sameas, "_schema_for", lambda domain: SCHEMA)
    rec = Recorder()
    pid = sameas.propose(rec, ("tennis~2024-03-30", "ACTIVITY", "pj"), [("tennis match~2024-04-02", "ACTIVITY", "pj")],
                         source="test")
    assert pid is None and not rec.queries_containing("MERGE (p:SameAsProposal")


def test_propose_accepts_same_day_variants(monkeypatch):
    monkeypatch.setattr(sameas, "_schema_for", lambda domain: SCHEMA)
    rec = Recorder()
    sameas.propose(rec, ("tennis~2024-03-30", "ACTIVITY", "pj"), [("tennis match~2024-03-30", "ACTIVITY", "pj")],
                   source="test")
    (cypher, params), = rec.queries_containing("MERGE (p:SameAsProposal")
    # param name as `propose` sends it today (`rg -n "members=" artmind/sameas.py`)
    assert params["members"] == ["tennis match~2024-03-30|ACTIVITY|pj", "tennis~2024-03-30|ACTIVITY|pj"]


def test_propose_refuses_category_members(monkeypatch):
    monkeypatch.setattr(sameas, "_schema_for", lambda domain: SCHEMA)
    assert sameas.propose(Recorder(), ("calm", "EMOTION", "pj"), [("serenity", "EMOTION", "pj")], source="t") is None


def test_twins_skip_occasion_and_category_rows():
    rows = [{"entity": {"_id": i, "name": "Tennis", "entity_class": "ACTIVITY"}} for i in ("a", "b")]
    rows += [{"entity": {"_id": i, "name": "Alex", "entity_class": "PERSON"}} for i in ("c", "d")]
    groups = identity.twin_groups(rows, schema=SCHEMA)
    assert [g["entity_class"] for g in groups] == ["PERSON"]
```

Add a conflicts test asserting the `candidate_pairs` fetch Cypher receives `$classes` without `EMOTION`, and a detector test asserting `_plan` returns no `category` class (follow the existing detector tests' `RunContext` fixture: `rg -l "same_as" test/test_insights*`).

`sameas._schema_for(domain)` is new: `temporal.load_schema(domain)` with the domain-family merge already used elsewhere; cached per call.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_identity_gates.py -v` — expected: FAIL.

- [ ] **Step 3: Implement.** In `sameas.py`:

```python
def identity_refusal(canonical: tuple[str, str, str], members: list[tuple[str, str, str]]) -> str | None:
    """Why this group may not be proposed, or None (spec 2026-11-15 §5.3)."""
    from artmind.observations import split_occasion
    from artmind.schema_validate import class_identity

    group = [canonical, *members]
    for name, cls, domain in group:
        ident = class_identity(_schema_for(domain), cls)
        if ident == "category":
            return f"identity:category ({cls})"
    occasion = [k for k in group if class_identity(_schema_for(k[2]), k[1]) == "occasion"]
    days = {split_occasion(k[0])[1] for k in occasion}
    if len(days) > 1:
        return "identity:occasion (different days)"
    return None
```

and at the top of `propose`: `refusal = identity_refusal(canonical, members); if refusal: logger.info(...); return None`. Callers that count (`refine_graph.propose_merges`, `conflicts`) treat `None` as `skipped_identity`. `approve` re-checks the same rule (an old open proposal could predate it) and refuses with the reason.

The other edits are as listed under **Files**; each replaces a `kind`-based class list with `classes_with_identity`.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_identity_gates.py test/test_canonicalize.py test/test_identity.py $(rg -l "sameas|propose_merges|candidate_pairs|same_as" test) -v && just dev-test` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/canonicalize.py artmind/sameas.py artmind/refine_graph.py artmind/conflicts.py artmind/insights artmind/identity.py artmind/cli.py test/
git commit -m "feat(identity): same-as, twins and the name vocabulary follow declared identity"
```

---

### Task 19: Journal OBJECT: a declared `type` list, happenings moved to ACTIVITY

**Files:**
- Modify: `artmind/domains/schemas/personal_journal_schema.yaml` and its skills-assets copy — OBJECT: new description (spec §5.3 wording), `types:` (closed list: device, document, vehicle, clothing, household_item, possession, gift, food, medication, medical_item, decoration, pet) replacing `type_examples`; new top-level `retime_reclassify: [{from: OBJECT, to: ACTIVITY, name_words: [flight, train, cab, taxi, ride, trip, journey, booking]}]`.
- Modify: `artmind/prompt_builder.py` — `_render_class_block` renders `types:` as "type: exactly one of: a | b | …" (closed), `type_examples` as today.
- Modify: `artmind/schema_validate.py` — ERROR on an empty or non-list `types`; ERROR when both `types` and `type_examples` are given; checks on `retime_reclassify` (classes exist, `name_words` a non-empty list of strings); readers of `type_examples` (`structured/semantics.py:352`, `schema_reference.py:64`, `insights/detectors/health.py:71`) read `types or type_examples` through a new `schema_validate.class_types(decl)`.
- Modify: `artmind/retime.py` — `retime_observations` applies `retime_reclassify`: an observation of class `from` whose `canonical_name` contains one of `name_words` as a whole word moves to `to` (class, `_kind`, key recomputed; `type` dropped).
- Test: `test/test_object_types.py` (new); property-audit report of out-of-list `type` values is **Blocked by SBP Task 8** (append there: `row["outside_types"]` count for the `type` key).

- [ ] **Step 1: Write the failing tests.**

```python
"""OBJECT's declared type list and the happening move (spec 2026-11-15 §5.3). Invented names."""
from pathlib import Path

import yaml

from artmind import schema_validate as sv
from artmind.prompt_builder import _render_class_block
from artmind.retime import retime_observations

JOURNAL = yaml.safe_load((Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"
                          / "personal_journal_schema.yaml").read_text(encoding="utf-8"))


def _obs(result):
    return result[0] if isinstance(result, tuple) else result


def test_object_declares_a_closed_type_list():
    obj = JOURNAL["entity_types"]["OBJECT"]
    assert "type_examples" not in obj and "medication" in obj["types"] and "flight" not in obj["types"]
    assert "not an object" in obj["description"]


def test_types_render_as_a_closed_choice():
    block = _render_class_block("OBJECT", {"kind": "recurrent", "types": ["device", "food"]}, {})
    assert "type: exactly one of: device | food" in block


def test_empty_types_is_an_error():
    schema = {"entity_types": {"OBJECT": {"kind": "recurrent", "types": []}}}
    assert any("types" in e for e in sv.validate_schema(schema, {"kinds": {"recurrent": "", "occurrent": ""}}))


def test_retime_moves_travel_objects_to_activity():
    staged = [{"id": "o1", "key": "flight to lisbon|OBJECT|pj", "entity_class": "OBJECT", "_domain": "pj",
               "canonical_name": "Flight to Lisbon", "name": "flight", "type": "flight"},
              {"id": "o2", "key": "bread|OBJECT|pj", "entity_class": "OBJECT", "_domain": "pj",
               "canonical_name": "Bread", "type": "food"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2024-03-30", "_valid_time_source": "filename"}, JOURNAL))
    moved, kept = new
    assert (moved["entity_class"], moved["key"], "type" in moved) == ("ACTIVITY", "flight to lisbon~2024-03-30|ACTIVITY|pj", False)
    assert (kept["entity_class"], kept["type"]) == ("OBJECT", "food")
```

- [ ] **Step 2: Run, implement, run.** Implement as listed; the reclassify step runs first in `retime_observations` so the moved observation is then dated, stripped and re-keyed as an ACTIVITY. Run `uv run --group dev pytest test/test_object_types.py test/test_schema_validate.py test/test_journal_schema_guidance.py $(rg -l "type_examples" test) -v && just dev-test` — expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add artmind/domains/schemas/personal_journal_schema.yaml artmind/skills/artmind-create-schema/assets artmind/prompt_builder.py artmind/schema_validate.py artmind/retime.py artmind/structured/semantics.py artmind/schema_reference.py artmind/insights/detectors/health.py test/
git commit -m "feat(journal): OBJECT has a closed type list; trips and rides move to ACTIVITY"
```

**Beyond the suite** (read-only, counts only): in brain01 staging, the number of OBJECT observations `retime_reclassify` would move (spec §5.3 evidence: tens, mostly travel), printed as a count.

---

### Task 20: The re-key migration inside `ingest retime`

**Files:**
- Create: `artmind/rekey.py` — `key_moves(folders_before, folders_after) -> dict[old_key, set[new_key]]` (from each staged observation's key before and after `retime_observations`, by observation id); `migrate_same_as(path, moves, schema_for) -> (groups, report)`; `close_proposals(session, moves, schema_for, dry_run)`; `migrate_records(moves, dry_run)` for `syntheses/` (record key → the new key with the most observations; ties by key; the other new keys get no synthesis), `conflicts/` (a record whose two entities no longer share a key is closed as `stale_identity`), `mart_dimension/` (`member_key` → each new key, one record per new key, same levels and source).
- Modify: `artmind/retime.py` — `retime_domain` computes `moves` from the planned changes before committing; when `moves` is non-empty, runs the three migrations (dry run: counts only), then commits folders, then one rebuild, then records `identity_version: 1` for the domain on `:ProjectionState` (inside the rebuild's state write: `projection.record_rebuild(..., identity_versions={domain: 1})`).
- Modify: `artmind/projection.py` — `record_rebuild` accepts and stores `identity_versions`; `read_state` returns it.
- Modify: `artmind/retime.py` `_effect` — new changes `{kind: "entity_rekey", count, sample: [class names]}`, `{kind: "same_as_groups", count}`, `{kind: "sameas_proposals_closed", count}`, `{kind: "curation_records", count}`; `records` lists `.artmind/same_as.yaml` and `.artmind/data/curation/...` folders touched; `reversible: true` with `undo`: "git restore the listed records and the staging folders, then ingest retime again with the previous schema".
- Modify: `artmind/cli_conventions.py` `EFFECT_KINDS` += those four.
- Modify: `artmind/ingest.py` `rebuild_projection`, `artmind/vault_sync.py` and `ingest sync|async` — the one-line warning when a domain's schema declares occasion classes and `read_state()["identity_versions"][domain]` is absent (see "What breaks…").
- Modify: `artmind/catalogue` — `same_as.yaml` change bumps nothing (catalogue reads the graph); the rebuild bumps the domains as today (`projection.py:1406`).
- Test: `test/test_rekey.py` (new), `test/test_dry_run_ingest_retime.py` (append: the re-key dry run sends no write Cypher, writes no record and `same_as.yaml` is byte-identical).

- [ ] **Step 1: Write the failing tests.** `test/test_rekey.py`:

```python
"""Re-keying occasions (spec 2026-11-15 §5.2, §8; questions 12-13). Synthetic keys only."""
import yaml

from _neo4j_recorder import Recorder

from artmind import rekey

SCHEMA = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
                           "EMOTION": {"kind": "recurrent", "identity": "category"},
                           "PERSON": {"kind": "recurrent", "identity": "name"}}}
MOVES = {"tennis|ACTIVITY|pj": {"tennis~2024-03-30|ACTIVITY|pj", "tennis~2024-04-02|ACTIVITY|pj"},
         "tennis match|ACTIVITY|pj": {"tennis match~2024-03-30|ACTIVITY|pj"}}


def test_key_moves_compares_by_observation_id():
    before = [{"id": "o1", "key": "tennis|ACTIVITY|pj"}, {"id": "o2", "key": "alex|PERSON|pj"}]
    after = [{"id": "o1", "key": "tennis~2024-03-30|ACTIVITY|pj"}, {"id": "o2", "key": "alex|PERSON|pj"}]
    assert rekey.key_moves(before, after) == {"tennis|ACTIVITY|pj": {"tennis~2024-03-30|ACTIVITY|pj"}}


def test_same_as_groups_split_per_shared_day(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text(yaml.safe_dump([
        {"canonical": "tennis|ACTIVITY|pj", "members": ["tennis|ACTIVITY|pj", "tennis match|ACTIVITY|pj"]},
        {"canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "serenity|EMOTION|pj"]},
        {"canonical": "alex|PERSON|pj", "members": ["alex|PERSON|pj", "alexander|PERSON|pj"]}]))
    groups, report = rekey.migrate_same_as(path, MOVES, lambda d: SCHEMA, write=True)
    assert groups == [
        {"canonical": "tennis~2024-03-30|ACTIVITY|pj",
         "members": ["tennis match~2024-03-30|ACTIVITY|pj", "tennis~2024-03-30|ACTIVITY|pj"]},
        {"canonical": "alex|PERSON|pj", "members": ["alex|PERSON|pj", "alexander|PERSON|pj"]}]
    assert report == {"groups_before": 3, "groups_after": 2, "split": 1, "dropped_category": 1, "members_dropped": 0}
    assert yaml.safe_load(path.read_text()) == groups


def test_dry_run_writes_no_file(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("[]\n")
    rekey.migrate_same_as(path, MOVES, lambda d: SCHEMA, write=False)
    assert path.read_text() == "[]\n"


def test_open_proposals_across_days_are_closed_with_the_reason():
    open_rows = [{"id": "p1", "canonical": "tennis~2024-03-30|ACTIVITY|pj",
                  "members": ["tennis~2024-03-30|ACTIVITY|pj", "tennis~2024-04-02|ACTIVITY|pj"]},
                 {"id": "p2", "canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "serenity|EMOTION|pj"]}]
    rec = Recorder(lambda c, p: open_rows if "status = 'open'" in c else [])
    closed = rekey.close_proposals(rec, MOVES, lambda d: SCHEMA, dry_run=False)
    (cypher, params), = rec.queries_containing("SET p.status = 'rejected'")
    assert params["rows"] == [{"id": "p1", "reason": "identity:occasion"}, {"id": "p2", "reason": "identity:category"}]
    assert closed == 2


def test_dry_run_closes_nothing():
    rec = Recorder(lambda c, p: [{"id": "p2", "canonical": "calm|EMOTION|pj", "members": ["calm|EMOTION|pj", "x|EMOTION|pj"]}])
    assert rekey.close_proposals(rec, MOVES, lambda d: SCHEMA, dry_run=True) == 1
    assert not rec.queries_containing("SET p.status")
```

Open proposals must be read with their old member keys mapped through `moves` first: an open proposal stored before the migration names `tennis|ACTIVITY|pj`. `close_proposals` maps each member through `moves`; a member with several new keys makes the proposal cross-day, so it closes with `identity:occasion`. Add a test for that case.

Records tests (append): a synthesis record keyed `tennis|ACTIVITY|pj` is re-pointed to the new key with more observations (pass a `counts` map); a mart dimension record is copied to each new key; a conflict record whose sides now have different keys is closed. Assert on the files written under a tmp `CURATION_DIR`, never on counts alone.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_rekey.py -v` — expected: ImportError.

- [ ] **Step 3: Implement `artmind/rekey.py`.** Core of `migrate_same_as`:

```python
def migrate_same_as(path, moves: dict[str, set[str]], schema_for, *, write: bool) -> tuple[list[dict], dict]:
    """Rewrite same-as groups for re-keyed members (question 12). Pure apart from the write."""
    from artmind.observations import split_occasion
    from artmind.schema_validate import class_identity

    groups = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    out, report = [], {"groups_before": len(groups or []), "groups_after": 0, "split": 0,
                       "dropped_category": 0, "members_dropped": 0}
    for group in groups or []:
        members = group.get("members") or []
        canonical = group.get("canonical") or (members[0] if members else None)
        classes = {m.split("|")[1] for m in members if m.count("|") == 2}
        domain = canonical.split("|")[2] if canonical and canonical.count("|") == 2 else ""
        idents = {class_identity(schema_for(domain), c) for c in classes}
        if "category" in idents:
            report["dropped_category"] += 1
            continue
        if not any(m in moves for m in members):
            out.append(group)
            continue
        by_day: dict[str, list[str]] = {}
        for m in members:
            for new in sorted(moves.get(m, {m})):
                by_day.setdefault(split_occasion(new.split("|")[0])[1] or "", []).append(new)
        canon_new = {split_occasion(k.split("|")[0])[1] or "": k for k in moves.get(canonical, {canonical})}
        kept = 0
        for day, day_members in sorted(by_day.items()):
            if len(day_members) < 2:
                report["members_dropped"] += len(day_members)
                continue
            out.append({"canonical": canon_new.get(day, sorted(day_members)[0]), "members": sorted(day_members)})
            kept += 1
        report["split"] += 1 if kept != 1 else 0
    report["groups_after"] = len(out)
    if write:
        atomic_write_yaml(path, out)      # temp file under LOGS_DIR, then os.replace, like same_as.py's writer
    return out, report
```

Use `same_as.py`'s existing writer for the atomic write (`rg -n "def .*write|os.replace" artmind/same_as.py`) rather than a new one; keep the file's key order and comments policy the same as that writer. The format above (`canonical`/`members`) must match what `same_as.load_groups` reads; check it first (`sed -n 100,200p artmind/same_as.py`) and adapt both test and code to the real shape.

`close_proposals` reads `MATCH (p:SameAsProposal) WHERE p.status = 'open' RETURN p.id AS id, p.canonical AS canonical, p.members AS members`, maps members through `moves`, applies `sameas.identity_refusal`, and (not dry run) sends one `UNWIND $rows AS row MATCH (p:SameAsProposal {id: row.id}) SET p.status = 'rejected', p.resolved_at = $now, p.resolution_reason = row.reason`.

- [ ] **Step 4: Wire it into `retime_domain`.** Order inside `retime_domain`:
  1. retime every folder with `write=False`, collecting `(folder, before_obs, after_obs)`;
  2. `moves = rekey.key_moves(all_before, all_after)`;
  3. if `moves`: `migrate_same_as(..., write=not dry_run)`, `close_proposals(session, ..., dry_run)`, `migrate_records(moves, dry_run)`;
  4. dry run: return the effect;
  5. write changed folders, commit each with `defer_rebuild=True` (as today), one `rebuild_projection(domain)`, then record `identity_versions`.

  The rebuild after step 3 sees the new same-as groups; `affected_keys` includes the old keys through `keys_for_document` (prior keys), so old entities lose their observations and are deleted by the zero-observation rule, and new ones are created.

- [ ] **Step 5: Write the dry-run test** in `test/test_dry_run_ingest_retime.py`:

```python
def test_rekey_dry_run_writes_no_record_and_sends_no_write(tmp_path, monkeypatch):
    folder, _ = _ingested_occasion_under_the_old_schema(tmp_path, monkeypatch)   # new fixture: ACTIVITY recurrent
    same_as = tmp_path / "same_as.yaml"
    same_as.write_text(yaml.safe_dump([{"canonical": "tennis|ACTIVITY|journal",
                                        "members": ["tennis|ACTIVITY|journal", "tennis match|ACTIVITY|journal"]}]))
    monkeypatch.setattr("artmind.same_as.SAME_AS_PATH", same_as)
    rec = WritableRecorder(lambda c, p: [])
    monkeypatch.setattr("artmind.retime.neo4j_session", lambda: rec, raising=False)
    before = same_as.read_bytes()
    dry = _run("--dryRun")
    assert_no_write_cypher(rec)
    assert same_as.read_bytes() == before
    kinds = {c["kind"] for c in dry["effect"]["changes"]}
    assert {"entity_rekey", "same_as_groups"} <= kinds
```

The fixture `_ingested_occasion_under_the_old_schema` extends `test_document_dates._ingested_under_the_old_schema`: stage under a schema where ACTIVITY is `kind: recurrent` with no `identity`, then switch the schema file to `kind: occurrent, identity: occasion`.

- [ ] **Step 6: Run and confirm.** `uv run --group dev pytest test/test_rekey.py test/test_dry_run_ingest_retime.py test/test_document_dates.py test/test_catalogue_bump_sites.py test/test_cli_conventions.py -v && just dev-test` — expected: PASS.

- [ ] **Step 7: The pre-migration warning.** Add `projection.identity_migration_pending(domain) -> bool` (reads `read_state`, the domain's schema; true when an occasion class exists and no version is recorded) and print, once per process, `artmind: <domain> holds occasion classes whose entities were keyed before identity (spec 2026-11-15); run: artmind ingest retime --domain <domain> --dryRun` on stderr from `rebuild_projection`, `vault sync` and `ingest sync|async`. Test it with a Recorder returning a state without `identity_versions`; assert the message text and that the command still proceeds.

- [ ] **Step 8: Commit.**

```bash
git add artmind/rekey.py artmind/retime.py artmind/projection.py artmind/cli_conventions.py artmind/ingest.py artmind/vault_sync.py artmind/cli.py test/
git commit -m "feat(retime): re-key occasions and migrate same-as and curation records in one run"
```

**Beyond the suite** (dry run first; the real run only with the user's yes):

1. `cd ~/artmind_vaults/brain01 && git status` must be clean (the migration rewrites committed files; git is the undo).
2. Inside the vault: `uv run --project $WT artmind init --force --schema personal_journal` (refused unless git can restore it). Then hand-apply the vault's own edits the shipped schema lacks (SBP rollout step 2: `location` on EVENT/ACTIVITY).
3. `ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest retime --domain personal_journal --dryRun --compact | python3 -c "import json,sys; r=json.load(sys.stdin); print([(c['kind'], c['count']) for c in r['effect']['changes']])"` — counts only. Expect `entity_rekey` in the low thousands (ACTIVITY ~265 multi-day keys over ~1,089 days, EMOTION unchanged keys, PLAN ~33, EVENT ~72 folded by the dash-tail rule), `same_as_groups` near the 36 ACTIVITY + 60 EVENT + 9 PLAN members' groups, and `curation_records` including the 42 EVENT and 4 ACTIVITY mart dimension records.
4. With the user's yes: the same command without `--dryRun`. Then the acceptance queries (Task 30, "Happenings").

---

### Task 21: `query happenings` and the skill route

**Files:**
- Modify: `artmind/graph_query.py` — `happenings(domains, match, *, entity_class=None, from_=None, to=None, by=None, with_id=None, limit=200)`.
- Modify: `artmind/cli.py` — `@query.command("happenings")` (proxied automatically as a `query` command; `--shape brief` via `shape_option`); add `"happenings"` to the Query group list in `COMMAND_GROUPS` (line ~447).
- Modify: `artmind/cli_conventions.py` — `LIST_KEY["query happenings"] = "rows"` so `ARTMIND_OUTPUT_BUDGET` cuts it.
- Modify: `artmind/traces/path.py` — `"query happenings": "graph"`.
- Modify: `artmind/brief.py` — `happening_row(row)` (id, name, class, date, precision, type, participants ≤ 5 names, places ≤ 3, chunk_ids ≤ 3).
- Modify: `artmind/hints.py` — `next` when `coverage.matching_documents` > the number of distinct documents in the rows: `artmind query survey "<match>" --domain d --by year --shape brief`.
- Modify: `artmind/skills/artmind-query/SKILL.md` — a route-table row (line ~215 table and the command table at ~336): "each time / every year / how many times / when did … happen → `artmind query happenings --domain d --match "<term>" [--by year] [--with <id>] --shape brief`; then `query chunks --shape brief --idList <chunk_ids>` for detail; `survey` only for what the happenings miss (`coverage` larger than the rows)".
- Test: `test/test_query_happenings.py` (new).

- [ ] **Step 1: Write the failing tests.**

```python
"""`query happenings` (spec 2026-11-15 §5.2). Recorder: assert on the Cypher and params sent."""
import json

from click.testing import CliRunner

from _neo4j_recorder import Recorder
from artmind import graph_query as gq
from artmind.cli import cli

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent"}, "ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
                           "PERSON": {"kind": "recurrent"}}}
ROWS = [{"id": "a1", "name": "Tennis", "entity_class": "ACTIVITY", "type": "exercise", "date": "2024-03-30",
         "precision": "day", "participants": ["Alex"], "places": ["Club"], "chunk_ids": ["d1_000"], "doc_id": "d1"},
        {"id": "a2", "name": "Tennis", "entity_class": "ACTIVITY", "type": "exercise", "date": "2025-04-02",
         "precision": "day", "participants": [], "places": [], "chunk_ids": ["d2_000"], "doc_id": "d2"}]


def _patch(monkeypatch, rows=ROWS, coverage=2):
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: SCHEMA)
    rec = Recorder(lambda c, p: rows if "entity_name_ft" in c else [{"matching_documents": coverage}])
    monkeypatch.setattr(gq, "neo4j_session", lambda: rec)
    return rec


def test_sends_occasion_classes_and_a_name_scoped_lucene_query(monkeypatch):
    rec = _patch(monkeypatch)
    gq.happenings(["pj"], "tennis", from_="2024-01-01", to="2025-12-31")
    (cypher, params), = rec.queries_containing("entity_name_ft")
    assert params["classes"] == ["ACTIVITY", "EVENT"]
    assert params["ft_query"] == "name:(tennis) OR aliases:(tennis)"
    assert (params["from_"], params["to"]) == ("2024-01-01", "2025-12-31")
    assert "SAME_AS" in cypher and "_valid_precision" in cypher


def test_with_limits_to_happenings_linked_to_an_entity(monkeypatch):
    rec = _patch(monkeypatch)
    gq.happenings(["pj"], "tennis", with_id="p1")
    (cypher, params), = rec.queries_containing("entity_name_ft")
    assert params["withId"] == "p1" and "RELATES_TO" in cypher


def test_by_year_groups_rows(monkeypatch):
    _patch(monkeypatch)
    out = gq.happenings(["pj"], "tennis", by="year")
    assert [(p["period"], p["count"]) for p in out["periods"]] == [("2024", 1), ("2025", 1)]
    assert out["rows_total"] == 2 and out["truncated"] is False


def test_coverage_beyond_the_rows_suggests_a_survey(monkeypatch):
    _patch(monkeypatch, coverage=9)
    out = gq.happenings(["pj"], "tennis", by="year")
    assert out["coverage"] == {"matching_documents": 9, "documents_in_rows": 2}
    assert out["next"].startswith("artmind query survey 'tennis' --domain pj --by year")


def test_cli_envelope_and_brief_shape(monkeypatch):
    _patch(monkeypatch)
    result = CliRunner().invoke(cli, ["query", "happenings", "--domain", "pj", "--match", "tennis",
                                      "--shape", "brief", "--compact"])
    payload = json.loads(result.stdout)
    assert payload["command"] == "query happenings" and payload["warnings"] == []
    assert set(payload["rows"][0]) == {"id", "name", "class", "date", "precision", "type", "participants", "places", "chunk_ids"}
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_query_happenings.py -v` — expected: AttributeError.

- [ ] **Step 3: Implement** `graph_query.happenings`. The Cypher:

```python
_HAPPENINGS = """
CALL db.index.fulltext.queryNodes('entity_name_ft', $ft_query) YIELD node AS hit
WHERE {scope_hit} AND hit.entity_class IN $classes
WITH collect(DISTINCT hit) AS hits
CALL (hits) {{
  UNWIND hits AS h RETURN h AS e
  UNION
  UNWIND hits AS h MATCH (h)-[:SAME_AS]-(e:Entity) WHERE e.entity_class IN $classes RETURN e
  UNION
  MATCH (e:Entity) WHERE {scope_e} AND e.entity_class IN $classes AND toLower(e.type) = toLower($term) RETURN e
}}
WITH DISTINCT e
WHERE ($from_ IS NULL OR e._valid_from >= $from_) AND ($to IS NULL OR e._valid_from <= $to)
  AND ($withId IS NULL OR EXISTS {{ MATCH (e)-[:RELATES_TO]-(:Entity {{_id: $withId}}) }})
OPTIONAL MATCH (e)-[:RELATES_TO]-(n:Entity)
WITH e, collect(DISTINCT CASE WHEN n.entity_class IN $people THEN n.name END)[..5] AS participants,
        collect(DISTINCT CASE WHEN n.entity_class IN $places THEN n.name END)[..3] AS places
OPTIONAL MATCH (e)-[:AGGREGATES]->(o:Observation)
WITH e, participants, places, collect(DISTINCT o.chunk_id)[..3] AS chunk_ids, head(collect(o.doc_id)) AS doc_id
RETURN e._id AS id, e.name AS name, e.entity_class AS entity_class, e.type AS type,
       e._valid_from AS date, coalesce(e._valid_precision, 'day') AS precision,
       participants, places, chunk_ids, doc_id
ORDER BY date, name
LIMIT $limit
"""
```

`scope_hit` / `scope_e` are `domain_predicate("hit")` / `domain_predicate("e")`. `$people` / `$places` are the domain schema's `name`-identity classes whose names are PERSON-like / LOCATION-like: read them from `domain_config` if it names them, else `["PERSON"]` / `["LOCATION", "PLACE"]` filtered to classes the schema declares. The `ft_query` is built with the existing `sanitize_lucene_query` on the term, wrapped as `name:(…) OR aliases:(…)`. `coverage` runs the `topics/grid._MATCHING` count (reuse it, with the window) on `passage_text_ft`, and `documents_in_rows` is the distinct `doc_id` count. `periods` groups rows by `date[:4]` or `date[:7]`. Rows with precision `none` are counted in a `undated` field and sorted last; they never fall inside a `--from/--to` window.

The CLI command:

```python
@query.command("happenings")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--match", "match", required=True, help="What happened, in the corpus's words (name, alias or type)")
@click.option("--entityClass", "entity_class", default=None, help="Only this occasion class")
@click.option("--from", "from_", default=None, help="On or after this date (YYYY[-MM[-DD]])")
@click.option("--to", "to", default=None, help="On or before this date (YYYY[-MM[-DD]]; a year or month means its last day)")
@click.option("--by", type=click.Choice(["year", "month"]), default=None, help="Group rows by period")
@click.option("--with", "with_id", default=None, help="Only happenings linked to this entity id")
@click.option("--limit", type=click.IntRange(1, 1000), default=200, show_default=True)
@shape_option
@compact_option
def query_happenings(domain, match, entity_class, from_, to, by, with_id, limit, shape, compact):
    """Every dated happening (an `identity: occasion` class) matching a term: one row per
    occasion with its date, class, type, participants, places and chunk ids.

    Answers "every year", "how many times", "when did X happen". `coverage` counts documents
    whose text matches; when it exceeds the documents in the rows, `next` names a survey.
    """
```

It calls `resolve_as_of` on `--to` (widening) and `--from` (no widening: `2024` → `2024-01-01`, use `parse_iso`).

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_query_happenings.py test/test_cli_conventions.py test/test_cli_guide.py test/test_query_skill_structural_schema.py -v && just dev-test` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/graph_query.py artmind/cli.py artmind/cli_conventions.py artmind/traces/path.py artmind/brief.py artmind/hints.py artmind/skills/artmind-query test/test_query_happenings.py
git commit -m "feat(query): happenings lists dated occasions per period, with coverage"
```

**Beyond the suite** (read-only; after Task 20's real run): in brain01, for each of the four recurring-happening questions in `<vault>/benchmarking/` (classic suite), run `ARTMIND_NO_PROXY=1 uv run --project $WT artmind query happenings --domain personal_journal --match "<term>" --by year --compact | python3 -c "import json,sys; r=json.load(sys.stdin); print([(p['period'], p['count']) for p in r['periods']], r['coverage'])"` and compare per-year counts with the dated days the expected answers name (acceptance §10). Print periods and counts only. In banking_corpus (public), the same for "transaction" prints full rows.

---
## Part 6 — Record time and `--knownAsOf` (spec §6.1, §6.2; rollout step 6)

### Task 22: Stamp `_recorded_at` at commit

**Files:**
- Modify: `artmind/ingest.py` — `_commit_document_tx` computes `recorded_at = datetime.now(timezone.utc).isoformat()` once and passes it down; `_merge_relabeled` (lines ~4553–4600) gains `recorded_at=None`: on CREATE sets `n._recorded_at = $recorded_at`; on revive of a History node keeps `_recorded_at` and sets `n._revived_at = $recorded_at`, removing `_retired_at`/`_retired_by`; on a plain re-write of a current node keeps `_recorded_at`. Because `_write_observations` uses `replace=True` (`SET n = $props`), the observation branch must carry the old `_recorded_at` across: `SET n = $props SET n._recorded_at = coalesce(old_recorded, $recorded_at)` with `old_recorded` read in the same statement.
- Modify: `artmind/passage_store.py` `MERGE_PASSAGES`, `artmind/summary_store.py` merge — same rule (`$recorded_at` param).
- Modify: `artmind/ingest.py` `_write_relation_observations` — `ASSERTS_RELATION` gets `_recorded_at` ON CREATE.
- Modify: `artmind/projection.py` `_merge_entities_batch` — `ON CREATE SET e._recorded_at = $recorded_at` (rebuild passes `datetime.now(timezone.utc)`); `_OBSERVATION_SYSTEM_KEYS` += `_recorded_at`, `_revived_at`, `_retired_at`, `_retired_by`, `_record_time_approx`; the `keep` list for an Entity keeps `_recorded_at`.
- Test: `test/test_record_time_commit.py` (new).

- [ ] **Step 1: Write the failing tests.** Drive `_commit_document_tx` over a `Recorder` as `test_catalogue_bump_sites.py` does, with one chunk, one observation and one relationship staged:

```python
"""`_recorded_at` on commit (spec 2026-11-15 §6.1, question 17)."""
import json

from _neo4j_recorder import Recorder

from artmind import ingest, passage_store, projection


def _stage(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "d1", "name": "a.md", "_valid_from": "2026-01-07"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "d1_000", "doc_id": "d1", "text": "x", "_domain": "pj"}]))
    (folder / "observations.json").write_text(json.dumps([{"id": "o1", "key": "a|PERSON|pj", "chunk_id": "d1_000",
                                                            "doc_id": "d1", "name": "A", "_domain": "pj"}]))
    (folder / "relationships.json").write_text("[]")
    return folder


def test_one_timestamp_reaches_every_write(tmp_path, monkeypatch):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    tx = Recorder(lambda c, p: [])
    ingest._commit_document_tx(tx, ingest._load_staged(_stage(tmp_path), "pj"), True)
    stamped = {p["recorded_at"] for c, p in tx.calls if "recorded_at" in p}
    assert len(stamped) == 1 and next(iter(stamped)).endswith("+00:00")
    merges = [c for c, p in tx.calls if "recorded_at" in p]
    for label in ("Document", "DocChunk", "Observation"):
        assert any(f"CREATE (n:{label}" in c for c in merges), label
    assert any("DocPassage" in c for c in merges)


def test_revive_keeps_the_first_recorded_at_and_sets_revived_at():
    tx = Recorder()
    ingest._merge_relabeled(tx, "Observation", "ObservationHistory", "o1", {"id": "o1"}, replace=True,
                            recorded_at="2026-11-16T00:00:00+00:00")
    cypher, params = tx.calls[0]
    assert "coalesce(existing._recorded_at" in cypher or "old_recorded" in cypher
    assert "_revived_at" in cypher and "REMOVE n._retired_at, n._retired_by" in cypher
```

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_record_time_commit.py -v` — expected: FAIL.

- [ ] **Step 3: Implement.** `_merge_relabeled`:

```python
    stamp = ""
    if recorded_at is not None:
        stamp = "SET n._recorded_at = coalesce(old_recorded, $recorded_at)"
    tx.run(
        f"""
        OPTIONAL MATCH (o1:{base_label} {{id: $id}})
        OPTIONAL MATCH (o2:{history_label} {{id: $id}})
        WITH coalesce(o1, o2) AS existing, o2 IS NOT NULL AS revived
        WITH existing, revived, existing._recorded_at AS old_recorded
        FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
            CREATE (n:{base_label} {{id: $id}})
            {set_clause}
            {stamp.replace("coalesce(old_recorded, $recorded_at)", "$recorded_at")}
        )
        FOREACH (n IN CASE WHEN existing IS NOT NULL THEN [existing] ELSE [] END |
            {set_clause}
            {stamp}
            SET n:{base_label}
            REMOVE n:{history_label}
        )
        FOREACH (n IN CASE WHEN revived AND $recorded_at IS NOT NULL THEN [existing] ELSE [] END |
            SET n._revived_at = $recorded_at
            REMOVE n._retired_at, n._retired_by
        )
        """,
        id=id_value, props=props, recorded_at=recorded_at,
    )
```

(`old_recorded` is read before `SET n = $props` wipes it.) Thread `recorded_at` through `_write_observations`, the document and chunk `_merge_relabeled` calls, `passage_store.write_chunk_passages(tx, chunk_id, rows, recorded_at=None)` (`MERGE_PASSAGES` sets `n._recorded_at` on CREATE and keeps it on the revive branch), `summary_store.write_document_summaries(..., recorded_at=None)`, and `_write_relation_observations` (`ON CREATE SET r._recorded_at = $recorded_at`).

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_record_time_commit.py test/test_catalogue_bump_sites.py $(rg -l "_merge_relabeled|write_chunk_passages|write_document_summaries" test) -v && just dev-test` — expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add artmind/ingest.py artmind/passage_store.py artmind/summary_store.py artmind/projection.py test/test_record_time_commit.py
git commit -m "feat(record-time): stamp _recorded_at once per commit; a revive keeps the first"
```

---

### Task 23: Every relabel to History sets `_retired_at` and `_retired_by`

**Files:**
- Modify: `artmind/ingest.py` `_retract_prior_version` (lines ~3206–3320) — every `REMOVE x:Y SET x:YHistory` gains `SET x._retired_at = $now, x._retired_by = $by` with `by = f"version:{new_version}"`; `_commit_document_tx` passes the staged document's `version`.
- Modify: `artmind/passage_store.py` `RETIRE_CHUNK_PASSAGES`, `artmind/summary_store.py` `RETIRE` — same, `by` from the caller.
- Modify: `artmind/lifecycle.py` `_transition` (lines ~31–107) — to History: `SET n._retired_at = $now, n._retired_by = $by` on document, chunks, passages, summaries, observations; `by` = `retire` from `retire_document`, `supersede:<record id>` from `apply_supersession`; to current (restore): `REMOVE n._retired_at, n._retired_by` and `SET n._revived_at = $now`.
- Modify: `artmind/projection.py` `apply_retractions` (line ~1225) — `SET o._retired_at = $now, o._retired_by = 'retract:' + $retractor` (question 8).
- Modify: `artmind/temporal.py` `apply_supersession` — passes `by=f"supersede:{record['id']}"` to `retire_document` (new kwarg).
- Modify: `artmind/lifecycle_records.py` and `artmind/supersession_records.py` `record_for` — records gain `recorded_at` (UTC); supersession records gain `effective` from the newer document's `_valid_from` when the caller passes none (§6.1).
- Test: `test/test_record_time_retire.py` (new); append to `test/test_lifecycle.py`, `test/test_supersession_records.py`.

- [ ] **Step 1: Write the failing tests.**

```python
"""`_retired_at` / `_retired_by` on every relabel to History (spec 2026-11-15 §6.1)."""
from _neo4j_recorder import Recorder

from artmind import ingest, lifecycle, projection


def _history_writes(rec):
    return [(c, p) for c, p in rec.calls if "History" in c and "SET" in c]


def test_version_supersession_stamps_every_label():
    rec = Recorder(lambda c, p: [{"n": 1}])
    ingest._retract_prior_version(rec, "pj", "d1", retired_by="version:3")
    writes = _history_writes(rec)
    assert len(writes) >= 4
    for cypher, params in writes:
        assert "_retired_at = $now" in cypher and params["by"] == "version:3"
        assert params["now"].endswith("+00:00")


def test_retire_and_restore(monkeypatch):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "d1", to_history=True, rebuild=False, retired_by="retire")
    assert all(p["by"] == "retire" for c, p in _history_writes(rec))
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "d1", to_history=False, rebuild=False)
    assert any("REMOVE" in c and "_retired_at" in c for c, p in rec.calls)


def test_a_retraction_names_its_retractor():
    rec = Recorder(lambda c, p: [{"key": "a|PERSON|pj"}])
    projection.apply_retractions(rec, [{"id": "o9", "_retracts": "o1"}])
    (cypher, params), = rec.queries_containing("SET o:ObservationHistory")
    assert "_retired_by = 'retract:' + $retractor" in cypher and params["retractor"] == "o9"
```

Append record tests: `supersession_records.record_for(...)` returns `recorded_at` (ends `+00:00`) and, with `effective=None` and a newer document dated `2026-02-01` in the Recorder's response, `effective == "2026-02-01"`.

- [ ] **Step 2: Run and confirm failure.** `uv run --group dev pytest test/test_record_time_retire.py test/test_lifecycle.py test/test_supersession_records.py -v` — expected: FAIL.

- [ ] **Step 3: Implement** as listed. Keep each Cypher a single statement per label (the existing shape) with the two extra `SET`s. `_transition`'s new kwarg is `retired_by: str = "retire"`; `retire_document(doc_id, domain=None, *, dry_run=False, retired_by="retire")`.

- [ ] **Step 4: Run and confirm.** `uv run --group dev pytest test/test_record_time_retire.py test/test_lifecycle.py test/test_lifecycle_records.py test/test_supersession_records.py test/test_supersession.py test/test_catalogue_bump_sites.py $(rg -l "test_dry_run_docs|retire" test | head -10) -v && just dev-test` — expected: PASS. The tier-1 dry runs of `docs retire` / `ingest unsupersede` still send no write Cypher.

- [ ] **Step 5: Commit.**

```bash
git add artmind/ingest.py artmind/passage_store.py artmind/summary_store.py artmind/lifecycle.py artmind/projection.py artmind/temporal.py artmind/lifecycle_records.py artmind/supersession_records.py test/
git commit -m "feat(record-time): every move to History records when and why"
```

---

### Task 24: `--knownAsOf` on `entity-history`, `document-history` and `pattern10`; `--asOf` honours `_valid_to`

**Files:**
- Modify: `artmind/graph_query.py` — `known_predicate(var, param="knownAsOf")`; `resolve_known_as_of(value)` (date → end of that UTC day, datetime → as given, `now`); `entity_history(..., known_as_of=None)`: when set, filters both labels with `known_predicate`, and each row's `status` becomes `current`/`history` **as of T** (`_retired_at` IS NULL or > T → current); `entity_history --asOf` uses `valid_predicate("o")` instead of the floor (line ~2333); `document_history(..., known_as_of=None)`; `pattern10` (`parameters["knownAsOf"]`) filters chunks with `known_predicate` over both labels.
- Modify: `artmind/cli.py` — `--knownAsOf` options on `query entity-history` (line ~4100), `query document-history` (~4139), `query graph pattern10` (~3531); `entity-history --asOf` help: "a point in valid time: facts whose own date window contains this date".
- Modify: `artmind/skills/artmind-query/references/temporal.md` and `SKILL.md` line ~341 — `--asOf` is a point; `--knownAsOf` reads what the graph held at T; nodes recorded before the backfill carry approximate record time.
- Test: `test/test_known_as_of.py` (new).

- [ ] **Step 1: Write the failing tests.**

```python
"""`--knownAsOf` reads record time (spec 2026-11-15 §6.2, question 9)."""
from _neo4j_recorder import Recorder

from artmind import graph_query as gq


def test_known_predicate_text():
    text = gq.known_predicate("o")
    assert "o._recorded_at IS NULL OR o._recorded_at <= $knownAsOf" in text
    assert "o._retired_at IS NULL OR o._retired_at > $knownAsOf" in text


def test_a_date_means_the_end_of_that_utc_day():
    assert gq.resolve_known_as_of("2026-03-01") == "2026-03-01T23:59:59.999999+00:00"


def test_entity_history_sends_both_labels_and_the_param(monkeypatch):
    sent = []
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: sent.append((cypher, params)) or [])
    gq.entity_history(["pj"], key="a|PERSON|pj", known_as_of="2026-03-01")
    cypher, params = sent[0]
    assert params["knownAsOf"] == "2026-03-01T23:59:59.999999+00:00"
    assert "o:Observation OR o:ObservationHistory" in cypher and "_retired_at > $knownAsOf" in cypher


def test_entity_history_as_of_is_a_point(monkeypatch):
    sent = []
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: sent.append((cypher, params)) or [])
    gq.entity_history(["pj"], key="a|PERSON|pj", as_of="2026-03-01")
    assert "o._valid_to IS NULL OR o._valid_to > $asOf" in sent[0][0]


def test_a_warning_names_approximate_record_time(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [{"observation": {"key": "k", "_record_time_approx": True},
                                                              "status": "current"}])
    out = gq.entity_history(["pj"], key="k", known_as_of="2026-03-01")
    assert any("approximate" in w for w in out["warnings"])
```

Status-as-of-T is computed in Cypher: `CASE WHEN o._retired_at IS NULL OR o._retired_at > $knownAsOf THEN 'current' ELSE 'history' END` when `knownAsOf` is set.

- [ ] **Step 2: Run, implement, run.** `uv run --group dev pytest test/test_known_as_of.py $(rg -l "entity_history|document_history|pattern10" test) -v` — expected: FAIL, then PASS. Run `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py && just obsidian-plugin-test` if any fixture covers these commands.

- [ ] **Step 3: Commit.**

```bash
git add artmind/graph_query.py artmind/cli.py artmind/skills/artmind-query test/test_known_as_of.py
git commit -m "feat(query): --knownAsOf reads record time; entity-history --asOf is a point in valid time"
```

**Beyond the suite** (synthetic vault, acceptance §10 "Record time"): `just` has no recipe for a throwaway vault; use `artmind vault new` per `docs/INSTALL.md` into `/tmp/tm-vault` with a scratch Neo4j database the user names. Write a note, ingest it (`env -u ANTHROPIC_BASE_URL … ingest sync`), note the time T, edit one fact, ingest again, then `ARTMIND_NO_PROXY=1 uv run --project $WT artmind query entity-history --domain d --name "<entity>" --knownAsOf <T minus 1 second>` returns the first version's observation and `--knownAsOf now` the second's.

---

### Task 25: Record-time backfill migration

**Files:**
- Create: `artmind/migrations/record_time_backfill.py`; call it from `setup.setup_all` after `document_valid_time_names`.
- Test: `test/test_migration_record_time_backfill.py` (new).

- [ ] **Step 1: Write the failing test.**

```python
from _neo4j_recorder import Recorder

from artmind.migrations import record_time_backfill as m


def test_current_nodes_get_recorded_at_history_nodes_get_retired_at():
    rec = Recorder(lambda c, p: [{"n": 2}])
    out = m.run(rec)
    current = rec.queries_containing("SET n._recorded_at = d.ingested_at")
    history = rec.queries_containing("SET n._retired_at = d.ingested_at")
    assert current and history
    assert "_record_time_approx = true" in current[0][0] and "n._recorded_at IS NULL" in current[0][0]
    assert "n._retired_at IS NULL" in history[0][0]
    assert set(out) == {"recorded", "retired"}
```

- [ ] **Step 2: Implement.** One statement per label pair, batched with `CALL { … } IN TRANSACTIONS OF 5000 ROWS` (brain01 has ~19k observations). Current labels (`Observation`, `DocChunk`, `DocPassage`, `DocSummary`) join their `Document` by `doc_id`; history labels join `Document` or `DocumentHistory`. Nodes whose document has no `ingested_at` are left alone and counted as `unknown`. `catalogue.state.bump` is not needed (no catalogue part reads record time); `test_catalogue_bump_sites.py` confirms.

- [ ] **Step 3: Run, commit.**

```bash
uv run --group dev pytest test/test_migration_record_time_backfill.py test/test_catalogue_bump_sites.py -v
git add artmind/migrations/record_time_backfill.py artmind/setup.py test/test_migration_record_time_backfill.py
git commit -m "feat(migrations): approximate record time from Document.ingested_at"
```

**Beyond the suite:** inside banking_corpus first, with the user's yes: `uv run --project $WT artmind setup` reports `record_time_backfill.recorded` ≈ the vault's observation + chunk + passage + summary count; a second run reports 0.

---

## Part 7 — Ends of validity and the projection winner (spec §6.2, §7; rollout step 7)

### Task 26: Superseding closes validity; retiring does not; unsupersede restores

**Files:**
- Modify: `artmind/supersession_records.py` — `apply` (lines ~119–138): after `_RECOMPUTE`, for `scope == "document"`, `_CLOSE_VALIDITY` sets `_valid_to = E` where it is null on the older document's observations (both labels), chunks, passages and summaries, and records which nodes it closed: `SET x._valid_to_by = $record_id`. `E` = `record.effective` else the newer document's `_valid_from`. `remove` runs `_REOPEN_VALIDITY`: `REMOVE x._valid_to, x._valid_to_by` where `x._valid_to_by = $record_id`.
- Modify: `artmind/temporal.py` `supersession_effect` / `unsupersede_effect` — the `effect` gains `{kind: "validity_closed", count}` from a count query (tier-1 dry runs stay read-only).
- Modify: `artmind/cli_conventions.py` `EFFECT_KINDS` += `validity_closed`.
- Modify: `artmind/lifecycle.py` — nothing (retire leaves `_valid_to`), asserted by a test.
- Test: `test/test_validity_close.py` (new); append to `test/test_dry_run_ingest_unsupersede.py` and the supersede dry-run test (`rg -l "dry_run.*supersede" test`).

- [ ] **Step 1: Write the failing tests.**

```python
"""Ends of validity (spec 2026-11-15 §7.2). Recorder-based."""
from _neo4j_recorder import Recorder

from artmind import lifecycle, supersession_records as sr


def _record(effective=None):
    return {"id": "r1", "newer_doc_id": "B", "older_doc_id": "A", "scope": "document", "effective": effective}


def test_apply_closes_open_facts_of_the_older_document_with_the_effective_date():
    rec = Recorder(lambda c, p: [{"n": 1}])
    sr.apply(rec, _record("2026-02-01"), "fp")
    (cypher, params), = rec.queries_containing("_valid_to_by")
    assert params == {"older": "A", "newer": "B", "effective": "2026-02-01", "record_id": "r1"}
    assert "x._valid_to IS NULL" in cypher
    for label in ("Observation", "ObservationHistory", "DocChunk", "DocChunkHistory", "DocPassage", "DocSummary"):
        assert label in cypher


def test_without_an_effective_date_the_newer_documents_date_is_used():
    rec = Recorder(lambda c, p: [{"n": 1}])
    sr.apply(rec, _record(None), "fp")
    (cypher, params), = rec.queries_containing("_valid_to_by")
    assert params["effective"] is None and "coalesce($effective, newer._valid_from)" in cypher


def test_remove_reopens_only_what_this_record_closed():
    rec = Recorder(lambda c, p: [])
    sr.remove(rec, _record("2026-02-01"))
    (cypher, params), = rec.queries_containing("REMOVE x._valid_to")
    assert "x._valid_to_by = $record_id" in cypher and params["record_id"] == "r1"


def test_retire_leaves_valid_to_alone(monkeypatch):
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "A", to_history=True, rebuild=False)
    assert not [c for c, p in rec.calls if "_valid_to" in c]
```

- [ ] **Step 2: Run, implement, run.** `_CLOSE_VALIDITY`:

```python
_CLOSE_VALIDITY = """
MATCH (newer {id: $newer}) WHERE newer:Document OR newer:DocumentHistory
WITH coalesce($effective, newer._valid_from) AS e
WHERE e IS NOT NULL
MATCH (x) WHERE x.doc_id = $older AND x._valid_to IS NULL
  AND (x:Observation OR x:ObservationHistory OR x:DocChunk OR x:DocChunkHistory
       OR x:DocPassage OR x:DocPassageHistory OR x:DocSummary OR x:DocSummaryHistory)
SET x._valid_to = e, x._valid_to_by = $record_id
RETURN count(x) AS n
"""
```

The unlabelled `MATCH (x)` must be index-backed: split it into one statement per label (each label has an index on `doc_id`), run in a loop; the test above then checks the label list across the statements (`rec.queries_containing("_valid_to_by")` returns several — adjust to `any`). The observation rows change, so the rebuild must run: `apply` returns the older document's keys (`projection.keys_for_document`) instead of `set()`, and its callers (vault sync, `apply_supersession`) already rebuild returned keys; `retire_document` follows in `apply_supersession` and rebuilds too. Add `_valid_to_by` to `_OBSERVATION_SYSTEM_KEYS`. Run `uv run --group dev pytest test/test_validity_close.py test/test_supersession_records.py test/test_supersession.py $(rg -l "unsupersede|supersede" test) test/test_catalogue_bump_sites.py -v && just dev-test`.

- [ ] **Step 3: Commit.**

```bash
git add artmind/supersession_records.py artmind/temporal.py artmind/cli_conventions.py artmind/projection.py test/
git commit -m "feat(temporal): superseding a document ends its facts' validity; unsupersede reopens them"
```

**Beyond the suite** (banking_corpus, dry run): `ARTMIND_NO_PROXY=1 uv run --project $WT artmind ingest detect-supersession --domain banking --dryRun --compact` shows `validity_closed` counts; with the user's yes, the real run, then `query vector-text --asOf <a date after E>` no longer returns the superseded document's passages.

---

### Task 27: `table2graph` carries SCD2 `_valid_to`

**Files:**
- Modify: `artmind/table2graph.py` — `_Row` gains `valid_to: str | None`; `build_staged` reads `values.get("_valid_to")` for temporal tables (line ~951) and passes `valid_to=fact.valid_to or built.row.valid_to` to `build_observation` (line ~1109).
- Test: `test/test_table2graph.py` (append).

- [ ] **Step 1: Write the failing test** following the file's existing temporal-table fixture (`rg -n "refresh_mode.*temporal" test/test_table2graph.py`): two versions of one row, the first closed `_valid_to = 2026-02-01`; assert the first observation's `_valid_to == "2026-02-01"` and the second's is absent.

- [ ] **Step 2: Implement, run, commit.**

```bash
uv run --group dev pytest test/test_table2graph.py test/test_dry_run_ingest_table2graph.py -v
git add artmind/table2graph.py test/test_table2graph.py
git commit -m "feat(table2graph): a closed SCD2 row version ends in the graph too"
```

---

### Task 28: The projection winner is the latest fact, then the latest document

**Files:**
- Modify: `artmind/projection.py` — `_winner_sort_key(observation, kind="occurrent")` (lines ~63–79): for `recurrent`, `(fact_date, _doc_valid_from, doc_version, id)` where `fact_date` is `_valid_from` unless `_valid_precision == "none"` (then `""`); for `occurrent`, unchanged. `merge_observations` passes the class's `kind` (`winner`-independent: read `_kind` from the first observation) to the sort; `_winner(obs_at_instant)` likewise.
- Test: `test/test_projection_winner.py` (new).

- [ ] **Step 1: Write the failing tests.**

```python
"""Winner selection (spec 2026-11-15 §6.2, §1.5; question 10). Synthetic observations."""
from artmind.projection import merge_observations


def _o(i, value, valid_from, doc_valid_from, precision="day", kind="recurrent"):
    return {"id": f"o{i}", "key": "rate|RATE|b", "canonical_name": "Rate", "entity_class": "RATE", "_domain": "b",
            "_kind": kind, "rate": value, "_valid_from": valid_from, "_doc_valid_from": doc_valid_from,
            "_valid_precision": precision, "doc_version": 1}


def test_a_later_fact_in_an_older_document_wins():
    merged = merge_observations([_o(1, "4.5", "2026-03-01", "2026-01-10"), _o(2, "4.7", "2026-02-01", "2026-02-15")])
    assert merged["props"]["rate"] == "4.5" and merged["props"]["_valid_from"] == "2026-03-01"


def test_a_default_date_never_beats_a_real_one():
    merged = merge_observations([_o(1, "4.5", "2026-11-15", "2026-11-15", precision="none"),
                                 _o(2, "4.7", "2026-02-01", "2026-02-01")])
    assert merged["props"]["rate"] == "4.7"


def test_occurrent_classes_keep_the_document_order():
    merged = merge_observations([_o(1, "a", "2026-03-01", "2026-01-10", kind="occurrent"),
                                 _o(2, "b", "2026-02-01", "2026-02-15", kind="occurrent")])
    assert merged["props"]["rate"] == "b"
```

(Use `merge_observations`'s real return shape — `rg -n "def merge_observations" -A50 artmind/projection.py`; adjust `merged["props"]`.)

- [ ] **Step 2: Run, implement, run.** `uv run --group dev pytest test/test_projection_winner.py test/test_projection_merge.py test/test_projection_live.py -v && just dev-test` — expected: PASS; any existing test asserting a winner by document date for a recurrent class with a later fact date is the defect this fixes and is updated.

- [ ] **Step 3: Commit.**

```bash
git add artmind/projection.py test/test_projection_winner.py
git commit -m "fix(projection): a recurrent class's winner is the latest fact, then the latest document"
```

**Beyond the suite** (banking_corpus, read-only count first): before merging, count entities whose winner would change (`observations where _valid_from > _doc_valid_from`, grouped by key) and check it is under 2% of keys (spec §6.2); after a `projection rebuild` (user's yes), `query graph conflicts --domain banking` shows no new conflicts.

---

## Part 8 — Docs and acceptance

### Task 29: Skills, justfile, CLAUDE.md

**Files:**
- Modify: `artmind/skills/artmind-query/SKILL.md` and `references/temporal.md` — the two time axes; `_valid_precision` (`none` = undated); `--asOf` a point on every command that keeps it; `--knownAsOf`; `query happenings` (Task 21 added the route row; here the reference text and an example with `--by year` and `--with`).
- Modify: `artmind/skills/artmind-create-schema/SKILL.md` — `identity: name|occasion|category` with when to use each; `temporal: valid_from|valid_to|due|review`; document `revised` / `valid_to` labels; `types:` closed lists; `retime_reclassify`; occasion naming (no dates in names).
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` — `ingest date-audit`; `retime` now re-keys occasions and migrates same-as/curation records (dry run first, git-clean vault); the pre-migration warning.
- Modify: `artmind/skills/artmind-curate/SKILL.md` — same-as proposals never pair occasions on different days or category entities; closed proposals carry `identity:<value>`.
- Modify: `justfile` — next to `ingest-retime`:

```make
# How each class of a domain is dated: share by property, precision, ranges, unparsed (read-only)  (usage: just ingest-date-audit <domain>)
ingest-date-audit domain:
    uv run artmind ingest date-audit --domain {{ domain }}

# Every dated happening matching a term, by year  (usage: just query-happenings <domain> "<term>" [--with <id>])
query-happenings domain term *args:
    uv run artmind query happenings --domain {{ domain }} --match "{{ term }}" --by year {{ args }}
```

- Modify: `CLAUDE.md` — rows for `artmind/date_audit.py`, `artmind/rekey.py`, the two migrations; the `retime.py` row (re-key phase, reclassify, chunk dates); the `temporal.py` row (`parse_valid`); the `observations.py` behaviour (occasion keys `name~YYYY-MM-DD`); command notes for `query happenings`, `--knownAsOf`, `ingest date-audit`; `graph timeline` lists occasion classes.

- [ ] **Step 1:** Make the edits.
- [ ] **Step 2:** `uv run --group dev pytest test/test_cli_conventions.py test/test_cli_guide.py test/test_query_skill_structural_schema.py test/test_temporal_skill_text.py -v` — expected: PASS. Update `test_temporal_skill_text.py`'s first test to assert "a point" now that `--asOf` honours `_valid_to`.
- [ ] **Step 3:** `just dev-cli-help | grep -E "happenings|date-audit|knownAsOf"` — all appear.
- [ ] **Step 4: Commit.**

```bash
git add artmind/skills justfile CLAUDE.md test/test_temporal_skill_text.py
git commit -m "docs: temporal model in skills, justfile and CLAUDE.md"
```

The `~/.artmind` run-folder skill copies need `cd ~ && artmind setup` after merge, run by the user. Vault agents get the edits through their symlinks.

---

### Task 30: Acceptance runs (spec §10)

Ask the user before each run that writes a vault or calls the LLM. brain01 results are counts and grades only.

- [ ] **Parsing.** `ingest date-audit --domain personal_journal`: every `unparsed` value is counted; ranges ≥ 13, each with `_valid_to` (check with a count over staging).
- [ ] **Retrieval filters.** brain01 and banking: `vector-text` and `chunks` with `--asOf <a date inside the corpus>` return fewer rows than without; `--asOf 2026` includes 2026 dates.
- [ ] **Happenings (brain01, counts only).**
  - No `occasion` entity spans observations on two days: `MATCH (e:Entity)-[:AGGREGATES]->(o:Observation) WHERE e.entity_class IN ['EVENT','ACTIVITY','STATE_CHANGE','PLAN'] WITH e, count(DISTINCT left(o._valid_from, 10)) AS d WHERE d > 1 RETURN count(e)` returns 0. Run it with a short `uv run --project $WT python` script that opens `graph_query.neo4j_session()` inside the vault (it reads the vault's own Neo4j config) and prints the count.
  - No open same-as proposal pairs two occasions on different days or two category entities (`sameas list --compact`, checked with `sameas.identity_refusal` over the rows).
  - Under 5% of occasion entity names contain a date.
  - `query happenings --match … --by year` for the four recurring-happening questions returns, per year, at least the dated days the expected answers name.
- [ ] **Answers on happenings.** `env -u ANTHROPIC_BASE_URL` benchmark runs of the brain01 classic suite with the new skill, 3 runs; the four recurring-happening questions: at least 2 of 4 correct in 2 of 3 runs, against the current-skill runs (`traces compare --baseline A,D --grade --allowJudge`).
- [ ] **Record time.** Task 24's synthetic-vault check.
- [ ] **Answers.** Classic suites on both vaults, 3 runs per side, no drop in grade bands; brain01 "when did X" questions hold their correct rate.
- [ ] **Conflicts (question 11).** Open projection conflicts on brain01 before and after the identity migration, by class.

Record results in the PR body; brain01 as aggregates only.

---

## Per-vault rollout (after merge; the operator runs it)

Order follows spec §9, interleaved with the SBP rollout. Each step: dry run, then the real run with a git-clean vault.

1. **After Parts 1–2 merge:** inside the vault, `artmind ingest retime --domain d --dryRun`, then `artmind ingest retime --domain d` (dates: ranges, precision, time of day). If SBP Task 6 has merged, the same run also drops undeclared keys (SBP rollout step 4) — read both `effect` blocks before saying yes.
2. **After Part 3:** `artmind init --force [--schema NAME]` for shipped schemas (banking deadlines, Last Updated → revised); `artmind domains validate`; `ingest retime` again. For banking TRANSACTION/SAR, `ingest backfill-properties --entityClass TRANSACTION --property transaction_date --dryRun` (SBP Task 10), then without `--dryRun` (LLM: `env -u ANTHROPIC_BASE_URL`).
3. **After Part 4:** `artmind setup` inside the vault (runs the §7.1 fold); `ingest retime` (chunk dates) — its commits re-write passages and summaries with dates; `ingest embed-passages` only if the sweep reports stale passages.
4. **After Part 5:** `artmind init --force --schema personal_journal` (journal vaults), re-apply the vault's own schema edits, `domains validate`, then `ingest retime --domain personal_journal --dryRun` and read `entity_rekey`, `same_as_groups`, `sameas_proposals_closed`, `curation_records`; then the real run. Commit the vault (staging, `same_as.yaml`, curation records) through Obsidian Git. Then `insights refresh` and `mart build` for the domain.
5. **After Part 6:** `artmind setup` inside the vault (record-time backfill).
6. **After Part 7:** `projection rebuild --domain d` (winner change; superseded documents' validity closes when `vault sync` or `detect-supersession` next applies their records — run `ingest detect-supersession --domain d --dryRun` to see counts).
7. Restart `serve`, `chat-ui` and `admin-ui` from inside the vault after each step.
