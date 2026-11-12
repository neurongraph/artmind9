# Find in a survey's documents

Status: design, 2026-11-12. Work plan item H (retrieval gaps). Follows the parked passage-depth design
(`2026-11-12-survey-passage-depth-design.md`); evidence in study note 22 ("Retrieval autopsy", "Banking runs A and B").

## 0. Problem

Banking loses must items inside documents the turn already returned. In the autopsy's banking set, 35 of 46 missed
items sit in a document the survey returned. The fact's passage was not shown: the document was a gist or line
row (15), the passage ranked 5th–8th inside it (8), or it did not match the whole question (11 in all).
Showing more of the survey's own ranking did not help. `more_passages` and an 80,000-character budget both stayed
inside the band (A 63, B 59, against R 59 and X 62).

The ranking is by the whole question. The facts the answer lacks belong to one part of it: a code, an amount,
a deadline. The agent already writes those parts (`Parts:`) but has no way to look for one part inside the
documents a survey found, short of a new search over the whole domain.

## 1. Command

```
artmind query find <handle> "<one part, in the corpus's words>" [--top N] [--perDoc M] [--compact]
```

- `<handle>`: the handle of a brief `query survey` result (§2). A handle from any other command is a
  `ClickException`: "find searches a survey's documents; run query survey first". So is a pruned handle ("results are
  kept 24 hours; re-run the survey").
- Scope: **every passage of the survey's listed documents**, whether or not it matched the survey's question. That is
  `documents[].doc_id` in the stored payload, up to 50. It covers `DocPassage` and, for a superseded document,
  `DocPassageHistory`. The survey's domains bound it as well (`p._domain IN $domains`).
- `--top` (1–20, default 8): passages returned. `--perDoc` (1–5, default 3): at most this many from one document.
- Read-only, no LLM. One embedding call, for the part. Proxied by `serve` like every `query` command and traced
  (`TracedGroup`); layer `survey`.

### 1.1 Ranking

One Cypher read returns `id, chunk_id, doc_id, text, embedding` for the passages in scope (about 25 per document,
1,200–2,500 per call). They are ranked in Python by weighted RRF (`retrieval_rank`, vector 2 : keyword 1, as in
`vector-text`) over two legs:

- **vector:** cosine of the part's embedding (`vector_query.embed_question`) with each passage's `embedding`;
- **keyword:** the part's terms (`brief.question_terms`, stopwords dropped), with an idf weight computed over the
  passages in scope. A passage that contains one of the part's special tokens (`brief.special_tokens`: codes,
  amounts, percentages, dates, versions) sorts first within this leg.

Then `--perDoc` is applied in rank order, and the top `--top` passages are kept. Passages without an embedding (the
passage sweep not yet run) take part in the keyword leg only, with a warning naming the count.

### 1.2 Output

```json
{"command": "query find", "warnings": [], "next": null,
 "query_type": "find", "handle": "<survey handle>", "part": "...", "survey_question": "...",
 "domains": [...], "scope": {"documents": 50, "passages": 1240},
 "rows": [{"rank": 1, "passage_id": "...", "chunk_id": "...", "text": "<= 1,200", "relevance": 0.71,
           "matched": ["COM-POL-006", "notice"], "doc": {"id", "title", "domain", "date", "status"}}]}
```

- Rows are `brief.passage_row` with `matched` (the part's terms the passage contains). There's no full shape: the
  command exists for the agent.
- About 12,000 characters at the default 8 rows, under the budget; `_echo_json` cuts as usual if not.
- When no passage contains any of the part's terms, the rows are still returned (vector only), with a warning
  "no passage in the survey's documents contains the part's words; the rows match by meaning only".
  `next` is then `artmind query survey --domain <d> --shape brief "<part>"` (`hints.py`).

## 2. Survey handle

Every brief `query survey` (not `--expand`, not full shape) stores its whole payload with `result_store.store` and
carries `handle`, whether or not it was cut. Today a handle exists only on a cut payload. `query page <handle>`
works on it as now. The payload gains nothing else. On an uncut payload, `next` stays as the command set it. The
stored copy is what `find` reads: `query_type`, `question`, `domains`, `documents[].doc_id`.

`find` needs the survey's documents, not its text, so a stored payload that `_echo_json` cut is still enough: the
handle file holds the whole list.

## 3. Skill

`artmind-query` (and `artmind-query-p2b` through its survey reference), in the survey paragraph and the per-part loop:

> After a survey, look for each part inside the documents it found: `artmind query find <handle> "<the part in the
> corpus's words>"`. Use the part's own terms (a code, an amount, a role, a deadline), not the whole question.
> A `find` row is evidence. Run a new survey only when `find` finds nothing.

`references/commands.md` is regenerated, and the CLI guide routes `find` (`COMMAND_GROUPS`).

## 4. Measurement

**Offline (no LLM).** For each missed item of §0's banking set, take the turn's own survey handles. Rebuild each by
re-running the survey with the turn's arguments, and run `find` with the wording of each `query survey` /
`vector-text` the turn made. The agent's searches are its parts as written; the stored final answers record a
`Parts:` list in only 10 of 36 turns. Count the items whose target chunk is in a `find` row, against 0 now. An
upper bound is the 35 items in a returned document. No threshold: the number decides whether the benchmark is worth
its cost, and a count under 8 is reported before any run.

**Benchmark.** One banking classic run on a restarted console, compared with R, X, A and B (59–63). Then brain01
as a guard, with the user's OK, since it spends LLM calls there. Also checked: how many turns call `find` and how many
times per turn. A low use rate is itself the result.

## 5. Tests

- `test/test_find.py`:
  - the Cypher sent carries `$docIds` from the stored survey and `$domains`, over both passage labels;
  - ranking: a passage with the part's code ranks above a closer-by-meaning one without it;
  - `--perDoc` and `--top` hold;
  - a passage with no embedding still ranks by keyword, with a warning;
  - a non-survey or unknown handle is a `ClickException`;
  - `matched` lists the part's terms found;
  - no matches gives the warning and the `next` survey.
- `test/test_survey_cli.py`: a brief survey stores its payload and carries `handle` when not cut; full shape and
  `--expand` don't.
- `test/test_traces_evidence.py`: `find` rows give provenance (doc and chunk).
- The contract tests stay green: conventions (envelope, read-only), `test_cli_guide.py` routing,
  `test_skill_commands_md.py`, `test_skill_references.py`.

## 6. Not in scope

- `find` over a `vector-text` or `entity-context` result (survey only, until measured).
- Documents the survey did not return (outside domains, below the top 50), and brain01's breadth losses.
- Items read but not used (30 % of banking's misses).
- The parked `more_passages` branch, which is not reused.
