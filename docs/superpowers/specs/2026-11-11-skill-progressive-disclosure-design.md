# P2b: artmind-query with references on demand (2026-11-11)

Status: **built, measured, not promoted (2026-11-12).** `artmind-query` keeps the orient text; `artmind-query-p2b`
stays as an unpromoted variant (contract `p2b`) with its `commands.md`. The `guide` key stays on results with no
`warnings` line (the live skill lacks most references); `temporal` fires only on non-empty history results. Gate
results: study note 22, "P2b full gate, second time". Work plan item E of
`2026-11-08-classic-baseline-and-skill-simplification-design.md` (§4.5). Before E2 (turn check).

## 1. Why

`artmind-query` (the orient text, promoted 2026-11-11) is 512 lines and loads whole every turn. Most of it is
detail that matters only when a turn reaches it: the graph model for Cypher, hub drilling, survey options, mart
caveats, conflicts. The goal is a SKILL.md of about 170 lines that every turn reads, plus references the agent opens
when the turn reaches their subject.

Opening a reference must not depend on the agent remembering to. Run L showed that a fact on the result (the mart
lower-bound warning and its survey `next`) moves scores; run M showed that a rule the agent is asked to follow (the
check-off list) does not. So each reference has a trigger, and where the trigger is something a result shows, the
result itself names the reference.

## 2. Two kinds of trigger

1. **Question shape, before any command runs** ("how many", "what changed", "every year"): a column of the routing
   table in SKILL.md names the reference.
2. **What a result shows, after a command runs** (a hub, twins, a replaced document, mart rows, conflict rows, Cypher):
   the payload carries `"guide": ["<key>", ...]`, and SKILL.md says that `guide: X` means read `references/X.md` of
   this skill before going on.

Results carry a key, never a path: the agent starts in the vault root (ACP), `<vault>/.artmind` (Claude SDK) or
`~/.artmind`, so no relative path is right everywhere, and a key resolves against whichever copy of the skill was
loaded. The key is the reference file's stem.

## 3. SKILL.md

| Part | Lines | From |
|---|---|---|
| Frontmatter | 5 | unchanged |
| Every turn | 10 | verbatim |
| Orient | 35 | verbatim |
| Plan the parts | 31 | verbatim |
| Read every result | 29 | verbatim, plus two lines on `guide` |
| Running commands, Grounding, Required inputs | 14 | kept; Running commands names `references/commands.md` |
| Routing table: question shape → command → reference | ~22 | Retrieve's table, Store routing, Fallback ladder |
| Judgement rules | ~10 | sibling domains on thin results; search before concluding absence; survey for spans of documents; when to say the data does not answer |
| Answer style, provenance | ~15 | condensed from 17 |

About 170 lines. 150 was a target, not a cap; the verbatim sections are most of it.

## 4. References

| File | Holds | Reached by |
|---|---|---|
| `graph-model.md` (new) | Fixed Structural Schema, Discover | `guide: graph-model`; routing row |
| `resolve.md` (new) | Resolve; twins and narrator rules | `guide: resolve` |
| `hubs.md` (new) | Hub entities, Ground | `guide: hubs` |
| `survey.md` (new) | Survey, Topic plans | `guide: survey`; routing row |
| `views.md` (new) | Views | routing row; orient peel 2 lists the views |
| `insights.md` (new) | Insights, Catalogue and assets | routing row |
| `composites.md` (new) | the mart paragraph and composites | `guide: composites` |
| `conflicts.md` | existing, plus Adjudicate | `guide: conflicts` |
| `temporal.md` | existing | `guide: temporal`; routing row |
| `structured-store.md` | existing, plus Store routing | routing row |
| `hierarchy.md` | existing | routing row |
| `commands.md` (generated) | every option of the `query` and read-only `db` commands | Running commands |

Retrieve's table becomes the routing table; each per-command note moves to the reference its row names, and a note
that fits none becomes an example in `artmind/cli_guide_examples.json`, so it reaches `commands.md`. Every line of
the current text lands in exactly one place (§6, moved-text test).

## 5. Code

### 5.1 `artmind/guides.py`

Stdlib only.

- `GUIDES`: the ordered keys `hubs`, `resolve`, `temporal`, `composites`, `conflicts`, `graph-model`, `survey`. Order
  is precedence when several apply.
- `guides_for(command_path, payload) -> list[str]`: pure; at most two keys, in `GUIDES` order.
- `reads_mart(sql) -> bool`: the `__mart_` test, shared with `text2sql`'s existing warning and `next` in `cli.py`.

| Key | Command | Payload shows |
|---|---|---|
| `hubs` | `query entity-context` | `hub: true` or a `cut` block |
| `resolve` | `query entity-resolve`, `query views run` | `twins`, or status `needs_disambiguation` / `no_match` |
| `temporal` | `query vector-text`, `query survey`, `query chunks` | a row (or its `doc`) with a non-empty `replaced` list (`brief.replaced_refs`) |
| `composites` | `query text2sql` | rows, and `reads_mart(generated_sql)` |
| `conflicts` | `query graph conflicts` | any rows |
| `graph-model` | `query graph text2cypher`, `query graph pattern*` | `generated_cypher`, or any pattern result |
| `survey` | `query survey`, `query topics run` | `coverage` partial (`at_least`, or `shown` < `matching_documents`), `periods` present, or `--shape brief` rows |

Partial results (`complete: false`, `truncated`, a budget cut) get no `guide`: the rule is in "Read every result",
which stays in SKILL.md, and `next` already names the closing command (`--all`, `query page`, `survey`).

### 5.2 Where `guide` is set

Only in `_echo_json` (`artmind/cli.py`): it calls `guides_for` before `envelope()` and adds `guide` only when the list
is non-empty, so a payload with no trigger keeps its shape and no command changes. `serve` runs the same Click path,
so proxied calls carry it. A `guide` a command set itself is kept (none do today).

Envelope: `guide` is an optional key after `command`, `warnings`, `next`. `test/test_cli_conventions.py` gains one
assertion: when present it is a list of `GUIDES` keys.

### 5.3 Traces

`traces/evidence.summarize` copies `guide` into the span's evidence (JSON; no schema change) and `traces show` prints
it. E's gate (§7) and E2 read it there.

### 5.4 `commands.md` from the Click tree

`artmind/cli_guide.py` gains `render_markdown(prefixes, read_only_db)` beside `render_fragment`, reusing
`build_sections`, `get_panels`, `collect_params` and `load_examples`, so the admin console's CLI guide tab and
`commands.md` come from one walk and cannot disagree. Per command: heading, first help line, a table of arguments and
options (flags, type, default, required, help), examples.

Scope: everything under `artmind query`, plus the read-only `artmind db` commands the traces count as discovery or
sql (`bridge`, `list`, `schema`, `sql`, `timeline`, `review`). No write, ingest or operator groups.

`artmind/skills/<skill>/references/commands.md` is checked in; `just dev-skill-commands` regenerates it.

## 6. Tests

- `test/test_guides.py`: per key, a synthetic payload that fires it and one that does not; a payload matching three
  signals returns two keys in `GUIDES` order; `_echo_json` adds no `guide` key when nothing fires.
- `test/test_skill_references.py`:
  - every `GUIDES` key has `references/<key>.md`;
  - every reference file is reached by a key or a routing-table row;
  - every routing-row reference exists;
  - an AST scan finds no `"guide"` key assigned outside `guides.py`.
- `test/test_skill_commands_md.py`: the checked-in `commands.md` equals `render_markdown(...)`; on mismatch the
  message says to run `just dev-skill-commands`.
- Moved-text test, replacing the prose pins in `test_query_orient_skill.py` and `test_query_skill_views_step.py`:
  `MOVED = {phrase: file}` checks each pinned phrase is in its new home and in no other file of the skill; the four
  verbatim sections equal the current text byte for byte; "every `artmind` command named exists" covers the
  references too.
- Plugin fixtures: regenerated only if a fixture payload now carries `guide`, then `just obsidian-plugin-test`.

## 7. Measuring it

The new text is built as `artmind/skills/artmind-query-p2b/` (contract `p2b`); `artmind-query` keeps the orient text
until P2b passes. The `guide` code lands with it, always on: the current skill never mentions `guide`, and no classic
runs are made during the measurement (L, M, N are the comparison and predate the change). `artmind init` links the
new folder into both vaults.

Two `p2b` runs per vault, graded with the usual baselines and question files (brain01 runs asked for first).

Passes when, per group (banking; brain01 Q01–Q14; brain01 Q15–Q29), all four hold:

| Check | Pass |
|---|---|
| Must-item score | inside or above the L/M/N band, and at or above the floor (67 %, 55 %, 76 %) |
| Commands per turn | median no higher than L/M/N |
| Partial result followed by its `next` | rate no lower than L/M/N |
| Cross-document questions that ran `survey` or `topics run` | rate no lower than L/M/N |

Reported, not gating: time and cost per turn; per key, how often `guide` appeared and how often the agent read that
reference in the same turn (a `Read` of the file in the turn's events); skill lines loaded per turn.

**Pass:** `artmind-query-p2b` replaces `artmind-query`; the orient text is archived at
`docs/archive/artmind-query-orient/`; tests move to the promoted path; the variant folder and its vault links are
removed (vault deletions asked first); `cd ~ && artmind setup` refreshes `~/.artmind`; the runs go into the study
notes, counts and ids only.

**Fail:** the variant stays unpromoted; the gate rows say which check it missed. The `guide` code stays (harmless,
and E2 reads it).

## 8. Commit order

1. `guides.py`, its wiring in `_echo_json`, traces, tests.
2. `render_markdown`, the `just` recipe, `commands.md`, its test.
3. The `p2b` skill and references, the sync and moved-text tests.
4. Spec and work-plan updates, study notes.

## 9. Non-goals

No planner (`artmind/query_plan/` stays frozen); no change to what commands return beyond the optional `guide` key;
no enforcement that a `guide` is read (that is E2); no change to the four verbatim sections.
