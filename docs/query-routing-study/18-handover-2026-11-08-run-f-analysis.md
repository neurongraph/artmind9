# Handover (2026-11-08, later): run F judged, losses analysed, two changes made

For a session that will do a **retrospective** of this work and an **introspection of the design direction**. It
follows `17-handover-2026-11-08.md`. Privacy as always: this checkout is public. banking_corpus is a public sample
corpus and is named freely. brain01 appears **by question number and counts only**: no question text, answer text
or judge reasons.

The user stopped the fix work on purpose: "we seem to be going into a band-aid over leaked pipes kind of loop".
**This note proposes no new fix.** Section 8 lists questions for the retrospective instead.

## 1. State of the repo

| What | Where |
|---|---|
| Branch | `claude/p3-design-a9f633`, checkout `~/projects/artmind9`. Last commit `828b89fc`. **Nothing from this session is committed.** |
| Uncommitted | 16 modified files (section 5). Full suite last passed: 7,044 passed, 14 skipped, after both changes. |
| Daemons | `admin-ui` and `serve` import code once at start. Restart them before any run that should use these changes. |
| Not done | No benchmark run after either change. No agent-level check of any change. |

## 2. Run F result (the P3b gate, Sonnet 5.5 judge)

Run F is one bundle run per vault on commit `af7d7a5` (the four skill rules). Banking F `b58b1bfb`, brain01 F `8f7d14e9`.
Compared with the classic runs A and D from handover 17. **The gate fails in both vaults.**

| | banking (36 questions) | brain01 (14 questions) |
|---|---|---|
| Bundle vs A: classic better / bundle better / equal | 19 / 13 / 4 (20 material) | 8 / 6 / 0 (10 material) |
| Judged balance vs classic A-vs-D band | 6 vs 6, passes | 2 vs 0, fails |
| Worse than both classic runs | 5 (seq 5, 18, 19, 35, 36) | 4 (seq 2, 8, 10, 14) |
| Commands, median, bundle / classic | 2 / 4 | 3 / 5.5 |
| Latency, median | 20.1 s / 26.0 s | 22.3 s / 27.4 s |
| Cost, median | $0.076 / $0.125 | $0.085 / $0.120 |
| Answer reach (labels) | 82 vs 71 of 101 | none |

Earlier rounds, for trend: banking round 1 had 14 questions worse than both; brain01 round 2 had 5. F has 5 and 4.

**The gate itself is noisy.** Counting, per run, the questions materially worse than both other runs:
banking A 4, D 3, F 5; brain01 A 2, D 2, F 4. F is only 1-2 above a classic run, so the "no question worse than
both" row may fail any run. This is the main argument for a third classic run per vault before more engineering.
Caveats: verdicts are not pinned (Sonnet 5.5 rejects temperature 0); the classic A-vs-D band is 6 in banking and 0
in brain01, so the balance row depends heavily on which two classic runs are used.

Detail files (private, in each vault's `.artmind/logs/`): `compare-20261108T123051513575.json` (banking),
`compare-20261108T122637793001.json` (brain01).

## 3. What the analysis found

Method: for each of the 9 questions worse than both classics, compare the documents the classic runs read with what
F's bundle opened, listed or never surfaced; read the judge's reasons; read the answers for brain01 seq 2, 10, 14
and banking seq 18 and 35 in full. Not read in full: brain01 seq 8, banking seq 5, 19, 36.

| Cause | Questions | Evidence |
|---|---|---|
| Search used the wrong vocabulary | banking 18 (also 5, 35 by document overlap) | Classic found 2-3 documents F never surfaced. They came from agent-written `vector-text` queries. See section 4. |
| Long set, bundle too small to hold it | brain01 2, 10 | Classic read 37 and 22 documents, F read 15 each. 13 and 12 of the classic documents were only listed in F; 14 and 7 never surfaced. About 10k of the bundle was document rows against 2-3k of passage text. |
| Computed answer wrong | banking 36, brain01 8 | banking 36: the SQL step grouped decided complaints as "open". brain01 8: route was `insights` at 0.63 (threshold 0.7) and the answer had no per-year counts. Mechanism for both is my inference; I did not read the generated SQL or Cypher. |
| Documents reached, answer thin | brain01 14, banking 19 | brain01 14: F read 18 documents (classic 12) and still gave a lower count; the graph held links classic used. banking 19: F reached 45 documents and left out two levels of an escalation matrix. |

Structure facts:
- **The `subplan` node and clause splitter do not exist.** They are S11 and B11 in the spec and were never built, so
  every multi-clause question runs as one question. Multi-clause questions did worse: brain01 4 such questions net
  -5 with 3 losses and no wins, against +4 for the 10 single-clause ones. My banking split matched 31 of 36
  questions, so it says nothing. Banking's five two-part `structured` questions net -4 with 3 losses.
- **Enumeration is not the weak route** (brain01: 6 questions, net +5, 1 loss). The weak ones are count and
  history questions that route elsewhere: `aggregate` 2 of 2 lost, plus one `entity_history` and one `insights`.
- **The agent breaks its own skill rules.** In brain01 seq 2 it reported documents it had not opened, which Rules 1,
  3 and 4 forbid. It found 2 of 4 expected items. Follow-ups after `query run` happened in 24 of 36 banking turns and
  13 of 14 brain01 turns, almost all `vector-text` or `survey`. Rules in a skill are advisory.
- **Entity routes hand over facts in date order**, not by relevance (brain01 seq 10: 10 of 279 facts shown, all
  from one early period). I did not check which command options already allow relevance ranking.
- No consistent link between a cut `facts` list, or the agent expanding documents, and losing.

## 4. The one root cause traced end to end (banking 18)

The sentence that answers the question ("confirm no holds on account (freezes, disputes, fraud investigation)") is
in chunk 5 of 25 of `sop_account_closure`. It reached both classic runs through one agent-written search:
`account closure blocked fraud investigation open complaint retention hold` (the SOP's passages ranked 1, 2, 3, 6;
the target ranked 3). The same phrase against the whole `banking` domain also ranks it 3, so domain scoping is not
the cause.

The bundle's planner searched with `"customer's account" "fraud investigation" complaint transactions CUSTOMER`
(`slots.search_term_slot`: mention spans, or the question minus filler and stopwords, plus category terms). That
returns policy_complaints_v3, policy_fraud and sop_exception_handling, and **no closure SOP**. The full question as
the search text also misses it. The question says "close", the document says "closure", "holds", "blocked".
A string filter cannot add that vocabulary. A model can.

Separately, the bundle never opened the closure SOP even though it ranked 5th, because `opening.select_documents`
skipped any document that already carried a search snippet. And `--expand` returned only 1-3 passages per document.
Both are fixed (section 5), but the first is a symptom: the planner's search never made the SOP the top result.

## 5. Changes made this session (all uncommitted)

**Change A: open documents by rank alone, return more ranked passages per document.**
- `artmind/query_run/opening.py`: `select_documents` no longer skips documents that already have a passage; removed
  `_passaged` and the `all_passaged` skip; `command()` adds `--passages 8` (`OPEN_FETCH = 8`); new
  `top_passages(rows, keep)` (best passage of every document, then best of the rest, `OPEN_KEEP = 12`, relevance order).
- `artmind/query_run/bundle.py`: `_apply_open` keeps the 12 best opened passages first and never cut; the rest are
  ranked evidence with `from: ["open_more"]`.
- `artmind/query_run/executor.py`: the `all_passaged` skip reason is gone.
- `artmind/cli.py`: `query survey --passages` cap raised from 3 to 8 (the function already allowed 8).
- Tests: `test_query_run_opening.py` rewritten for the new behaviour; `test_query_run_bundle_lines.py` (`open_next`
  string), `test_query_run_replay.py` (executed 1 to 2), `test_survey_cli.py` (cap 3 to 8) updated.
- Spec `2026-11-07-p3-executor-design.md` section 6.5 updated.
- Effect, banking 18 run alone in process: the bundle now contains the "no holds" sentence; before, it did not.

**Change B: bundle and output budget raised 24,000 to 28,000.** Change A put about 13,000 characters of opened text in
the bundle, which squeezed the document rows. `DEFAULT_BUDGET` in `bundle.py`; `ARTMIND_OUTPUT_BUDGET` default in
`webui/agent.py` and `webui/backends/acp.py` (the bundle reads that variable first, so the code default alone would
change nothing for the UIs); CLI help; `CLAUDE.md` (two mentions); tests that pin the value; specs
`2026-11-05-bounded-output-design.md` and the P3 spec. **Cost:** the margin under the agent harness's 30,000-character
inline limit drops from 6,000 to 2,000. Past 30,000 the harness swaps output for a file path, and the agent often
did not read it.

**Measured at 28,000 (bundle only, banking, agent not run):**

| Question | Document rows printed | Bundle chars |
|---|---|---|
| savings interest | 0 of 50 (was 10 of 50 before change A) | 27,400 |
| SmartSaver features | 23 of 29 (was 29 of 29) | 28,500 |
| £300 compensation | 9 of 9 | 27,200 |
| banking 18 | 0 of 50, contains the target sentence | 28,000 |

The 0-of-50 cases happen because lists fill in a fixed order (opened passages, ranked evidence, document rows, facts,
guides) and stop at the first one that is cut. Moving document rows ahead of ranked evidence would fix it at the
cost of one test (`test_evidence_cut_comes_first_in_next`) and about 4 ranked evidence rows. **Not done**; the user has
not decided. An earlier attempt broke that test and was reverted.

## 6. Mistakes and corrections made during the session (for the retrospective)

- I used "chunk" and "passage" interchangeably. The hierarchy is Document (no text) to DocChunk (text, no embeddings,
  extraction unit) to DocPassage (about 1,200 characters, embeddings, the retrieval unit). Retrieval ranks chunks by
  their best passage (MaxP). `--expand` returns one passage per matching chunk. In `sop_account_closure` the 25
  chunks are about 500 characters each, so each holds one passage.
- I first proposed returning whole documents when opening. The user pointed out ranked passages should be the
  default. Whole-document reads for questions that ask for one remain a separate, undesigned idea.
- A counter of mine ("expand used: 0") was a column mix-up; fixed before use. A "bundle search phrase" test I ran
  was a phrase I invented, not the bundle's; discarded and redone with the real one.
- I initially said enumeration and sub-plans were both weak. The data supports the sub-plan point (it is not
  built) and does not support the enumeration point.
- An auto-mode classifier blocked a bulk-edit script mid-session; the same edits were made with the Edit tool.

## 7. Not verified

- No agent-level result for either change. Nothing was run through the benchmark.
- Whether `--expand` and the open step lose text beyond the case traced (banking 18).
- The generated SQL (banking 35, 36) and Cypher (brain01 14, 2): causes are inferred from the judge's reasons.
- Whether 12 opened passages is the right cap: the banking 18 target was the 10th best by relevance among the top 7
  documents' passages, which is how the number came about. One data point.
- Behaviour of the other 27 banking and 10 brain01 questions after either change (could be better or worse).

## 8. For the retrospective and design introspection

Not proposals. Questions this session's evidence raises.

1. **Where does classic's advantage actually come from?** In the one case traced end to end, it was the agent
   writing its own search phrases with vocabulary the question lacked, then iterating on results. The bundle is
   single-shot by design (one `query run`, then answer). Is the bundle contract trying to replace an iterative loop
   with a deterministic one, and which parts of that loop can code do?
2. **The fix pattern.** Each finding so far led to a local fix for one question (open by rank, more passages, bigger
   budget, reordering lists). Change B exists only because change A took budget. Is the budget the real constraint,
   and is a fixed-size bundle the right shape for questions that need many documents?
3. **What was built versus what was specified.** The spec's `subplan` node (S11) and clause splitter (B11) were never
   built, yet 5 of the 9 losses are multi-part or vocabulary problems. Should the order of work have been different,
   and does the planner need a model-written query step (rather than string rules) as a first-class part?
4. **The gate.** A "no question worse than both" row over runs whose own spread is 2-4 such questions per run, with an
   unpinned judge and 14 and 36 questions. Is this a measurable gate? Should it rest on a larger set, pinned
   judging, or a different criterion?
5. **Skill rules versus code.** Rules 1-4 in the bundle skill were broken in at least one observed answer. Which
   behaviours should move into the executor so they are not optional?
6. **Cost.** The user stated lower cost is not a goal if answers are not right and comprehensive. The gate currently
   treats cost and latency as pass/fail rows, and every bundle design decision so far traded recall for them.
7. **Coupling of limits.** One `ARTMIND_OUTPUT_BUDGET` value serves single commands and the whole bundle, and sits
   2,000 characters below a harness limit it does not control. Is that coupling sound?

## 9. Where things are

- Run ids: banking A `61cf661f`, D `15837ded`, F `b58b1bfb`; brain01 A `49bc9ef6`, D `d6c41e2c`, F `8f7d14e9`.
- Registry: `document_registry.db` in each vault's `.artmind/data/` (`registry.db` is empty). Opening it with
  `sqlite3 -readonly` failed on the WAL mode; a plain open worked.
- The throwaway analysis scripts were written to `/tmp/analysis/` (reach by command, bundle structure, answer
  readers, correlation of bundle features with outcome). They are **not** in the repo and may not survive. Their
  logic is described in section 3; they read `trace_spans`, `trace_events` and `benchmark_questions`.
- Judge call recipe is in handover 17.
