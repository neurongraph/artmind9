# Survey passage depth: more than one passage per document

Status: design, 2026-11-12. Work plan item H (retrieval gaps), banking first.
Evidence: study note `docs/query-routing-study/22-p2-1-orient.md`, "Retrieval autopsy".

## 0. Problem

Banking loses most of its unread must items inside documents the turn already returned. A brief survey
shows each passage-row document's single best passage, and the agent's way to open a document,
`survey --expand <doc_id>`, takes its passage count from `--passages`, default 1. In the banking benchmark
turns, 150 of 1,226 brief surveys used `--expand` and none raised `--passages`, so every expand returned the
passage the row already showed.

Of 50 missed items from runs `9a027799`, `ca13f53b`, `70560495` and `26ce7d08` (facts in the corpus, in no
chunk the turn returned), 35 sit in a returned document. Re-running each turn's survey under the current code:

| The fact's passage inside its document | in a passage row | in a gist row | not in the brief list |
|---|---|---|---|
| rank 2–3 among the document's matching passages | 6 | 3 | 0 |
| rank 4–8 | 10 | 2 | 0 |
| not among the matching passages | 4 | 7 | 3 |

Twenty-one of the 35 match the question. Showing more of a document's matching passages can reach them. The other
14 need different search wording (not this design).

## 1. Changes

### 1.1 `--expand` opens the document

`survey --expand` returns up to **5** matching passages per named document when `--passages` is not given.
An explicit `--passages N` still wins. Without `--expand`, `--passages` keeps its default of 1. In the CLI,
`--passages` defaults to `None` and resolves to 5 with `--expand` and to 1 otherwise. `survey_search` keeps
`passages=1` as its default, and the CLI passes the resolved value. Expanded rows are unchanged
(`brief.passage_row`, 1,200 characters each). An over-budget payload is cut by `_echo_json` as now, with
`next` pointing to `query page`.

### 1.2 Passage rows carry the document's next passages

A brief survey passage row gains `more_passages`: the document's next matching passages, after `passage`, in fused
order (`doc_units[doc_id][1:]`, one per chunk), up to `--morePassages` (0–4, default **2**). Each entry is
`{passage_id, chunk_id, text, relevance}`, with `text` cut to `brief.SECOND_PASSAGE_CHARS` (400) by
`cut_keep`. The key is absent when the document has no further matching passage or the row did not get them.

Allocation (`brief.allocate`, spec 2026-11-05 §4.2) gains one tier between the passage floor and the gist
floor:

1. passage rows (needles first, then rank, up to `--passageDocs`), as now;
2. **`more_passages` for those passage rows, in the same order, each only while the budget allows** (new);
3. the gist floor (`--gistDocs`), then more gists while the budget allows, as now.

A `Candidate` gains `more_chars`, the size difference between the row with and without `more_passages`. The
upgrade is all or nothing per row: if a document's extras don't fit in the budget, it keeps the plain passage row.
Tier 2 comes before the gist floor on purpose. At the agents' 24,000-character budget, a 50-document brief survey
already fills about 23,000, so after the gist floor the extras would never fit. The cost is about 2,900 characters
(3 rows × 2 × ~480), roughly four gist rows that become line rows. §2 measures that trade.

`--morePassages 0` restores today's rows exactly. Full shape is unchanged: `--passages` already gives it N passages.

### 1.3 Skill and help text

- `artmind-query` SKILL.md, the survey paragraph:
  - a passage row's `more_passages` are further passages of that document, and count as evidence;
  - `--expand <doc_id>` shows up to 5 matching passages of the document.
- `query survey` docstring and option help: mention `more_passages`, `--morePassages`, and `--expand` with 5 passages.
- `artmind-query-p2b` gets the same lines, through `scripts/split_p2b_skill.py`.
- Regenerate `references/commands.md` (`just dev-skill-commands`).
- Regenerate the plugin fixtures only if a fixture's command is `survey`.

## 2. Measurement

**Offline gate (no LLM).** Re-run every survey of the 50-item set under the new code with
`ARTMIND_OUTPUT_BUDGET=24000`, for `--morePassages` 0, 2 and 3, and count:

- **visible:** missed items whose target chunk appears in a shown passage (`passage` or `more_passages`). The baseline is 0 by construction.
- **gists lost:** gist rows per survey compared with `--morePassages 0` (median and max).
- **payload size:** stays ≤ the budget (no `_echo_json` cut).

Keep default 2 if it makes at least 5 items visible and costs a median of 4 or fewer gists. Otherwise pick the
setting the numbers favour, and record the choice in study note 22.

**Benchmark.** Then run one classic run per vault on restarted consoles, graded as usual. It passes when banking
improves on runs R and X (59, 62) and brain01 stays inside the band. Brain01 is a guard: its losses are breadth,
which this design does not address.

## 3. Tests

- `test/test_brief.py`:
  - tier 2 upgrades passage rows in needle order;
  - a row whose extras would exceed the budget stays plain;
  - `--morePassages 0` gives today's allocation;
  - gist rows are given out only after tier 2.
- `test/test_survey_cli.py`:
  - `--expand` without `--passages` asks for 5;
  - an explicit `--passages 2` wins;
  - `--morePassages` is validated (0–4) and passed through;
  - `more_passages` entries come from `doc_units[1:]` in order and are cut to 400 characters.
- Contract tests (`test_cli_conventions.py`, `test_skill_commands_md.py`, `test_skill_references.py`) stay green.

## 4. Not in scope

- Neighbour chunks (the option set aside on 2026-11-12).
- Query wording per part, and brain01 breadth (the next design).
- `vector-text` rows.
