# 11 — What the P2 spec needs from the collection round

Written 2026-11-02. The P1a (`2026-11-02-cli-uniformity-design.md`) and P1
(`2026-11-02-catalogue-and-answer-assets-design.md`) specs are written; they need no trace
data. P2, the planner v2 of the architecture design (§5.3), stops here: each decision below
is settled by the analysis of the round in `10-collection-sop.md`, and the P2 spec is written
after it. Same privacy rule as this folder.

| # | P2 decision | Evidence that settles it | SOP shapes (§2) |
|---|---|---|---|
| N1 | Intent classes: are the six (retrieve, follow_up_format, follow_up_why, curate, update, meta) the right cut, and can lexical rules separate them? | Every non-retrieval turn's text vs its rating note; share of follow-ups that are format/why/new question | Operator / non-retrieval, Follow-ups |
| N2 | Asset match threshold: at what `assets.like` score does a view or topic page answer the question? | Questions rated good where a view or page was the right source, and ones where it was not | Topic page asked for by name, Count / aggregate (vaults with views), Survey |
| N3 | Coverage guard: do evidence-shape signals (concentration, level agreement, score drop, documents over a relevance floor; report 2026-11-03 idea C) separate needle from survey, and at what thresholds? | Rating notes naming missed documents, vs the documents the turn cited; the report's offline prototype of the signals on labelled runs (its §7 step 3); more survey and mixed labels (its §6) | Survey across documents, Named document |
| N4 | Identity guard: how often do twins or open same-as proposals decide an answer, and should the plan expand every twin or ask? | Notes naming a duplicate person/thing; turns with `twins` in the shadow plan | Count / aggregate, Enumeration, First person |
| N5 | Programs per route: which routes need a `par`/`best_of`/`union` program, and which stay one step? | Routes where good-rated turns used two kinds of evidence (graph + text) | Count / aggregate, Entity profile, History |
| N6 | Confidence threshold per vault and whether route confidence should weigh less than domain (`09` §4) | Shadow plans with ratings: wrong-at-high vs abstained | All |
| N7 | Lexical vs decision model per vault, and hosted vs local (`09` §4) | `plan replay --classifier both` on the labels drafted from notes | All |
| N8 | `route_stats` fields for the catalogue (P1 §9) | What N5/N6 actually needed to read | — |
| N9 | Whether `follow_up_why` should re-run guards with the missed item as a must-find (open question 26) | Follow-ups that corrected the previous answer | Follow-ups |

**Temporary setting for the round:** `query survey` returns 50 documents by default
(commit `1294fbd`, 2026-11-03; it was 10), so traces show how far down the ranking the useful
documents sit. It is an observation setting, not a chosen default. P2 sets the real default
(or replaces it with the evidence-shape coverage guard) from N3's answer, and reverts this.

**Ready to write P2 when:** the SOP's "done when" holds, the round is appended to
`02-trace-evidence.md`, and N1–N7 each have a recorded answer in `06-open-questions.md`
(an answer may be "not enough data; default X").
