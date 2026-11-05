# Bounded output: results the agent can read

Date: 2026-11-05. Status: **design for review** (step 1 of `docs/query-routing-study/13-pickup-2026-11-05.md`).
Answers the user's decision R7-10 (`06-open-questions.md`): fix how results reach the agent,
rather than shrinking survey. Inputs: round-7 lessons 2, 3, 5, 14, 37 and §2.1/§4
(`docs/query-routing-study/12-round7-lessons.md`), and measurements on this machine
(§1). Parent design: `2026-11-02-retrieval-curation-architecture-design.md` (updated with this
document's principle, §3 "bounded output").

## 0. In one paragraph

In round 7, 158 of 158 default `vector-text` outputs and 34 of 35 survey(50) outputs were too
large for the agent to see. The harness replaced each with a 2 KB preview and a file path, and
the agent mostly went on without reading the file (P34). Raising harness limits does not fix
this: the limits differ between harness versions and backends, a compact JSON file is one
line the `Read` tool cuts, and every result stays in context for the rest of the conversation. The fix is
in artmind: (1) a **brief row shape** for the retrieval commands the agent uses: every document
listed with title, date and ids, and the text shown for each chosen for the question (the
best-matching passage or sentence, never a fixed opening), with an eval gate that proves the
short form hides no answer the full form shows; (2) an **output budget**, applied in code,
so a result never exceeds what the harness shows inline; (3) a **result handle**, so the part
that did not fit can be paged with one `artmind` command the tool gate already allows; (4)
two **tool-gate repairs** for the free-form path. Harness settings are a secondary safety
margin, set but not relied on. Survey keeps its depth of 50 in the brief shape; the full
shape goes back to 10.

## 1. Facts this design rests on (measured 2026-11-05)

**Harness limits** (re-checked on this machine; lessons §2.1 measured the desktop on 11-04):

| Item | Value | Where checked |
|---|---|---|
| Inline Bash output, success | 30,000 **characters** default; beyond it a ~2 KB preview and a path under `~/.claude/projects/<cwd-slug>/<session>/tool-results/` | bundled CLI string `kJi=30000` |
| `BASH_MAX_OUTPUT_LENGTH` (env) | default 30,000, max 150,000 | bundled CLI 2.1.218 |
| `bashOutputMaxChars` (setting) | 4,000–128,000; replaces the env var when set | **only** in the installed CLI 2.1.304, **absent** from the SDK's bundled CLI 2.1.218 |
| Failed Bash command | ~10,000 characters, head and tail, no file | lessons §2.1 (not re-measured) |
| In-process SDK tool | `ToolAnnotations(maxResultSizeChars=…)` sent as `_meta["anthropic/maxResultSizeChars"]` (SDK 0.2.126 `__init__.py`); `MAX_MCP_OUTPUT_TOKENS` env present | SDK source |

Consequence: the setting the lessons proposed (`bashOutputMaxChars`) does nothing on the
SDK backend as installed, because `ClaudeAgentOptions` runs the bundled CLI unless
`cli_path` is set. The env var does work there. The ACP/opencode backend has its own limits,
not measured. **A budget set in artmind holds on every backend; a harness setting holds on one.**

**Output sizes** (chunks-only contracts domain, one needle question, `--compact`; sizes only):

| Command | Characters | Rows | Median row | Largest fields (median) |
|---|---|---|---|---|
| `survey` (default 50) | 211,383 | 50 | 4,372 | summary 1,704 · passages 1,241 · chunk_summary 1,059 |
| `survey --topDocs 10` | 45,150 | 10 | 4,481 | same |
| `vector-text` (default `--context chunk`, topK 5) | 42,033 | 5 | 8,380 | chunk 7,711 · document 480 |
| `vector-text --context passage` (topK 5) | 11,328 | 5 | 2,310 | chunk 1,641 · document 480 |
| `vector-text --context passage --topK 10` | 22,007 | 10 | 2,215 | same |

Consequence: survey at 10 is still 1.5 times the limit, so reverting the default alone does
not make survey readable. `vector-text`'s `document` object (480 characters) carries name,
paths and `supersedes`, but **no title and no date**. Survey rows carry a title, but no date.

**Token cost.** At about 4 characters per token, one 30,000-character result is about 7,500
tokens, re-sent on every later call in the conversation. A budget well under the harness
limit is a cost control as much as a visibility one.

## 2. Requirements

| # | Requirement | Lesson |
|---|---|---|
| R1 | No result an agent receives from a planned step exceeds the **step budget**; no `query run` bundle exceeds the **bundle budget** | 2, 37 |
| R2 | Every evidence row carries document id, title, domain, date (`valid_from`, or `null` stated as undated) and the id needed to read more (chunk or passage id) | 5 |
| R3 | Survey depth (50 documents) reaches the agent with every document listed; full text for the strongest matches, question-chosen text for the rest | 14 |
| R4 | Whatever the budget cut is reachable by one allowed command, with no file path and no `Read` | 2, 3 |
| R5 | A cut is never silent: `truncated`, `rows_total`, `rows_shown` and a `next` that names the paging command | P44, lesson 19 |
| R6 | Behaviour holds on both backends and from Claude Code shells; harness settings only widen the margin | §1 |
| R7 | The CLI's default behaviour stays as it is for existing callers (plugin, scripts), except survey's default depth (§6) | architecture §0 constraint |
| R8 | Every span records the characters returned and whether the harness persisted the output | 24 |
| R9 | Shortening never hides the part of a document that answers the question: short forms are chosen for the question, every listed document stays listed, and the eval shows no loss against the full shape (§4.1–4.3) | 14, user review 2026-11-05 |

## 3. Budgets

| Budget | Value | Why |
|---|---|---|
| **Step budget** (one command, agent-facing) | 24,000 characters | 6,000 under the inline limit (30,000). Measured 2026-11-05 on the contracts domain: brief survey(50) is ~23,000 (3 passage, ~10 gist, ~37 line rows; passage rows ~1,900, gist ~650, line ~250, `topics` ~1,200) and brief `vector-text`(10) ~16,500. A 16,000 budget left survey with 1 gist and 49 line rows |
| **Bundle budget** (`query run`, P3) | 24,000 characters | one result per question, same ceiling as a step |
| **Error budget** | 2,000 characters | a failed command gets only head+tail; the message must name the fix in its first line |

Budgets are in **characters**, the unit the harness counts. They are set by one env var,
`ARTMIND_OUTPUT_BUDGET` (characters; `0` = off), which the web UI backends set for their
agent processes (step budget) and which `query run` reads for the bundle (the same value
unless set as `ARTMIND_BUNDLE_BUDGET`). Unset means no budget: a human at a terminal and the plugin
see today's output (R7). Bad values are a `ClickException` naming the variable, as for the
other `ARTMIND_*` integers.

## 4. The brief row shape

A new option on the retrieval commands the agent uses, `--shape brief|full` (default `full`,
so the CLI and plugin are unchanged; the skill, `routes.yaml` templates and the executor pass
`brief`). Contract test: every command with `--shape` emits the brief keys below for every
row. Names follow the CLI conventions (camelCase option, envelope untouched, keys only added).

**Brief document row** (survey; one per document, ~350–400 characters):

```json
{"rank": 3, "doc_id": "…", "title": "…", "domain": "…", "date": "2018-06-01" | null,
 "status": "current" | "superseded", "score": 0.0487,
 "gist": "the sentence of this document that best answers the question, ≤ 220 characters",
 "gist_source": "passage" | "chunk_summary" | "document_summary",
 "matched": ["notice", "termination"], "hits": {"passages": 4, "chunk_summaries": 2},
 "best": {"passage_id": "…", "chunk_id": "…"}}
```

The `gist` is chosen for the question, not for the document (§4.1).

**Brief passage row** (`vector-text`, `entity-context` text legs, survey's top passages,
`chunks`; ~1,300–1,500 characters):

```json
{"rank": 1, "passage_id": "…", "chunk_id": "…", "text": "≤ 1,200 characters (the passage)",
 "relevance": 0.71, "doc": {"id": "…", "title": "…", "domain": "…", "date": … , "status": …}}
```

- A `gist` longer than 220 characters is cut by the rule summaries already use
  (`summary_tree.parse_summary`: last sentence boundary in the window, else last
  whitespace), so it never ends mid-word.
- `date` is the document's `valid_from`; `status` is `superseded` when a `SUPERSEDES` edge
  points at it (history documents never reach these indexes, so `retired` cannot occur).
- `doc.title` falls back to the name without extension, as survey's `coalesce` does today.
- `chunks --shape brief` adds `doc` to each row, which closes lesson 5 (`chunks` rows with no
  title) for every caller that asks.
- Full rows gain `title`, `date` and `status` too, as added keys (R2 applies to every shape).

**Survey in brief shape**: every one of the `--topDocs` documents is listed, always. Only how
much is shown for each document varies, in three row kinds, sized from §1's measurements
(ids 38 characters, titles ~65):

| Row kind | Shows | Characters each |
|---|---|---|
| **passage row** | the document's best passage in full (≤ 1,200) plus the document row's fields | ~1,900 (measured) |
| **gist row** | the brief document row above | ~650 (measured) |
| **line row** | `rank, doc_id, title, date, status, score, matched, hits` | ~250 (measured) |

Rows are given out within the budget (§4.2), not by fixed rank bands. A typical result for 50
documents is 3 passage rows, 10 gist rows and 37 line rows: 5,700 + 6,500 + 9,250 + ~1,500
envelope (mostly `topics`) ≈ 23,000, measured on the contracts domain. `--passageDocs` (0–10,
default 3) and `--gistDocs` (0–50, default 10) are targets the allocation meets when the
budget allows. The second vault (short journal titles) is still to be measured.

### 4.1 Making the short forms point at the answer

A short row loses knowledge only if it shows the wrong part of a document. Survey already
holds, per document, three pieces of evidence chosen *for this question*: the best-matching
passage (P leg, with its cosine `relevance`), the best-matching chunk summary (C leg), and the
document summary (D leg), plus how many passages and chunk summaries matched. The brief rows
use them instead of a fixed opening sentence.

1. **The gist is extractive and query-relevant.** Candidate sentences come from, in order of
   preference, the best passage, the best chunk summary, then the document summary. Each
   sentence is scored against the question: question terms after stopword removal
   (`sanitize_lucene_query(drop_stopwords=True)`, the same terms survey's keyword legs use),
   matched as lowercased words with a plural fold (no new stemming dependency), weighted by inverse document frequency within the result, plus
   one point for each name, number or date that also appears in the question.
   The gist is the highest-scoring sentence, copied verbatim; ties go to the earlier source. It
   is never generated, so it adds no LLM call and cannot state something the source does not.
   `gist_source` tells the agent whether it is evidence (`passage`) or a guide
   (`chunk_summary`, `document_summary`; architecture principle 3).
2. **The fallback is stated.** When no sentence matches a question term (a purely semantic
   hit), the gist is the first sentence of the document summary, which the reduce prompt
   already makes state the document's purpose (`DOC_PROMPT_VERSION` 3), and `matched` is
   empty, so the agent can see the match was only by meaning.
3. **Line rows still carry signal.** `matched` (the question terms the document contained)
   and `hits` (passages and chunk summaries that matched) show how strong a deep document's
   match is without its text. A document at rank 40 with `hits.passages: 6` is visibly worth
   opening.
4. **Opening a document is one call.** `survey … --expand <doc_id,…>` (new) returns passage
   rows for the named documents under the same question, without re-ranking.
   `chunks --shape brief --idList` reads a chunk. Both are budgeted.
5. **Relevance beats rank for space.** The allocation (§4.2) does not cut by rank alone.
6. **`vector-text` brief shows more of a document only when the match is spread out.** A
   brief passage row shows the best passage (1,200 characters) rather than its 7,700-character
   chunk. When `passages_matched` ≥ 2 for a chunk, the row adds the second-best passage's text
   up to 400 characters, so an answer split across two passages of one chunk stays visible.

### 4.2 Allocating rows within the budget

1. List all documents as line rows (the floor: no document leaves the list).
2. Upgrade to **passage rows**, in this order: every document whose best-passage relevance is
   within 0.03 of the top relevance (the needle rule of architecture §5.3 d, so a needle
   ranked 7th by fusion still shows its text), then ranks 1, 2, 3, … until `--passageDocs` or
   the budget is reached.
3. Upgrade the remaining documents to **gist rows** by rank, then by `hits` for ties, until the
   budget is reached.
4. No upgrade is made that would exceed the budget (passage slots go to the needle set first,
   best relevance first, but never more than `--passageDocs`). If even the floor exceeds the budget (very long titles, `--topDocs 50` across many
   domains), §5 pages the tail and says so.

The allocation is deterministic, so the same question gives the same rows, and replay and
eval can measure it.

### 4.3 Proving nothing is lost: the visibility gate

The ranking is unchanged, so `docs_covered@50` is identical by construction. What can change
is whether the **answer-bearing text** is in front of the agent. The retrieval eval
(`artmind/retrieval_eval/`) gains one metric and the build gains a gate:

- **`evidence_visible`**: for a needle label, 1 when the labelled chunk's text (its best
  passage, or a gist with `gist_source: passage` from that chunk) appears in the output; for a
  survey label, the share of labelled documents whose row shows text from a passage
  (passage row, or a gist with `gist_source: passage`) rather than a line row or a summary
  gist.
- Measured for three outputs on the same labels: **full survey(10)** (what the agent would
  see if a full result fitted, which in practice it does not), **brief survey(50)**, and
  **brief `vector-text`(10)**.
- **Gate for P1b:** brief survey(50) `evidence_visible` ≥ full survey(10) on needle labels, and
  no lower than full survey(10) on survey labels at depth 10. Run on both vaults' labels
  (`retrieval_labels.yaml`). If the gate fails, tune the gist scorer and the allocation
  first. Only then consider the ingest-side option below.
- **In traces (round 8):** `expand_calls` and `chunks` calls that follow a brief survey, per
  turn. Many drill-downs into line-row documents that then get cited show where the gist or
  the allocation is too thin.

**Ingest-side option, deferred:** a per-chunk list of key facts (names, numbers, defined
terms) written next to the chunk summary, to give the gist scorer better candidates. It needs
an LLM pass over every chunk (`ingest summarise` cost again), so it waits until the
visibility gate shows that extractive gists from passages and summaries are not enough.

**`vector-text` in brief shape** implies `--context passage` (lesson 2, fixes-now row 1).

## 5. The output budget and result handles

One mechanism in `_echo_json`, so every command inherits it, rather than per-command logic.

1. **Measure.** `_echo_json` serialises the payload once and knows its length (it also gives
   the span its `payload_chars`, R8).
2. **Within budget**: print as today.
3. **Over budget**: write the full payload to a **result file**
   `<ARTMIND_HOME>/logs/results/<handle>.json` (gitignored `logs/`, private, never
   committed; the handle is 12 hex characters), then print a cut payload:
   - the command's main list (`documents`, `results` or `rows`; one attribute per command,
     declared next to `WRITE_COMMANDS` as `LIST_KEY`) is cut from the tail until the payload
     fits;
   - the envelope gains `truncated: true`, `rows_total`, `rows_shown`, `handle`, and `next`
     is set to the paging command (`artmind query page <handle> --from <rows_shown>`);
   - one `warnings` line says what was cut and why ("output budget 24,000 characters:
     showing 31 of 50 documents").
   A payload with no list key that is still over budget (a huge single row) is cut field by
   field: string values over 2,000 characters are shortened with `…` and named in `warnings`.
4. **Page.** `artmind query page <handle> [--from N] [--rows K]` (new, read-only, proxied,
   budgeted itself) prints rows `N…N+K` of the stored list with the same envelope. It reads
   only `logs/results/`, so the tool gate passes it like any `artmind` command (R4), and it
   works on both backends.
5. **Lifetime.** Result files are pruned after 24 hours by the next write; `traces prune`
   also removes them. They hold private corpus text, like `logs/plan-catalog/`.

**Why in `_echo_json` and not in each command.** The commands already route every JSON
result through it (envelope, span evidence), so one cut rule covers the 160 commands and new
ones by construction. The cost: a cut by position is crude for commands whose order is not a
ranking. That is why the agent-facing retrieval commands also get the brief shape (§4): the
shape keeps most results under budget, and the cut is the backstop.

**Why not page inside the harness.** No built-in paging exists for tool results (lessons
§2.1), and a persisted file of compact JSON is one line, which the `Read` tool shows only in
part (to re-verify; the 2,000-character line cut held in earlier versions). A handle read by
an `artmind` command does not depend on either.

## 6. Survey's default: decision

Survey's ranking depth stays at 50, where coverage is measured (43 % at 10, 82 % at 50, P43).
The default changes by shape:

| Shape | `--topDocs` default | Reason |
|---|---|---|
| `brief` (agent, executor) | 50 | depth is what coverage needs, and one line per document is affordable |
| `full` (terminal, plugin, `topic build`) | **10** (revert of `1294fbd`) | 45,000 characters at 10 already; 50 was an observation setting |

`ARTMIND_SURVEY_TOP_DOCS` keeps overriding both. `topic build` passes its own depth and is
unaffected. The revert lands with the brief shape, not before it, so no collection round
runs with survey at 10 in full shape and no brief alternative.

## 7. Tool-gate repairs (free-form path)

The executor removes most composed command lines (lesson 1), but the reasoning path keeps
Bash. Two changes to `artmind/webui/tool_gate.py` and its hook in `agent.py`:

1. **Strip a leading `cd <dir> &&`.** When the rest is one allowed `artmind …` command and
   `<dir>` resolves to the agent's working directory or the vault root, the `PreToolUse` hook
   returns `allow` with `updatedInput` set to the command without the prefix (the SDK's hook
   output supports `updatedInput`). Anything else is refused as today. This turns 29 syntax
   failures' most common shape (P35) into a no-op instead of a lost turn.
2. **Allow reading the agent's own persisted results.** `Read` of a file whose real path is
   inside `~/.claude/projects/<slug of the agent cwd>/*/tool-results/` is allowed, by the same
   realpath segment check as skill reads. These files are the agent's own tool output (an
   `artmind` result), not vault content, so the grounding rule is untouched. This is a margin
   for results produced before the budget lands and for commands without a list key; with
   §5 in place it should rarely fire, and `traces stats` counts it (`persisted_reads`).

Pipes stay refused: a `| jq` or `| head` is exactly what the brief shape and the handle make
unnecessary.

## 8. Harness and SDK settings (margin, not mechanism)

In the claude-sdk backend's `agent_options`:

- `env["BASH_MAX_OUTPUT_LENGTH"] = "40000"`: works with the bundled CLI 2.1.218; gives the
  budgeted outputs a margin and catches commands that have no budget yet.
- `env["ARTMIND_OUTPUT_BUDGET"] = "24000"`: the budget of §3, read by every `artmind` the
  agent runs.
- `bashOutputMaxChars`: not set while the bundled CLI lacks it; when the bundled CLI gains it,
  set it to the same value and drop the env var (it would be ignored then anyway).

Not raised further: every character shown stays in context (§1, token cost). In the ACP
backend the same `ARTMIND_OUTPUT_BUDGET` goes into the agent's environment; opencode's own
output limit is measured in the spec and recorded here.

**In-process tool, deferred to P3.** An SDK `@tool` for `query run` with
`maxResultSizeChars` set would remove the Bash round trip and the file preview entirely. It
exists only on the claude-sdk backend, and `query run` does not exist yet. With the budget
and handle in the CLI, the tool would add speed, not readability, so P3 decides it with
latency numbers.

## 9. Instrumentation (lesson 24, needed by the P2 gate)

| Field | Where | Source |
|---|---|---|
| `payload_chars` | `trace_spans` | `_echo_json` length, before any cut |
| `shown_chars`, `cut_rows`, `handle` | `trace_spans` | the budget step (§5) |
| `persisted` | `trace_events` for the tool result | the SDK event mapper sees the harness's `<persisted-output>` marker in the tool result |
| `persisted_reads`, `page_calls` | `traces stats` | Read of a `tool-results/` path; `query page` spans |

The round-8 gate reads them: persisted share of retrieval steps under 5 % (from 96 %), and
`page_calls` per turn, which shows whether the cut point is too tight.

## 10. What changes where

| Change | Files | Size |
|---|---|---|
| `--shape brief` on `survey`, `vector-text`, `chunks`, `entity-context`; `title/date/status` on full rows; question-relevant gist scorer and row allocation (§4.1–4.2); `survey --expand` | `survey.py`, `vector_query.py`, `cli.py`, `cli_conventions.py` (`LIST_KEY`, shape contract) | medium |
| Output budget + result files + `query page` | `cli.py` `_echo_json`, new `artmind/result_store.py`, `cli_conventions.py` | medium |
| Survey default by shape (revert of `1294fbd` for full) | `survey.py`, `cli.py` | small |
| Tool gate: `cd` strip with `updatedInput`, persisted-result reads | `tool_gate.py`, `agent.py`, `test/test_tool_gate.py` | small |
| Backend env: `ARTMIND_OUTPUT_BUDGET`, `BASH_MAX_OUTPUT_LENGTH` | `agent.py`, `backends/acp.py` | small |
| Skill + routes use `--shape brief` | `artmind/skills/artmind-query/`, `query_plan/routes.yaml` | small |
| `evidence_visible` metric and the visibility gate (§4.3) | `retrieval_eval/metrics.py`, `runner.py` | small |
| Spans: `payload_chars`, `shown_chars`, `persisted` | `traces/`, `webui/agent.py` event mapper | small |
| Plugin fixtures | unchanged (default shape `full`, no budget unless the env var is set); regenerate only if full rows' added keys appear in a fixture | check |

Tests (hermetic): budget cut and handle round trip on a synthetic payload; `query page`
bounds; brief-shape keys on every command that takes `--shape`; gate rewrite and refusal
cases; `LIST_KEY` declared for every command whose payload has a list. A live check on one
vault per backend: a survey(50) brief and a `vector-text` run from the admin console, with the
span showing `persisted: false`.

## 11. Rejected alternatives

| Alternative | Why not |
|---|---|
| Revert survey to 10 and stop | Still 45,000 characters; loses the depth coverage needs (P43) |
| Raise harness limits to 128,000 | Not available on the bundled CLI; every character is re-sent each call; does nothing for ACP |
| Let the agent `Read` persisted files as the main path | Compact JSON is one line; two calls per result; ties artmind to one harness's file layout |
| A `--maxChars` option per command | The agent would have to choose it; the env budget is set once by the surface that knows its limit |
| Summarise results with an LLM before returning | Adds a model call and cost to every step, and guides are not evidence (architecture §3.3) |

## 12. Open for the spec

1. Measure brief survey(50) characters on both vaults (titles in brain01 are short, contract
   titles long) and fix `--passageDocs` and the gist length from it.
2. Measure opencode's (ACP) tool-output limit.
3. Confirm the `Read` tool's long-line cut on the installed CLI, which decides whether §7.2 is
   worth keeping once §5 lands.
4. Whether `entity-context` needs `--shape` in the first cut or only its text leg.
5. The gist scorer's weights and the 0.03 needle margin in the allocation, tuned on both
   vaults' labels against the visibility gate (§4.3); whether `vector-text` brief's second
   passage (§4.1.6) earns its characters.
