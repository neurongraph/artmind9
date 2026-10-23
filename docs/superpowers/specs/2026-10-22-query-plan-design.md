# `artmind query plan` v1 — design

Date: 2026-10-22. Status: **draft for review**; §4 contracts and §5–§7 approved in
conversation. Source: the query-routing study, `docs/query-routing-study/` (evidence in
`02-trace-evidence.md`, plan in `08-fix-and-redesign-plan.md`, workstreams W0 and W2).

## 1. Problem

The `artmind-query` skill routes a question to CLI commands through about 8,500 words of
prose, with routing stated in nine places in the skill and three in code. Recorded traces
(rounds 1–5, two vaults, about 130 turns) show the result:

- **The same question takes different routes.** One count question, a saved view in the
  catalogue, run four times on the benchmark profile: four routes, three answers, the view used
  once (P26). In chat, two of five asks of the same shape used the view (P1).
- **Facts the agent cannot guess are guessed.** The domain (P8, P11, P14), whether views exist
  (P10), who "I" is (P19), which of two same-named nodes holds the data (P19, P22).
- **Commands report misleadingly.** A dry run looks like zero rows (P20); entity search silently
  stops at 10 rows and names no source (P21, P24); history is reachable only behind a flag the
  docs describe as a date (P12); a name missing its suffix returns nothing and says nothing (P12).

Adding more prose has stopped working (the skill grew by one rule per failure). The fix is to do
the routing in code, once, deterministically, and make every command the route uses report
honestly.

## 2. Scope

**In scope:**

1. `artmind query plan`, a read-only, deterministic command that returns a ready-to-run plan
   for a question (§4.1, §4.2), with a packaged routes file (§4.3), a pluggable classifier
   (lexical, and decision models over the Jev API, §4.4), and per-domain config (§4.5).
2. The labelled set and offline replay (§4.6): the plan's test fixtures and its gate.
3. Honest-command fixes the plan depends on (§5).
4. Shadow mode on the traced surfaces (§6).

**Out of scope** (each is a later spec or an existing workstream):

- Wiring the plan into the skill and shrinking the skill (study W3). Until then the agent never
  sees a plan except by calling `query plan` itself.
- Traces on the chat UI (it records none today), so no shadow mode there.
- An LLM classifier; embedding-based view matching (study W5).
- A date-honest `--asOf` (needs the `valid_from` diagnosis, study W1.1); the supersession
  tie-break and the banking vault repair (study W1).
- Per-vault route overrides; sub-document supersession; command consolidation.

## 3. Architecture

### 3.1 Components

New package `artmind/query_plan/`. Each module has one job and is tested on its own.

| Module | Does | Depends on |
|---|---|---|
| `routes.yaml`, `routes.py` | The routing data (§4.3) and its loader/validator | — |
| `config.py` | Per-domain plan config (§4.5) | `paths` |
| `candidates.py` | Deterministic candidates: domains, views, mentions, resolved entities, twins, store facts | `graph_query.domains_overview`, `domain_guard`, `kg_views.store`, `vector_query.entity_resolve`, `db` bridge readers |
| `classify/base.py` | `Decision` and `Answer` types, the `Classifier` protocol | — |
| `classify/lexical.py` | Trigger and token-overlap scoring | `routes` |
| `classify/jev.py` | Jev-API client with fallback and circuit breaker (§4.4, §7) | stdlib `urllib.request`, as `_entry.py` uses (`httpx` is a dev-only dependency, and a core install must not need a new one) |
| `fill.py` | View param filling by type (§4.2 `view_match`) | `kg_views.model`, `kg_views.params` |
| `planner.py` | Runs the stages, assembles the payload, optional `--execute` | all of the above, `kg_views.runner` |
| `labels.py` | Labelled-set parser and validator (§4.6) | `benchmark._parse_questions` |
| `replay.py` | Replay and scoring (§4.6) | `planner`, `labels` |
| `shadow.py` | Background plan for a traced turn (§6) | `planner`, `traces.store` |

CLI: `query plan` in the `query` group (proxied, traced); a new top-level `plan` group with
`config check`, `labels validate` and `replay` (not proxied). Both are routed in `COMMAND_GROUPS`
(`test/test_cli_guide.py` fails otherwise).

### 3.2 Data flow for one plan

```
question, --domain?
  │
  ├─ candidates: domains (overview + guard) ─ views of those domains ─ mentions
  │              ─ entity_resolve per mention (exact/fulltext only) ─ twins ─ store facts
  │
  ├─ classifier call 1: domain? (if not given) + view? (if any views) + route
  │
  ├─ classifier call 2 (only if a view with entity/enum/bool params was chosen):
  │              one question per param over that param's candidates
  │
  ├─ fill: scalar params by regex/default; twins expanded to combinations
  │
  └─ assemble: route → step templates filled → confidence → payload
                 └─ --execute: views run (only high confidence, one combination)
```

At most two classifier calls; with the lexical backend, none leave the process.

## 4. Contracts

Seven contracts. Each lists who writes it, who reads it, every field, the validation
rules and what happens when something is missing or wrong. Items marked **Review** are
choices I made that you may want to change.

### 4.1 The command: `artmind query plan`

```
artmind query plan "<question>" [--domain D ...] [--execute]
                   [--classifier auto|lexical|decision] [--compact]
```

| Argument | Meaning |
|---|---|
| `QUESTION` | Required. The user's question, verbatim. Empty or whitespace-only → `ClickException`. |
| `--domain` | Optional, repeatable, comma-splittable (the usual `_parse_domains`). Given → the plan uses exactly these (unknown names still produce the domain-guard warning). Absent → the plan chooses (§4.2 `domains`). **Review:** every other `query` command requires `--domain`; this one must not, since choosing the domain is part of its job (P8, P11). |
| `--execute` | Also run the plan's single step when, and only when, the route is `view`, confidence is `high` and the view has exactly one parameter combination (no twins). Otherwise the flag is ignored and a warning says why. Runs nothing else, ever. |
| `--classifier` | `auto` (default): `decision` when a decision endpoint is configured (§4.4), else `lexical`. With `--domain` given, the selected domains' config (§4.5) may override this; without it, the domain is itself chosen by the classifier, so only the endpoint setting decides. Forcing `decision` with no endpoint configured is an error. |
| `--compact` | Compact JSON, as everywhere. |

**Behaviour guarantees:**

- **Read-only.** Never writes the graph, the registry or any file. `--execute` only calls the
  `views run` code path, which is read-only by construction (kg_views spec).
- **Deterministic.** Same question, graph, config, routes file and classifier model → the same
  payload, except `classifier.latency_ms`. Tested by running a question 4 times (the P26 test).
- **Exit codes.** 0 whenever a plan is produced, including a low-confidence one. Non-zero only
  for an empty question, an invalid option, a forced `decision` with no endpoint, or Neo4j
  unreachable (candidate generation needs it, like every other `query` command).
- **Classifier failures are not errors.** A decision endpoint that times out, refuses or
  returns an unusable answer → that request is redone with the lexical classifier, and
  `classifier.fell_back` plus a warning say so.
- **Proxied** by `serve`, like all `query` commands, so the stale-daemon rule applies to every
  test (CLAUDE.md §1). **Traced** automatically by `TracedGroup` as a span.

### 4.2 The plan payload (stdout JSON)

Every field is always present, null or empty when not applicable, so callers never test for
key existence. New fields may be added later; none will change meaning (`plan_version` bumps if
one has to).

```json
{
  "plan_version": 1,
  "question": "…",
  "domains":    { … },
  "route":      { … },
  "view_match": { … } | null,
  "entities":   [ … ],
  "catalogue":  { … },
  "store":      { … } | null,
  "confidence": { … },
  "steps":           [ … ],
  "suggested_steps": [ … ],
  "fallbacks":  [ … ],
  "read":       [ … ],
  "classifier": { … },
  "warnings":   [ … ],
  "executed":   { … } | null,
  "rows": [ … ], "rows_total": 0, "truncated": false
}
```

#### `domains`

```json
{ "selected": ["banking"], "source": "parent",
  "confidence": 0.86,
  "candidates": [{"domain": "banking", "p": 0.86}, {"domain": "banking.policy", "p": 0.09}] }
```

| Field | Meaning |
|---|---|
| `selected` | Domains every step is scoped to. Passed to commands as one comma-joined `--domain`. |
| `source` | `given` (from `--domain`), `matched` (the classifier picked one or more leaf domains), `parent` (it picked a family parent such as `banking`, which scopes all `banking.*`; P14), `all` (nothing decidable: every domain, and confidence is forced low). |
| `confidence` | The classifier's probability for the chosen option; `null` when `given`. |
| `candidates` | Top 3 options with probabilities; empty when `given`. |

Options offered to the classifier: each leaf domain from `domains-overview` **and** each family
parent that has two or more children. Each option's text is built from the overview: document
count, the first document names and the top classes (§4.4).

#### `route`

```json
{ "id": "time_history", "confidence": 0.81,
  "alternatives": [{"id": "document", "p": 0.11}, {"id": "procedure", "p": 0.04}],
  "triggered_by": ["earlier version"] }
```

| Field | Meaning |
|---|---|
| `id` | A route id from `routes.yaml` (§4.3), or the pseudo-route `view` when `view_match` is confident. |
| `confidence` | Probability of the chosen route. |
| `alternatives` | Next two routes with probabilities. |
| `triggered_by` | Lexical classifier only: the trigger patterns that matched. Empty with `decision`. |

**Review: `view` is not one of the route options.** View selection is its own classifier
question ("which saved view, or none?"). The final route is `view` if that answer is a view with
probability ≥ threshold, otherwise the route question's answer. Keeping them apart means adding
a view never changes the route options, and a vault with no views asks no view question (P10).

#### `view_match`

`null` when the domain has no views, or the view question answered `none`. Otherwise:

```json
{ "name": "trips_by_year", "version": 1, "confidence": 0.91,
  "status": "ok",
  "params": {
    "person": { "type": "entity", "via": "narrator", "input": "I",
                "chosen": {"_id": "…", "name": "Diarist", "entity_class": "PERSON", …},
                "candidates": [ … ], "twins": false, "confidence": 0.97 },
    "place":  { "type": "entity", "via": "mention", "input": "CITY",
                "chosen": null,
                "candidates": [{"_id": "a…"}, {"_id": "b…"}], "twins": true, "confidence": 0.95 },
    "year":   { "type": "int", "via": "regex", "input": "in 2025", "value": 2025 },
    "merge_within_days": { "type": "int", "via": "default", "value": 10 }
  } }
```

`status`: `ok` (every required param filled), `needs_disambiguation` (an entity param has
several plausible candidates that are not twins), `no_match` (an entity param's mention resolved
to nothing of its class), `missing_param` (a required param has no value; the view still
appears so the agent can ask the user).

How each param type is filled:

| View param type | How | `via` values |
|---|---|---|
| `entity` | Mentions in the question are resolved (§4.2 `entities`); candidates of the param's `entity_class` are offered to the classifier ("which of these is *the traveller*?" from the param's `description`), with `none` as an option. First-person words map to the narrator (§4.5). | `mention`, `narrator` |
| `int`, `float` | Regex over the question (`in 2025`, `last 3 years` → resolved against today), checked against `min`/`max`. | `regex`, `default` |
| `date` | Regex: ISO dates, `D Month YYYY`, `Month YYYY`, `last year`, `this month`, resolved to ISO. | `regex`, `default` |
| `enum` | Classifier `choice` over the declared `values`. | `classifier`, `default` |
| `bool` | v1: the param's default (a required `bool` → `missing_param`). A classifier `noul` question from the param's `description` once `noul` is verified on both endpoints (§10). | `default` (later `classifier`) |
| `string` | Only a quoted span in the question ("…"). Never invented. | `quoted`, `default` |
| `list` | Not filled in v1 unless it has a default. | `default` |

**Review: free `string` and `list` params are not guessed.** A decision model can only choose,
and a lexical guess would be a fabrication. A required one makes `status: missing_param`.

**Twins** (P19): candidates of the right class with the same casefolded name. They are not
disambiguated. `twins: true`, `chosen: null`, and `steps` gets one `views run` per
combination, up to 4 combinations; more than 4 → `confidence.level: low`.

#### `entities`

Every mention found, whether or not a view uses it (entity routes need them too):

```json
[{ "mention": "PERSON_A", "span": [24, 32], "via": "capitalised",
   "candidates": [{ "_id": "…", "name": "…", "entity_class": "PERSON", "domain": "…",
                    "observation_count": 12, "connection_count": 40,
                    "matched_by": ["exact"] }] }]
```

Mention sources (`via`): `capitalised` (runs of capitalised words not at sentence start, or at
sentence start when the word is not a stopword), `quoted`, `first_person` (I, me, my, mine,
myself → the narrator), `class_word` (a word equal to a class name, e.g. "trips" is not, "PERSON"
is). Candidates come from `vector_query.entity_resolve`, and **only exact or fulltext evidence
counts** (the kg_views rule: the vector leg always returns neighbours, so it is not evidence).
At most 5 candidates per mention, 8 mentions per question.

#### `store`

`null` unless a selected domain has structured tables (`db bridge`). Then:

```json
{ "kind": "hybrid", "tables": ["TABLE_1"], "bridge_classes": ["CUSTOMER"],
  "matched_values": [{"table": "TABLE_1", "column": "status", "value": "open"}] }
```

`kind` is decided deterministically: no resolved class is bridged and no value matched →
`graph`; aggregate wording ("average", "total", "how many" over a table's grain) and a table
whose grain fits → `analytical`; a bridged class plus a number → `hybrid`; matched bridge values
plus policy or guidance wording → `records_plus_guidance` (structured-store.md's four cases,
S7–S9). It feeds the `structured` route's step templates.

#### `confidence`

```json
{ "level": "high", "score": 0.81, "threshold": 0.7, "weakest": "route" }
```

`score` = the minimum over every stage that was decided rather than given: domain (when not
`given`), route, view (when a view was offered), each entity param. `level` is `high` when
`score >= threshold` **and** `view_match.status` is `ok` or `null`; otherwise `low`. `weakest`
names the stage that set the score (`domain`, `route`, `view`, `param:<name>`), so a reviewer
sees at once why a plan abstained.

#### `steps`, `suggested_steps`, `fallbacks`, `read`

```json
"steps": [
  {"n": 1, "cmd": "artmind query views run trips_by_year --domain personal_journal --param person=@a… --param place=@b… --param year=2025 --compact",
   "why": "saved view trips_by_year answers this shape (p=0.91)"}
],
"suggested_steps": [],
"fallbacks": [
  {"when": "zero_rows", "cmd": "artmind query vector-text --domain personal_journal \"…\" --compact",
   "why": "the view found nothing; check the notes over the same year"}
],
"read": ["references/views.md"]
```

- `cmd` is a complete shell-ready string: every placeholder filled, every value quoted with
  `shlex.quote`, `--compact` appended. A template whose placeholders cannot all be filled is not
  emitted, and that lowers `level` to `low`.
- **Review: high confidence → `steps` filled, `suggested_steps` empty. Low confidence → `steps`
  empty, `suggested_steps` holds the top route's steps, and `read` adds the routing table.** The
  split lets the skill say "run `steps`; judge `suggested_steps`", and lets traces measure
  whether a low-confidence suggestion was taken and whether that turned out right.
- `when` is one of a fixed vocabulary: `zero_rows`, `truncated`, `no_document`,
  `needs_disambiguation`, `error`.
- `read` paths are relative to the artmind-query skill folder.

#### `classifier`

```json
{ "backend": "decision", "model": "nimble", "calls": 2, "latency_ms": 184,
  "fell_back": false, "reason": null }
```

`reason` is set when `fell_back` is true (`timeout`, `http_<code>`, `bad_answer`) or when
`auto` chose `lexical` (`no_endpoint`, `domain_config`). The endpoint URL is not echoed.

#### `warnings`

Strings: domain-guard warnings carried through; classifier fallback; narrator configured but not
found in the graph; twins found; a view listed in the catalogue but invalid; `--execute`
ignored and why.

#### `executed`, `rows`, `rows_total`, `truncated`

`executed` is `null` unless `--execute` ran. Then:

```json
"executed": {"cmd": "artmind query views run trips_by_year …", "status": "ok",
             "result": { …the views run payload minus its rows… }},
"rows": [ …the view's rows… ], "rows_total": 1, "truncated": false
```

**Review: the view's rows go at the top level.** The trace span summariser counts only a
top-level `rows` list (`traces/evidence.py`). Nested under `result`, an answered question would
be recorded as zero rows and its evidence ids lost. Without `--execute`, `rows` is `[]`. In
`traces/path.py` the command `query plan` is a new layer `plan` (not a graph attempt), and
`query plan` with `executed` counts as layer `view`.

### 4.3 The routes file: `artmind/query_plan/routes.yaml`

Packaged, read-only to vaults. The one place routing lives: the plan reads it, the skill's
routing table is rendered from it (or a test checks the table against it), and
`traces/path.py` uses its ids.

```yaml
version: 1
routes:
  - id: time_history
    description: >-
      Which version of a document applied on a date, or what an earlier,
      superseded version of a document said.
    triggers: ["as of {date}", "on {date}", "earlier version", "previous version",
               "before {date}", "which version", "used to say", "changed"]
    needs: [document]
    steps:
      - "artmind query graph pattern10 --domain {domains} --documentName {document} --includeHistory"
      - "artmind query document-history --domain {domains} --documentName {document}"
    fallbacks:
      - {when: no_document, cmd: "artmind query vector-text --domain {domains} {question}"}
    read: [references/temporal.md]
    evidence: [S14, S20, P12]
```

| Field | Rule |
|---|---|
| `id` | `snake_case`, unique, not `view` (reserved). |
| `description` | 1–3 sentences, at most 300 characters. **This is the text the decision model reads** for the option, so it describes the question shape, not the commands. |
| `triggers` | Lexical classifier patterns, case-insensitive, matched on word boundaries. May contain `{date}`, `{year}`, `{entity}` (a resolved mention), `{document}` (a document name from the overview). |
| `needs` | What must be resolved for the steps to be fillable, from: `entity`, `two_entities`, `document`, `date`, `class`, `store`, `insights_snapshot`. A route whose needs are unmet is still a valid answer, but its steps are not emitted and confidence is low. |
| `steps`, `fallbacks` | Command templates. Placeholders: `{domains}`, `{entity_id}`, `{entity_ids}`, `{entity_name}`, `{other_entity_id}`, `{document}`, `{date}`, `{year}`, `{class}`, `{question}`, `{view}`, `{view_params}`. Any other placeholder fails validation. |
| `read` | Reference files, relative to the skill folder; each must exist. |
| `evidence` | At least one scenario (`S<n>`) or pattern (`P<n>`) id. A route with no evidence fails validation. |

Validation (unit tests, run in the hermetic suite): unique ids; every placeholder known; every
command and every `--option` in a template exists in the Click tree (the `cli_guide` technique);
every `read` file exists; `evidence` non-empty.

v1 routes, 17 plus the `view` pseudo-route:

| id | Question shape | First step | Evidence |
|---|---|---|---|
| `existence` | Did X happen / any reference to X | `pattern7 --searchTerm {entity_name}` | S25 |
| `enumeration` | All / every / recorded X | `pattern7 … --limit 50`, raise until not `truncated` | S24 |
| `aggregate` | How many / total / average, no view fits | `text2cypher` (executed, not dry-run) | S3 |
| `superlative` | Favourite / most / top / least | `insights show` salience, else executed `text2cypher` count; say it is inferred | S22 |
| `entity_profile` | Tell me about X | `entity-context --entityId {entity_id}` | S10 |
| `entity_history` | History of X, what was said about X over time | `entity-history --entityId {entity_id}` | S5 |
| `time_history` | Which version applied on D / earlier version | `pattern10 --includeHistory`, `document-history` | S14, S20 |
| `conflicts` | Do documents disagree / which one applies | `query graph conflicts`, then the supersession-notice chunk | S13 |
| `hierarchy` | Who is under X / chain | `query graph hierarchy` | S11 |
| `path` | How are X and Y related | `pattern6`, fallback `pattern5 --mode shortest` | S12 |
| `document` | Summarise / what does document D say | `pattern10 --documentName {document}` | S16 |
| `inventory` | What documents of kind K exist | `domains-overview` (its per-domain document lists), then `pattern1 --entityClass {class}` when classes are asked for | S18 |
| `procedure` | How do I / steps / checks across documents | `vector-text` over the parent domain, then `pattern10` per named document | S19 |
| `structured` | Numbers in tables, alone or with policy | by `store.kind` (`text2sql`, `resolve-key`, `db sql` + `vector-text`) | S7–S9 |
| `insights` | What changed / who matters / lost touch | `insights list`, `insights show` | S6 |
| `filing` | Notes tagged X / in project Y | `query graph filing-listing` | S17 |
| `search` | None of the above: a factual question over text | `vector-text` over `selected` domains | S2-like default |

**Review:** the 17th route, `search`, is the explicit default, so the classifier always has a
non-specific answer to give instead of forcing a wrong specific one. Its steps are the current
skill's generic retrieval.

**Review: no per-vault route overrides in v1.** Routes are packaged and versioned with the code.
A vault that needs a different route for a recurring shape should save a view, which is the
existing per-vault mechanism.

### 4.4 The decision-model endpoint

**Configuration** (`~/.artmind/config.env` or the run folder's `config.env`; a real environment
variable overrides, as for every artmind setting):

| Variable | Meaning | Default |
|---|---|---|
| `ARTMIND_DECISION_URL` | Full endpoint URL, e.g. `http://localhost:11434/v1/systemone` (Ollama) or a Clef endpoint | unset → lexical |
| `ARTMIND_DECISION_MODEL` | Model name, e.g. `nimble`, `tev1` | required when the URL is set |
| `ARTMIND_DECISION_API_KEY` | Bearer token, for hosted endpoints only | unset |
| `ARTMIND_DECISION_TIMEOUT` | Seconds per call | `5` |

Names are artmind-specific, so Claude Code's exported variables cannot redirect them (the
`ANTHROPIC_BASE_URL` problem, CLAUDE.md §5).

**Call 1** (always; questions omitted when not needed):

```json
{ "model": "nimble",
  "state": { "question": "<the user's question>" },
  "questions": {
    "domain": { "type": "choice",
                "instructions": "Which knowledge domain holds the answer to the question?",
                "criteria": { "personal_journal": "Personal journal: 1,687 documents …; classes PERSON, LOCATION, EVENT …",
                              "banking": "All banking.* domains: policies, SOPs, guides …" } },
    "view":   { "type": "choice",
                "instructions": "Which saved query answers this question exactly? Choose none if no saved query fits.",
                "criteria": { "trips_by_year": "<summary> Examples: <examples, one per line>",
                              "none": "No saved query answers this question." } },
    "route":  { "type": "choice",
                "instructions": "What kind of question is this?",
                "criteria": { "<route id>": "<route description>", "…": "…" } } } }
```

- `domain` is omitted when `--domain` is given; `view` is omitted when the candidate domains
  have no views. With no `--domain`, `view` offers the views of every domain (the catalogue is
  small); the chosen view's domains must cover the chosen domain, otherwise the view is dropped
  and a warning says so.

**Call 2** (only when the chosen view has entity, enum or bool params to decide):

```json
{ "model": "nimble",
  "state": { "question": "<the user's question>" },
  "questions": {
    "param_person": { "type": "choice",
                      "instructions": "Which candidate is the traveller in the question?",
                      "criteria": { "c1": "Diarist (PERSON): the journal's narrator, the 'I' of the notes",
                                    "c2": "<name> (PERSON): <first 120 chars of description>",
                                    "none": "None of these" } },
    "param_kind":   { "type": "choice", "instructions": "<param description>",
                      "criteria": { "<value>": "<value>" } } } }
```

Twins are presented as **one** option (they share a name) and expanded to every twin afterwards.

**Response handling:** for each question, read `answers[name].choice`, `probabilities` and
`confidence`. A missing answer, a choice not among the options, or a non-numeric probability →
the whole call is redone with the lexical classifier (`fell_back: true`, `reason: bad_answer`).

**Privacy (Review):** the payload holds the question, domain and class names, view summaries and
candidate names and descriptions. With a local Ollama endpoint nothing leaves the machine. With a
hosted endpoint (Clef on Workers AI, hosted Jev) all of it does, as it already does today for
text2cypher with a remote LLM. The spec states this; artmind does not try to block it.

**Lexical classifier** (same `Decision → Answer` interface, no network): a route scores one
point per matched trigger, plus a bonus when its `needs` are met; a view scores by token overlap
between the question and its summary and examples, with capitalised words, numbers and
stopwords removed from both sides; scores are normalised to probabilities with a fixed `none`
mass, so a question matching nothing comes out low-confidence rather than as a forced pick.
Entity params are assigned by class when exactly one candidate of the class exists, otherwise
`needs_disambiguation`.

### 4.5 Per-domain plan config: `<ARTMIND_HOME>/domains/plan/<domain>.yaml`

Optional, never seeded by `setup` or `init`, outside `domains/schemas/` so `schema_set_hash()`
never moves (the insights-config pattern). Read on every call.

```yaml
narrator:
  entity_class: PERSON
  name: Diarist
first_person: [i, me, my, mine, myself]    # optional; this is the default
confidence_threshold: 0.7                   # optional; 0 < t <= 1
classifier: auto                            # optional: auto | lexical | decision
decision_model: nimble                      # optional; overrides ARTMIND_DECISION_MODEL
```

| Rule | Behaviour |
|---|---|
| Unknown key, wrong type, threshold out of range | Error naming the file and key; `query plan` fails for that domain rather than silently ignoring the file. |
| `narrator` given but no entity of that class and exact name in the domain | Warning; no narrator rule; first-person words are not mapped. |
| No file | Defaults; no narrator rule. |
| `classifier` key | Applies only when `--domain` is given (§4.1); a plan that chooses its own domain uses the endpoint setting. |
| Domain families | A file for `banking` applies to every `banking.*` domain that has no file of its own; the most specific file wins, never merged. |
| Several selected domains with different thresholds | The highest threshold applies (the most cautious). |

`artmind plan config check [--domain D]` validates the files and, with Neo4j up, that each
narrator exists.

### 4.6 The labelled set: `<vault>/benchmarking/plan_labels.yaml`

Private: holds real questions, committed only to the vault's own repository, never to the
checkout. Written by hand or drafted by an agent session and confirmed by the user.

```yaml
version: 1
benchmark_file: benchmarking/questions.md     # optional; enables `benchmark:` references
labels:
  - id: L001
    question: "…"                    # exactly one of question | benchmark
    source: trace:ae812980           # benchmark | trace:<8-char id> | written
    domains: [personal_journal]
    route: view
    view: trips_by_year
    params:
      person: Diarist
      place: [CITY, CITY]            # a list = any of these candidates is right (twins)
      year: 2025
    expect: high                     # optional: high (default) | low (the plan should abstain)
    notes: "P26 question"

  - id: L014
    benchmark: Q07                   # question text taken from the benchmark file
    source: benchmark
    domains: [banking]
    route: time_history
    view: null
    expected_documents: [policies/policy_complaints.md]   # used by benchmark grading only
```

| Field | Rule |
|---|---|
| `id` | `L` + 3 or more digits, unique. |
| `question` / `benchmark` | Exactly one. `benchmark` must name a qid parsed from `benchmark_file` by `benchmark._parse_questions`. |
| `source` | `benchmark`, `trace:<id>` (the trace it was taken from), or `written`. |
| `domains` | Expected scope. Correct when the plan's `selected` equals it, or is its family parent when the label lists every sibling. |
| `route` | A route id or `view`. |
| `view` | View name or `null`. Must exist in the vault when not null. |
| `params` | Only for `view`. Entity params by **name** (resolved at replay, so ids may change after a rebuild); a list means any of the names is right; scalars as literals; omitted params are not scored. |
| `expect` | `low` marks a question the plan should not be confident about (S22-type inference, an ambiguous area question). |
| `expected_documents` | Optional, for benchmark grading; replay ignores it. |

`artmind plan labels validate [--file PATH]` checks every rule above, and with Neo4j up that
each entity param name resolves in the label's domains.

**Replay scoring** (`artmind plan replay [--classifier lexical|decision|both] [--model M]
[--repeat N] [--compact]`), per label:

| Outcome | When |
|---|---|
| `correct` | Plan confidence `high`, and domain, route, view and every scored param match. |
| `abstained` | Plan confidence `low` while the label expects `high`. Not counted as wrong. Reported separately: the agent falls back to the routing table, which is today's behaviour. |
| `correct_abstain` | Plan `low` and the label says `expect: low`. |
| `wrong` | Plan `high` and anything scored does not match. **The number to drive to zero.** |

Report: counts per outcome, per route and per backend; for each wrong label, the field that
differs; classifier latency p50/p95; with `--repeat N`, every label whose plan differed between
runs (the determinism check). The summary prints to stdout; per-label detail goes to
`<ARTMIND_HOME>/logs/plan-replay-<timestamp>.json` (gitignored, private), never into the
checkout.

**Review: v1 gate.** On both vaults' labels: zero `wrong` on view-answerable labels (S1, S2,
P26); `wrong` under 10% overall; every label identical across `--repeat 4`. The classifier default
(`lexical` vs `decision`, and which model) is whichever has fewer `wrong`, then fewer
`abstained`.

### 4.7 Review summary

The choices most worth a second look, in order of consequence:

1. `view` is a separate classifier question, not a route option (§4.2 `route`).
2. High confidence fills `steps`; low confidence fills only `suggested_steps` (§4.2).
3. `abstained` is not counted as wrong; the gate is on `wrong` (§4.6).
4. Free `string` and `list` view params are never guessed (§4.2 `view_match`).
5. The executed view's rows sit at the top level for tracing (§4.2 `executed`).
6. `--domain` is optional for this command alone (§4.1).
7. A hosted decision endpoint receives the question and candidate names (§4.4).
8. No per-vault route overrides; views are the per-vault mechanism (§4.3).
9. A parent domain's plan config applies to its children unless they have their own (§4.5).
10. `search` is an explicit default route (§4.3).

## 5. Honest commands

The plan emits these commands, so they must not mislead the agent that runs them. Each change is
small, independent and lands before the plan (§9).

### 5.1 H1 — `--dry-run` reads as "nothing ran" (P20)

- `text2cypher` (`text2cypher.py`, `execute_text2cypher`) and `text2sql`
  (`text2sql.py`): a dry-run payload no longer carries `"rows": []`. It carries
  `"dry_run": true` (as today) and `"next": "<the same command without --dry-run>"`.
- `traces/path.py`: a span whose recorded `args` include `dry_run: true` is a `discovery`
  span (not a graph attempt, not unproductive). The layer check gains the span's args, not
  only its command name.
- The skill's one line "run `--dry-run` first" gains "then run it" (the only skill edit in
  this spec; see §9).

### 5.2 H2 — the temperature refusal is learned once per machine (P20 side effect)

- `llm_providers._no_temperature` (in memory, per process) is backed by
  `<ARTMIND_HOME>/logs/llm_capabilities.json`: `{"<endpoint>|<model>": {"no_temperature": true,
  "learned_at": "<iso>"}}`. Read on first use, written atomically (temp file in `logs/`, rename).
- Entries older than 30 days are ignored, so a provider that changes behaviour is re-probed.
- The warning is logged only when a refusal is first learned, not on every process start.
- A missing, unreadable or corrupt file is treated as empty and never raises.

### 5.3 H3 — `pattern7` reports truncation and provenance (P21, P24)

- Top-level `rows_total` (fulltext hits in the domains, counted up to 1,000;
  `rows_total_capped: true` when there are more), `truncated` (`rows_total > len(rows)`) and,
  when truncated, `next: "raise --limit (rows_total=N)"`.
- Each row gains `documents`: up to 3 `{id, name, path}` and `document_count`, found via
  `(e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)-[:PART_OF]->(:Document)`
  within the requested domains.
- The default `--limit` stays 10, so existing callers see the same rows plus new fields;
  `--limit` is bounded to 1–500 (per-row document lookups scale with it).

### 5.4 H4 — history reachable from the graph (P12)

1. **`supersedes`.** Document rows returned by `pattern10`, `chunks` and the `vector-text`
   document block gain `supersedes: [{id, name, path}]`, computed at query time with
   `OPTIONAL MATCH (d)-[:SUPERSEDES]->(older)` over `:Document` and `:DocumentHistory`. No new
   stored property: `superseded_by` stays derived by `supersession_records`, and so is this.
2. **`--includeHistory` on `pattern10`.** Explicit flag with today's `--asOf` behaviour (also
   reach `:DocumentHistory` / `:DocChunkHistory`). `--asOf` stays as an alias; when it is used
   the payload carries a warning that on `pattern10` it is a history switch, not a date filter,
   and the help text says the same.
3. **Retired-only matches.** `pattern10` without the flag, no current match, but matching
   retired documents: zero rows, `history_matches: [names]` (up to 5) and
   `next: "add --includeHistory"`. One extra count query, run only when the main query returned
   nothing.
4. **`document-history` name matching.** Exact name, path or id as today; else, when exactly
   one document's name equals the input plus a known suffix (`.md`, `.pdf`, `.docx`, `.txt`,
   `.html`), that document is used and a warning names it; else zero rows, `closest` (up to 5
   names containing the input, case-insensitive) and `next`.

## 6. Shadow mode

**Purpose:** measure the plan against real agent turns before any agent is told to follow it.

**Where:** the two traced surfaces. The admin console, right after `TraceRecorder.start` in
`webui/app.py` (the `/api/chat` stream), and benchmark runs, at the same call in
`benchmark.py`. The chat UI records no traces, so it gets no shadow mode (§2).

**How:**

1. `shadow.start(question, recorder)` launches `planner.plan(question)` in a worker thread.
   `question` is the user's own text (`payload.prompt`), never the replayed conversation.
2. `TraceRecorder.finish` waits for it until 10 s after the plan started, then adds one
   trace event, `type: "plan_shadow"`, payload `{status: ok|timeout|error|skipped, followup:
   bool, plan: {…}, error: str|null}`. A plan over 60 KB is trimmed (`"trimmed": true`) so the
   stored JSON stays valid; a payload still cut by `store.cap` reads back as `truncated`.
   `skipped` means 8 shadow plans were already running. A plan still running at that point is
   written by a background thread when it finishes, up to 90 s after the turn (`LATE_WAIT_S`),
   else as `timeout`; the turn and the next one never wait for it. A process that exits first
   (a benchmark run's last question) records no event.
3. It is an event, not a span, so `traces/path.py` never counts it as agent work.
   `public_event` never forwards it to the browser; the agent never sees it.
4. `followup` is true when the session has an earlier traced turn.

**Guarantees:** it never delays the agent (it runs alongside) and never fails a turn (every
exception becomes `status: error` and a warning log line; recording it can never cost the
turn's other events). Nobody waits on a shadow plan, so its decision-model calls use
`ARTMIND_PLAN_SHADOW_DECISION_TIMEOUT` (default 60 s) instead of `ARTMIND_DECISION_TIMEOUT`:
measured on a local 9B decision model, a question it has not seen costs 6-15 s of prompt
processing, which the interactive timeout would turn into a lexical fallback. Kill switch: `ARTMIND_PLAN_SHADOW=0` in `config.env`; default on; the
test suite pins it off. With a hosted `ARTMIND_DECISION_URL`, every traced question is sent
there for classification.

**Reading it back:**

- `traces show` prints the shadow plan beside the agent's route.
- `traces stats` gains a `plan_shadow` block per bucket: a tally of every shadow status, turns
  with a plan, share at high confidence, and **agreement** (the plan's first traced `query` step,
  normalised to command plus view name, appears among the turn's spans other than `query plan`;
  an executed plan counts as running its view), split by confidence level, with the first/
  follow-up and rating splits computed on high-confidence plans.
- `traces triage` gains the signal `plan_disagreed_high`: a high-confidence plan the agent did not
  follow, on a turn that ran at least one other `query` command. These are the turns to review
  first.

## 7. Error handling

| Failure | `query plan` | Shadow mode |
|---|---|---|
| Neo4j unreachable | exit 1, as every `query` command | `status: error` |
| Decision endpoint times out, HTTP error, unusable answer | that call redone lexically; `classifier.fell_back`, warning | same |
| Decision endpoint down repeatedly | per-process circuit breaker: after 3 consecutive failures, skip it for 60 s | same |
| Decision model not loaded (cold start) | `serve`, `chat-ui` and `admin-ui` send one tiny warm-up request at start (background thread, 300 s, outside the breaker; `ARTMIND_DECISION_WARMUP=0` turns it off); it only loads the model, since a new question is never cached | same |
| One mention's resolution fails | mention dropped, warning, confidence lowered | same |
| A view in the catalogue is invalid | skipped, warning (as `views list` reports it) | same |
| Plan config invalid | `ClickException` naming file and key (§4.5) | `status: error` |
| Routes file invalid | caught by hermetic tests; at runtime a `ClickException` naming the route | `status: error` |
| `--execute` view run fails | exit 0, `executed.status: error` with the message | never executes |
| Old `serve` daemon without `plan` | proxied call fails with Click's "No such command"; restart `serve` | in-process, unaffected |
| Labels file invalid | `labels validate` lists every error with its label id; `replay` refuses to run | — |
| One label errors during replay | outcome `error`, counted separately; the rest continue | — |

## 8. Testing

**Hermetic** (`test/`, no Neo4j, no network; CLAUDE.md §3: assert the Cypher and parameters
actually sent, never summary counts alone):

| Area | Tests |
|---|---|
| `routes` | Every route validates; ids unique; every placeholder known; every command and `--option` in a template exists in the Click tree; every `read` file exists; `evidence` non-empty. |
| `config` | Valid file; each invalid case named; family inheritance (most specific wins); missing file → defaults. |
| `candidates` | Mention extraction on synthetic sentences (capitalised, quoted, first person, dates); entity resolution called with the right domains, and only exact/fulltext hits kept; twins grouped by class and casefolded name. |
| `classify.lexical` | Trigger scoring, `none` mass, view token overlap; identical output on repeat. |
| `classify.jev` | A local fake HTTP server records the request: body shape for call 1 and call 2 matches §4.4; timeout, HTTP 500, a choice not among the options → lexical fallback with the right `reason`; circuit breaker opens after 3 failures and closes after 60 s (clock injected). |
| `fill` | Each param type of §4.2's table; `string` never invented; `missing_param`; twins → combinations, capped at 4. |
| `planner` | With a fake classifier: every payload key always present; `high` vs `low` (steps vs suggested_steps); `--execute` only on a high, single-combination view; executed rows lifted to top level; `confidence.weakest`. |
| `labels`, `replay` | Each validation rule; `benchmark:` references resolve; scoring outcomes on synthetic plans; `--repeat` difference detection. |
| H1–H4 | Dry-run payload has no `rows` and has `next`; dry-run span is discovery in `path.py`; capability cache read/write/expiry/corrupt file; `pattern7` Cypher and parameters for count, documents and slicing; `supersedes` in the three document queries; `--includeHistory` ≡ old `--asOf`; retired-only `next`; `document-history` suffix and `closest` paths. |
| Shadow | A fake planner: `plan_shadow` event recorded; timeout and exception paths; never in `public_event` output; kill switch; `traces stats` agreement on synthetic spans. |
| CLI | `COMMAND_GROUPS` routing; `query plan --help`; plugin fixtures regenerated if any CLI output the plugin reads changed (`test_plugin_fixtures.py`). |

All test text is synthetic: no corpus names, questions or answers (the checkout is public).

**Live probes** (from the checkout, `ARTMIND_VAULT=<vault> ARTMIND_NO_PROXY=1`, or `serve`
restarted):

- brain01: the P26 question, four times → four identical plans, route `view`, narrator
  resolved, both place twins in `steps`.
- banking_corpus: Q10's document without `--includeHistory` → `history_matches` and `next`;
  `document-history` without the suffix → matched with a warning; `supersedes` on the current
  version's row; a truncated `pattern7`.
- With `ollama pull nimble` (and `tev1`) done by the operator: the same probes with
  `--classifier decision`.

**Replay gate** (§4.6), on both vaults' labelled sets, for `lexical`, `nimble` and `tev1`:
zero `wrong` on view-answerable labels; `wrong` under 10% overall; every label identical
across `--repeat 4`. The default classifier is the one with the fewest `wrong`, then the fewest
`abstained`.

## 9. Rollout

1. **H1, H2, H3, H4**: one commit each, independent of each other and of the plan. The skill's
   dry-run line and the `pattern10` flag name are the only skill edits.
2. **`query_plan` with the lexical classifier**: routes, config, candidates, fill, planner, CLI.
3. **`classify.jev`**: the decision backend.
4. **Labels and replay**: `plan labels validate`, `plan replay`. Then a drafting session per
   vault: an agent drafts labels from `04-scenarios.md`, the benchmark and the traces into
   `<vault>/benchmarking/plan_labels.yaml`; **the user confirms each label** before it counts.
5. **Replay gate**, then choose the default classifier.
6. **Shadow mode** on the admin console and benchmark runs; one week of normal use, then read
   `traces stats` and `traces triage`.
7. Then the skill wire-in and diet (study W3), as its own spec, using the shadow numbers.

Pulling decision models (`ollama pull nimble`, `ollama pull tev1`) is an operator step; artmind
never downloads a model itself.

Docs updated with the code (CLAUDE.md §4): CLAUDE.md's repo layout (a row for
`artmind/query_plan/`) and command routing quick reference (`query plan`, `plan …`); the `query`
group docstring; `docs/INSTALL.md` for the `ARTMIND_DECISION_*` and `ARTMIND_PLAN_SHADOW`
settings; a `justfile` recipe `plan-replay VAULT`.

## 10. Open items

- Whether the Jev API's `noul` type is a plain probability for a yes/no question in every
  implementation (Ollama documents it; Clef's announcement uses it without defining it). Checked
  against both before relying on it for `bool` params; until then `bool` params use their default.
- How many labels are enough. Target at least 40 across both vaults, covering every route that
  has evidence; S10–S12 and S17 (no evidence yet) are written labels and stay so until traces
  exist.
- Agreement in shadow mode compares first steps only. Whether the answer was right needs a
  rating or a graded benchmark; the stats split by rating where one exists.

## 11. Addendum (2026-10-23): the domain catalogue

**Problem.** The planner reads graph metadata fresh on every plan and uses little of it: the top
8 classes and the first 25 document names per domain (from `domains-overview`), all document
names (one more query), and one name query per mention. Mentions come only from capitalised or
quoted text, so an entity written in lower case ("the gym", "dinner") is never resolved; classes
beyond the top 8, entity aliases, the schema's class descriptions and relationship types are not
used at all.

**Design.** A per-domain catalogue, `artmind/query_plan/catalogue.py`, built by a few
deterministic Neo4j reads plus the domain schema on disk, and cached.

| Part | Source | Bound |
|---|---|---|
| `classes` | `MATCH (e:Entity)` per domain: `entity_class`, count; plus the schema's `entity_types.<CLASS>.description` | all classes |
| `relations` | `RELATES_TO` `rel_type`, count, within the domain | top 50 by count |
| `documents` | live and retired document names (moves here from `candidates.document_names`) | all |
| `names` | entities' `_id`, `name`, `aliases`, `entity_class`, `observation_count`, ordered by `observation_count` desc then name | `NAME_INDEX_MAX` = 20,000 per domain |

A parent domain's catalogue is the union of its children's; each leaf is cached on its own.

**Freshness.**
- One cheap fingerprint query per plan covers all selected domains: per domain, entity count,
  document count and history-document count, plus `:ProjectionState.last_rebuilt_at`.
- A cached catalogue is reused while its fingerprint matches. Within `FINGERPRINT_TTL_S` (10 s)
  of the last check the check itself is skipped, so a burst of plans costs one query.
- `MAX_AGE_S` (15 min) forces a rebuild even when the fingerprint matches. An incremental ingest
  can keep the counts equal (an edit that renames an entity), and the age bound is the safety net.

**Cache layers.**
1. In memory, per process: `serve` and `admin-ui` are long-lived.
2. On disk at `<ARTMIND_HOME>/logs/plan-catalog/<domain>.json`, written atomically with its
   fingerprint, so one-shot CLI calls and `plan replay` reuse it. It holds entity names, so it
   lives only in the gitignored, private `logs/`.

`ARTMIND_PLAN_CATALOG=0` disables both layers: every plan builds the catalogue live and caches
nothing. Tests pin it off unless a test opts in.

**What the planner uses it for.**
1. **Name mentions (gazetteer).** After `find_mentions`, word sequences of 1–6 tokens in the
   casefolded question are matched against catalogue names and aliases, normalised as
   `match_document` does.
   - The longest match wins, and matches overlapping an existing mention are skipped.
   - A single-token match must be at least 4 characters, not a stopword or question word, and
     belong to an entity with `observation_count` ≥ 2.
   - A hit becomes a mention with `via: "catalogue"` and candidates already filled, with
     `matched_by: ["catalogue"]`; no name query runs for it.
2. **Resolution.** Only a `catalogue` mention takes its candidates from the catalogue (entries of the
   selected domains; one with none there is dropped). A capitalised or quoted mention resolves through
   `entity_name_candidates` as before, so it keeps fulltext neighbours, the evidence rule and the description.
3. **Class words.** `_class_word` matches against every catalogue class, not just the top 8.
4. **Domain option text.** Up to 15 classes by count and the top 5 relationship types join the
   text the classifier reads for each domain option.
5. **Documents.** `match_document` reads the catalogue's document names.

**Payload.** A new top-level key, additive:

```json
"catalogue": {"<domain>": {"source": "memory|disk|live", "built_at": "<iso>", "names": 12345, "truncated": false}}
```

`truncated` is true when the name index hit `NAME_INDEX_MAX`; then names past the cap fall back
to the per-mention name query.

**Failure.** If the catalogue cannot be built (Neo4j down), the planner behaves as before this
addendum, with a warning. Disk-cache read and write errors are never fatal.

**Determinism.** The catalogue's contents are sorted. A plan built from a cached catalogue equals
one built live, apart from `catalogue.source`/`built_at` and the classifier latency, which replay
strips from its fingerprint.
