# 03 — Proposal (working draft)

Not a spec. Each element names the patterns (`02-trace-evidence.md`) and scenarios
(`04-scenarios.md`) it answers; an element no evidence supports is a candidate to drop.

## Diagnosis in one line

The skill grew by adding a rule per failure, and routing now lives in nine places in the
skill and three in code. The more rules there are, the less reliably any one is followed,
especially on weaker backends and on follow-up turns that don't reload the skill
(P1, P4).

## A. Restructure: short SKILL.md plus references

`SKILL.md` target about 120 lines, holding only what applies to every question:

1. **Grounding contract**: only CLI output; say when the data doesn't answer; `artmind:`
   stderr lines are advisory.
2. **The entry call** (today `domains-overview` + `views list`; later `query plan`) and how
   to read it.
3. **One routing table**: question shape → command → reference to read. The only routing
   in the skill.
4. **Answer style and Sources.**

References, read only when the routing table points to them:

| Reference | Content |
|---|---|
| `graph-model.md` | Fixed structural schema; needed only to check `text2cypher` Cypher |
| `views.md` | Matching, `status` handling, `@<_id>`, what to do on an empty result (P3) |
| `entity-and-hubs.md` | entity-context vs patterns 2/3/4, hubs, bounds, pagination |
| `temporal.md`, `conflicts.md`, `hierarchy.md`, `structured-store.md` | Kept, de-duplicated |
| `insights.md` | Out of `SKILL.md` |
| `commands.md` | Generated from Click (like `cli_guide.py`), so it can't drift |

Also: rewrite the frontmatter `description` around triggering questions; replace
prose-pinning tests with tests against data.

## B. Routing in one place

Routing as data, e.g. `artmind/query_routes.yaml`: intent → preconditions → steps →
fallbacks → reference. Readers:

- the skill's routing table (rendered from it, or checked against it by a test);
- `traces/path.py` (judge "followed the route", not only "listed views first");
- `query plan` (C).

The Fallback ladder becomes per-route `fallbacks`.

## C. Deterministic routing in the CLI — `artmind query plan "<question>" [--domain …]`

Pre-work done in code, returning a ready-to-run plan:

1. **Domains**: given `--domain`, else matched against `domains-overview` (document and
   class names, sibling-domain rule). Replaces the routing sub-agent.
2. **Views, when any exist**: `domains-overview` carries a view count per domain (R3 P10: a vault
   with none cost one empty `views list` per domain per turn); where the count is zero the
   step is skipped. Where views exist, embed each view's `summary` + `examples`, score the question; fill
   params: entity params by resolving noun phrases filtered on the param's
   `entity_class`, int/date params by regex ("in 2025", "last year"). A strong match
   returns the full `views run … --param p=@<id>` line, or runs it with `--execute`.
   Views-first stops being a rule the agent can skip (P1, P4).
3. **Entities**: resolve mentions; add `observation_count`/`connection_count`; flag hubs
   so the plan routes to `entity-context --query`. A first-person reference ("I", "my") resolves
   to the domain's narrator entity (R4 P19: the agent took a named person node instead and
   the view returned nothing). Where several nodes of the right class share a name, the plan
   lists all of them and the view runs once per candidate; degree is not a safe tie-break
   (the node holding the trip was the lower-degree twin).
4. **Intent, lexical rules first**:

   | Question looks like | Route |
   |---|---|
   | count / total / average | aggregate (view, `text2sql`, or `text2cypher`) |
   | as of / before / changed / history / "which version applied" | temporal, with one `asOf` threaded through every step, including `pattern10 --asOf` and `document-history` with the `.md` name (R3 P12) |
   | conflict / differ / inconsistent / which applies | conflicts: `query graph conflicts`, then `references/conflicts.md`; fetch the supersession-notice chunk (R3 P13) |
   | history of entity / document versions | `entity-history` / `document-history` (P5) |
   | under / reports to / chain | hierarchy |
   | two entities + related / connected | path |
   | a document name matches | `pattern10` or `document-history` |
   | what changed / who matters / lost touch | insights |
   | favourite / most / top / least (superlative over a class; R4 P22) | `insights` salience first, else an executed `text2cypher` count; report ties and split nodes, and say the ranking is inferred |
   | all / every / complete / recorded (enumeration; R4 P21) | `pattern7`/`entity-listing` with `--limit` raised until `truncated` is false; cite documents |

5. **Store**: resolved classes ∩ `db bridge` classes, question terms ∩ bridge-column
   values → graph / analytical / hybrid / records-plus-guidance.
6. **Output**: `{domains, intent, confidence, entities, view_match, steps:[{cmd, why}],
   read:[reference], fallbacks:[…]}`. High confidence: run `steps`. Low: fall back to the
   routing table.

Record the plan as a trace span, so "plan followed vs overridden, and was the answer
right" becomes measurable and artmind-review fixes route data rather than prose.

Open: whether an LLM classifier is ever needed when lexical rules are ambiguous, or
whether "low confidence → routing table" is enough (`06-open-questions.md`).

## D. Other ideas

- **Time route made discoverable from the graph (R3 P12, corrected 2026-10-21).** Retired
  chunks are not in the vector or fulltext index (label swap), and the design decision is
  that they stay out: history is reached through the graph first, so a vector fallback for
  history is never needed. Deterministic changes, all graph-side:
  1. Document rows from `pattern10`, `chunks` and the `vector-text` document block carry
     `supersedes` (predecessor name and id) next to the existing `superseded_by`, so an
     agent holding the current version can name the retired one.
  2. `--asOf` becomes honest: either documented as a history flag (what it does today for
     `pattern10 --documentName`) or made to filter by `valid_from`/`valid_to`; the skill
     stops framing it as a date it does not apply.
  3. A `--documentName` that matches no document returns `next` with the closest names (a
     missing `.md` suffix is the commonest cause), and `document-history` accepts the name
     without the suffix when unambiguous.
  4. Skill rule: a date or "earlier version" question goes `pattern10`/`document-history`
     on the document found, then reads the predecessor via `supersedes`.
  `--includeHistory` on the search commands was considered and dropped: it would require
  indexing history chunks and a currency predicate, which today does not exist and cannot
  be forgotten. Skill side: the routing table's time row names `document-history` next to
  `pattern10 --asOf`.
- **Parent domain means all siblings (R3 P14).** `domains-overview` shows the parent name
  (`FAMILY`) as scoping all `FAMILY.*` siblings, and the skill's default for area questions
  is the parent, not a hand-picked subset. Optional: `vector-text` output lists the
  domains with zero hits, so a narrowed scope is visible.
- **Command aliases and `next` for invalid forms (R3 P15).** `query db sql`, `query graph
  entity-resolve` and `db sql --help` were tried by the agent; each returns a hint naming
  the real command rather than a bare usage error.
- **Wikilink hint.** When a retrieved chunk contains `[[links]]` to documents not yet read,
  the output lists them (R3 P14 early stopping).

- **Truncation and provenance on entity search (R4 P21, P24).** `pattern7` returns at most
  `--limit` rows (default 10) with no `rows_total` or `truncated`, and its rows carry no source
  document. Add both, as the other pattern commands do: `rows_total`, `truncated`, `next`
  ("raise `--limit`"), and the documents an entity was observed in (as `documents`, bounded).
  Deterministic and model-independent; it also feeds the ready `sources` block below.
- **`--dry-run` says it ran nothing (R4 P20).** The `text2cypher` / `text2sql` dry-run payload
  carries `next: "re-run without --dry-run to execute"` and no `rows` key at all (an empty list
  reads as a result). `traces` stops counting a dry-run as an unproductive graph attempt.
  The skill line ("run `--dry-run` first") gains "then run it". Separately, the `temperature=0`
  rejection is logged on every call; cache the learned setting per model for the process.
- **Output-size guard on discovery commands (R4 P23).** `graph metadata` (88 KB) and `pattern1
  --limit 100` (110 KB) were persisted to disk and previewed at 2 KB, so the turn paid for
  them and used none of it. Return a `next` pointing at `structural-metadata` or a smaller
  `--limit` when the payload exceeds a threshold.
- **First-person and memory framing on chat surfaces (R4 P18).** `ADMIN_SYSTEM_APPEND`
  describes artmind-query as the tool "for inspecting the graph"; the QA profile already says
  "for questions". Candidates, cheapest first: state that questions about the user's own life,
  work or notes are answered from artmind, never from the agent's memory; rewrite the skill
  `description` around such questions (A); a deterministic first-turn check (C). Test on the
  admin profile first, since the QA profile was not exercised.
- **Unknown-domain guard.** A `--domain` that matches no known domain returns today an empty
  success (P9). Return a `warnings` entry naming the unknown value and the known domains
  (or fail), so an invented domain cannot read as "no data". Deterministic, model-independent;
  the cheapest fix for P8/P9 and a natural first `next` hint.
- **Commands return their own next step.** On empty or truncated output, the JSON carries
  `next` with an exact command: `pattern6` empty → `pattern5 --mode shortest`;
  `hierarchy` empty → `pattern1` property fallback; `text2cypher` 0 rows → dry-run hint;
  a view with 0 rows → what to check (P3). Removes the ladder and most routing notes.
- **Fewer, fused commands** (old names kept as aliases): patterns 2/3/4 → `entity-context`
  flags; 6-then-5 → one `relate` that escalates; `pattern7` / `entity-listing --nameFilter`
  / `entity-resolve` → one resolver.
- **Composite commands for observed loops**: date range + mentioned entity → chunks,
  replacing per-note `pattern10` loops (P2).
- **Views proposed from traces**: shapes that repeatedly fall through become candidates
  for artmind-create-view.
- **CLI emits a ready `sources` block**; the skill's Sources rules shrink to "paste it".
- **Measure**: benchmark + `traces stats` for current skill vs slim skill vs slim + `plan`;
  track `views_skipped_rate`, calls per turn, fall-through, answer correctness.

## Phasing (tentative)

1. A + B-as-a-table (text only, low risk), plus the time and conflicts rows (R3 P12, P13).
2. B as data + D's `next` hints, the `supersedes` pointer and `--asOf` fix, conditional views-first (R3 P10).
3. C, `query plan`.
4. D's command consolidation.

## Testing strategy

The full 36-question benchmark is slow and expensive (about 94 minutes, $3.67). Run it once,
as the final gate, after every change has passed the cheaper levels below.

1. Unit tests (about 9 s, no network). Assert the Cypher and parameters actually sent, not
   summary counts (CLAUDE.md, "a mocked graph session answers every query").
2. Deterministic CLI probes on `banking_corpus` with `ARTMIND_NO_PROXY=1`:
   `document-history` without `.md`; `supersedes` present on the current version's row;
   `domains-overview` view counts; domain-guard warnings.
3. Targeted agent runs on the affected questions only: Q07, Q09, Q10, Q11 (time route),
   Q13 (conflicts), Q18/Q19 (domain scope).
4. One full re-run as the final gate, compared with `07-benchmark-round3.md`.

## Revision log

- 2026-10-19 — first draft from round 1 (brain01 traces, my_work bridge only).
- 2026-10-20 — round 2 (one `banking_corpus` turn, Claude SDK / Haiku 4.5): C1 supported, since
  "domains from `domains-overview` when `--domain` is absent" would have prevented P8; added
  the unknown-domain guard to D (P9). A also gains one rule for the routing table: a name not
  in `domains-overview` is not a domain, and all-zero results mean run Route before saying
  the data is absent.
- 2026-10-20 — D's unknown-domain guard implemented in the CLI (`artmind/domain_guard.py`): a warning, not
  an error, which settles open question 11 for now. The skill-side rules stay with this dossier.
- 2026-10-21 — round 3 (36 benchmark turns, `banking_corpus`, Claude SDK / Haiku 4.5): graded
  9 correct, 24 partial, 3 wrong; routing is the primary cause for 14 of 27 non-correct
  answers. Changes: C2 made conditional (views-first costs one empty call per domain in a
  vault with no views, P10); the temporal row of C4 names `document-history` and `.md` names
  and gains a conflicts row (P12, P13); D gains the time-route hint, parent-domain fan-out,
  command aliases and the wikilink hint (P12, P14, P15). Supported: C1, C4, the unknown-domain
  guard (worked, P11). Untested still: C3 (hubs), hierarchy and path routes.
- 2026-10-21 (correction) — P12 mechanism corrected: retired chunks are absent from the
  vector/fulltext indexes by label, there is no currency predicate. Decision: retired
  chunks stay out of the vector index and `--includeHistory` is dropped; history becomes
  discoverable from the graph (`supersedes`, honest `--asOf`, `.md` hint, skill rule). Five
  questions need it (Q07, Q09, Q10, Q11, Q13); Q21/Q25 are a data error. Testing strategy
  section added.
- 2026-10-22 — supersession root cause: the adjudicator's newer-side choice compares empty
  `valid_from` values and always picks B on a tie; its scope is hardcoded to `document` and
  sub-document scopes do not exist. The P12 graph-side design is unchanged and is also the
  answer for Q11; the ingest-side fix (no supersession on empty dates, text-signal
  tie-breakers, why `valid_from` is empty) is recorded in open questions 15, 18 and 20 and
  stays out of this study's scope until "move to specs".
- 2026-10-22 (round 4) — 11 brain01 turns, Claude SDK / Haiku 4.5. Supported: C1/C2 (the
  first-turn skill skip, P18), C3 (first-person to the narrator, P19; **degree is not a safe
  tie-break**, so the hub rule is narrowed), C4 (superlative and enumeration rows, P21/P22),
  the `next` hints in D (dry-run, truncation, size guard) and the ready `sources` block (P24).
  New in D: truncation and provenance on `pattern7`, an honest `--dry-run`, an output-size
  guard, chat-surface memory framing. Unchanged: A, B, the time route. Untested still:
  hierarchy, path and filing routes; a model larger than Haiku on a first-person question.
