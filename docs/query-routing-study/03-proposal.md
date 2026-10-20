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
2. **Views, always**: embed each view's `summary` + `examples`, score the question; fill
   params: entity params by resolving noun phrases filtered on the param's
   `entity_class`, int/date params by regex ("in 2025", "last year"). A strong match
   returns the full `views run … --param p=@<id>` line, or runs it with `--execute`.
   Views-first stops being a rule the agent can skip (P1, P4).
3. **Entities**: resolve mentions; add `observation_count`/`connection_count`; flag hubs
   so the plan routes to `entity-context --query`.
4. **Intent, lexical rules first**:

   | Question looks like | Route |
   |---|---|
   | count / total / average | aggregate (view, `text2sql`, or `text2cypher`) |
   | as of / before / changed / history | temporal, with one `asOf` threaded through every step |
   | history of entity / document versions | `entity-history` / `document-history` (P5) |
   | under / reports to / chain | hierarchy |
   | two entities + related / connected | path |
   | a document name matches | `pattern10` or `document-history` |
   | what changed / who matters / lost touch | insights |

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

1. A + B-as-a-table (text only, low risk).
2. B as data + D's `next` hints.
3. C, `query plan`.
4. D's command consolidation.

## Revision log

- 2026-10-19 — first draft from round 1 (brain01 traces, my_work bridge only).
- 2026-10-20 — round 2 (one `banking_corpus` turn, Claude SDK / Haiku 4.5): C1 supported, since
  "domains from `domains-overview` when `--domain` is absent" would have prevented P8; added
  the unknown-domain guard to D (P9). A also gains one rule for the routing table: a name not
  in `domains-overview` is not a domain, and all-zero results mean run Route before saying
  the data is absent.
