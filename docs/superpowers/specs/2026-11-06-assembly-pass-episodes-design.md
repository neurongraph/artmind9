# Assembly pass: episodes and other cross-observation structure

Date: 2026-11-06. Status: **design draft for review**. Not yet planned or built.
Parent: `2026-11-02-retrieval-curation-architecture-design.md` (§5.10, phase PA in §9).
Related: `docs/projection-pipeline.md` (observations, rebuild, synthesize),
`2026-11-05-planner-v2-design.md` (routes, programs, plan facts), `docs/question-routing.md`.

## 0. In one page

First-pass extraction reads one chunk at a time. A happening that the corpus describes over
several chunks or documents (an admission and a discharge, a trip's departure and return, a
case opened and closed) comes out as **separate occurrent observations**, often under
different names. Nothing later joins them. The class cannot hold a span either: an occurrent
class has one `valid_from`-tagged date and, by the projection's rule, two observations of one
occurrent entity that disagree on it are a `:Conflict`, not a history.

So a question of the shape "when did PERSON_A go into HOSPITAL_B for PROCEDURE_C, and how many
days was it?" has no graph answer, and no single passage holds it either.

This design adds an **assembly pass**: an explicit, LLM-bounded second pass that builds
structure *across* observations and the entities they connect to. Its first product is the
**episode**: a derived node that groups the occurrent observations of one happening, with a
start, an end, a duration, its participants and its member observations as evidence.

- Episodes are **derived, never extracted**: the members stay as they are; an episode points at them.
- Episodes are **curation records** in the vault (`.artmind/data/curation/episodes/`), so
  `projection rebuild` reproduces them and `vault sync` carries them, like syntheses.
- Grouping is **code first, LLM second**: deterministic blocking proposes candidate groups; a
  bounded LLM call per group confirms it and labels member roles; dates and durations are
  computed in code from the members' own dates.
- Planner: a new route **`episode`** (not `event`: `EVENT` is a schema class in four schemas).
  With episodes in the domain it is a graph lookup; without them, it falls back to an
  anchor-then-expand text program that needs the P3 executor's step bindings.
- Later assembly tasks reuse the same frame: closing intervals (`valid_to`), recurrence,
  cross-document coreference (into the same-as queue), plan → outcome links.

## 1. The problem, precisely

| Gap | Where it comes from | Effect on questions |
|---|---|---|
| One happening, several observations | extraction is per chunk; per-document canonicalisation only folds names within one document | "how long", "what happened after", "from when to when" have no single fact |
| Different names for one happening | "the operation", "PROCEDURE_C", "came home today" | the entity key (canonical name + class + domain) splits them |
| No span on an occurrent class | the class carries one date property (`temporal: valid_from`, `exclusive: true`) | no end date, no duration |
| Disagreeing dates are conflicts | projection rule: occurrent + different `valid_from` → `:Conflict` | merging the members into one EVENT entity would raise false conflicts |
| Recurrence unknowable per chunk | `is_recurring` is asked of each chunk | wrong or empty |
| `valid_to` rarely set | a state ends in a later document ("moved out", "left the job") | point-in-time questions over recurrent states read stale facts |

`projection synthesize` does not cover any of this: it rewrites one entity's description from
that entity's own observations and writes no structure (`artmind/synthesize.py`).

## 2. Goals and non-goals

Goals:

1. Answer span questions (start, end, duration, what surrounded it) from the graph, with the
   member observations' chunks as citations.
2. Keep the projection deterministic: every episode is a record that a rebuild applies without an LLM.
3. Keep cost explicit and bounded: never automatic, never per document, `--limit`, `--dryRun`.
4. Give the planner a fact (`episode_count`) and an asset kind (`episode`), so routing knows
   whether the cheap path exists.

Non-goals:

- Changing first-pass extraction or the observation format.
- Merging observations or entities. Episodes sit beside them; same-as merges stay in curation.
- Inferring facts no member states. A duration is computed only from dates the members carry.

## 3. The episode

```
(:Episode {
   id,                  # sha256 of the domain + the anchor observation id (the earliest member at creation)
   _domain, title, episode_type,
   started_on, ended_on, duration_days,   # computed in code from member dates; null when unknown
   date_basis,          # "member_dates" | "document_dates" | "partial"
   status,              # "confirmed" | "proposed" | "stale"
   confidence, built_at, model, record_fingerprint,
   member_set_hash
})
(:Episode)-[:HAS_MEMBER {role}]->(:Entity)        # role: start | end | during | aftermath | cause
(:Episode)-[:INVOLVES {role}]->(:Entity)          # participants, places, organisations, objects
```

Members are recorded as **observation ids** in the record (deterministic, `sha256(chunk_id |
canonical_name | class | domain)`) and projected to the entities those observations currently
build. That handles the case where one EVENT entity (a recurring "checkup") aggregates
observations from several episodes: the episode keeps only its own observations.

Rules at rebuild:

- A member whose observation went to `history` (its document was re-versioned or retired) drops out.
- An episode left with fewer than two latest members, or that lost its start or end member,
  is `stale` and is queued for the next `assemble` run, the same way a synthesis whose set shrank is.
- `started_on` / `ended_on` / `duration_days` are recomputed from the surviving members.
- An `:Episode` is **not evidence by itself**: no `Document`, `DocChunk` or `DocPassage`
  label, not in the vector-text or survey indexes. An answer cites the members' chunks
  (`EXTRACTED_FROM`), the same rule as `:TopicPage`.
- `catalogue.state.bump(tx, domains)` in the same transaction that writes or drops episodes.

## 4. Building episodes

### 4.1 Blocking (code, no LLM)

Input: the latest observations of the configured occurrent classes in a domain (default:
every class with `kind: occurrent` that the assembly config enables).

Two observations are a **candidate pair** when, within the class's time window (§6), at least
two of these hold:

| Signal | Source |
|---|---|
| shared participant | both link to the same PERSON (or other agent class) entity, directly or via `participants` |
| shared neighbour | same LOCATION, ORGANIZATION or OBJECT entity |
| similar type | same `type`, or types listed as related in config (`appointment` → `health_incident`) |
| similar name | casefolded token overlap, or embedding cosine over name + description above τ |
| adjacent text | same document, or documents adjacent in date order for one narrator |

Candidate groups are the connected components of the pair graph, capped at `max_members`
(default 12). A component over the cap is split at its weakest pair before the LLM sees it.

### 4.2 Confirmation (one LLM call per group, bounded)

The prompt gets the group's members (name, type, date, description, the sentence they came
from, their document date) and returns, as JSON:

- whether the members are one episode, several, or none (it may split a group, never join two);
- per member: `role` (start, end, during, aftermath, cause) or `not_member`;
- a short title and an `episode_type` from the class's `type_examples`;
- a confidence.

Post-checks in code, any failure → the group is not recorded:

- every member id in the reply is one that was sent;
- dates are never taken from the reply: `started_on` = the date of the earliest `start` (else
  earliest member), `ended_on` = the date of the latest `end` member, computed from the
  members' own date property, else their document `valid_from` (`date_basis` says which);
- `duration_days` only when both ends are dated; otherwise null and the title says "end not recorded".

Groups with confidence below the domain's threshold become `proposed` and go to the review
queue (the same-as review pattern; later the P5 Inbox). `confirmed` episodes apply directly.

### 4.3 Incremental runs

`assemble` fingerprints each candidate group by its member set. An unchanged group is skipped;
a group that grew (a new document added the discharge) is re-confirmed; a stale episode is
rebuilt. A full re-run on an unchanged graph makes no LLM call.

## 5. Records, CLI and the pipeline

**Curation kind `episodes`.** One file per episode,
`.artmind/data/curation/episodes/<episode id>.json`, registered in
`curation_records.kinds()` after `syntheses`. It holds the node properties, the member
observation ids with roles, the involved entity keys with roles, and `record_fingerprint`.
`vault sync` applies it before the union rebuild, like syntheses. Removing the file removes the
episode. No embedding is stored.

**CLI.**

| Command | What it does | Tier |
|---|---|---|
| `artmind projection assemble --domain D [--task episodes] [--class C] [--since DATE] [--limit N] [--model M] [--noLLM] [--dryRun]` | blocking + confirmation + records; `--noLLM` writes blocking groups as `proposed` only | write, tier 1 (`--dryRun` returns the candidate groups and the `effect` block: groups, LLM calls, est. cost) |
| `artmind query episodes list --domain D [--entity NAME] [--from DATE] [--to DATE] [--type T]` | read-only listing, brief/full shape | read, proxied, traced |
| `artmind query episodes show <id>` | one episode with members, roles, dates and member chunk ids | read, proxied, traced |
| `artmind curate episodes review` (later, with P5) | confirm, split, reject proposed episodes | write |

`assemble` goes in `WRITE_COMMANDS` and gets `test/test_dry_run_projection_assemble.py`. It
runs where `synthesize` runs: on demand, or as the deliberate step after a bulk ingest
(`ingest sync <dir>` → `projection synthesize` → `projection assemble`).

**Assets and catalogue.** `assets.build_assets` gains kind `episode` (one record per domain:
count, types, newest `built_at`, stale count). The catalogue's `assets` part carries it, and
`candidates.assets_line` adds "N episodes". The planner reads it as plan fact `episode_count`.

## 6. Configuration

`domains/assembly/<domain>.yaml`, never seeded, outside `schemas/` so it never moves
`schema_set_hash()` and never triggers re-extraction (the insights config pattern):

```yaml
episodes:
  classes: [EVENT]                 # occurrent classes to assemble; default: none (opt-in)
  window_days: {default: 7, health_incident: 60, visit: 21}
  related_types: [[appointment, health_incident], [visit, outing]]
  agent_classes: [PERSON]          # which classes count as participants
  max_members: 12
  confirm_threshold: 0.7
```

`artmind assembly config check` validates it against the domain schema (unknown class or type
→ error).

## 7. Routing

**Route `episode`.** Description: when something happened, how long it lasted, and what
surrounded it. Signals: a when / how long / how many days / from-to ask together with an event
noun, often with a person. Programs, chosen by plan facts:

| Kind | When | Program |
|---|---|---|
| `episode_lookup` | `episode_count >= 1` and an entity or event term resolves | `query episodes list --entity {entity_name}` → `episodes show` on the best match → `chunks --idList {member_chunks} --expand 1` for the citations |
| `event_window` | no episodes, or no match | anchor: needle `vector-text` on the event terms (narrator/kinship slot narrows the person) → expand: `chunks --expand 2` on the anchor, documents dated in `[anchor, anchor + window]`, the anchor's connected entities |
| fallback | anchor not found | `search` |

`event_window` needs a later step to read an earlier step's result (the anchor's chunk id and
date). The program grammar fills every argument at plan time, so `event_window` waits for the
P3 executor's step bindings. Until then the plan names the shape and the agent carries the
anchor into the next call. `episode_lookup` needs bindings too for the `show` step; its first
step alone already answers most questions.

Labels: tag span questions `route: episode` now, before the route exists, so replay counts how
often the shape occurs and the labels become its test set.

## 8. Other assembly tasks (same frame, later)

Each is a `--task` of `projection assemble`, with its own curation kind, blocking in code,
an optional bounded LLM confirmation, and dates computed in code.

| Task | Builds | Notes |
|---|---|---|
| `intervals` | `valid_to` on recurrent-class observations whose state a later observation replaces (address, job, role, status) | Mostly deterministic: same entity, same exclusive property, later `valid_from`. LLM only for "is this a replacement or an addition". Feeds `--asOf` |
| `recurrence` | `is_recurring` and occurrence counts per event type and participant | Derived from episodes; no LLM |
| `coreference` | cross-document same-as **proposals** for occurrent and recurrent names ("the operation" ↔ PROCEDURE_C) | Writes to the existing same-as review queue; never merges by itself |
| `fulfilment` | `(:PLAN)-[:FULFILLED_BY]->(:Episode or :Entity)` | Complements the insights `open_loops` detector, which finds unfulfilled plans read-only |

Order: episodes, then intervals (biggest effect on point-in-time answers), then recurrence
(cheap once episodes exist), then coreference and fulfilment.

## 9. Privacy and cost

- `assemble` sends member observations (names, types, descriptions, dates, one source sentence
  each) to the configured provider and logs the calls in `llm_calls.log`. `--noLLM` keeps
  everything local and leaves groups `proposed`.
- Episode records hold private corpus content and live in the vault's private repo, like
  syntheses. Never copy them into code, tests or commits; tests use synthetic observations.
- Cost scales with candidate groups, not documents. `--dryRun` reports the group count and an
  estimate before any call.

## 10. Measurement

- **Grouping quality**: a hand-labelled set of episodes on a public corpus (the banking
  corpus's occurrent classes in its cases and communications domains are the first candidate;
  to be checked for multi-document happenings), scored by pairwise precision/recall of
  membership and exact-day accuracy of start/end.
- **Answers**: plan labels with `route: episode`; replay grades the route; a targeted agent
  run compares `episode_lookup` against `event_window` and against today's `search` on the same
  questions (answer correct, calls, cost).
- Gate to switch `episode_lookup` on in the planner: membership precision ≥ 0.9 on the
  labelled set, and no labelled span question worse than `search`.

## 11. Risks

| Risk | Answer |
|---|---|
| Over-merging recurring events (two separate hospital stays) | time window per type; the LLM may split, never join; members of a group must share a participant |
| A stale episode answering after its members changed | rebuild drops history members and marks the episode stale; `episodes show` returns `status` |
| Date taken from the model | never: dates come from member properties or document dates in code |
| Another LLM pass nobody runs | listed in the post-ingest sequence; `pipeline status` (P7) reports stale or unassembled domains; the catalogue line shows "0 episodes" |
| Name clash with the `EVENT` class | the node is `:Episode`, the route is `episode`, the curation kind is `episodes`; no class label on the node |

## 12. Open questions

1. Should `:Episode` carry an `:Entity` label so `entity-resolve` can find it by title? The
   default here is no (keeps it out of `entity_embedding`), with `query episodes list --entity`
   doing the lookup instead.
2. Do participants come from `participants` property values, from relationships, or both?
   Depends on how consistently the journal schema's extraction fills each; to be measured.
3. Is one window per type enough, or does the window need to grow while members keep arriving
   (a long illness)? Start with fixed windows plus chaining through the pair graph.
4. Does `intervals` belong here or in `projection rebuild` itself, since most of it is deterministic?

## 13. Phasing

| Step | Contents | Gate |
|---|---|---|
| PA1 | `episodes` curation kind, `:Episode` projection and rebuild rules, `query episodes list/show`, assets line, `episode_count` fact | rebuild reproduces episodes from records; dry-run tests on the Cypher sent |
| PA2 | `projection assemble --task episodes` (blocking, confirmation, post-checks), config + `config check` | grouping quality gate (§10) on the labelled public set |
| PA3 | route `episode` with `episode_lookup`; `event_window` once P3 bindings exist | replay on `route: episode` labels; agent comparison |
| PA4+ | `intervals`, `recurrence`, `coreference`, `fulfilment` | each its own measurement |

PA1 and PA2 do not depend on P3 and can run beside it. PA3's `event_window` depends on P3.
