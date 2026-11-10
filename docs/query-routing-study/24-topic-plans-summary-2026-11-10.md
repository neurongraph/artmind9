# 24 — Topic plans for breadth: what was built, measured, left (2026-11-10)

Spec `docs/superpowers/specs/2026-11-10-topic-breadth-design.md`, built on `baseline/v0.11-classic` in seven
commits, `e15890fa` … `69740267`. Not pushed. Full suite 6,748 passed. Counts only (README privacy rule).

## What changed

| Step | Commit | What |
|---|---|---|
| 1 | `e15890fa` | `survey` `coverage` fact; topic plans (`domains/topic_plans/<slug>/{plan.yaml,SKILL.md}`, view rules: read on every call, version guard, atomic save); `query topics plan [--list]`, `query topics run` (proxied, traced as survey, budgeted rows); `topic plan validate`; pages leave agent surfaces (`query topics list/show` → `topic list/show`; assets and orient list plans); `:TopicPage` graph copy, `topic index`, its sweeps and setup indexes removed, with a migration (run in both vaults: banking 5 nodes dropped). |
| 2–3 | `2765ddd8` | Faceted `topic build` (`--facet`, `--by/--split auto` from orient facts, `--perCell`, `--maxDocs` 150/300, fair cut, dry-run grid with cost); concurrent map with a map cache; reduce per subject; page Overview / subject sections / Timeline / Where sources differ; plan written beside the page. Orient peel 2 `levels` (catalogue part, n = p90 of observation counts); suggested subjects; climb hints; the one plan rule in `artmind-query-orient`; new `artmind-build-topic` skill (admin console); web tool gate refuses page reads. |
| 4 | `30984795` | Freshness check (generation short-circuit, grid re-count, changed/retired sources); section reduce cache, so a refresh re-reduces only changed subjects; daily background check, opt-in auto-refresh in the admin console under `ARTMIND_TOPIC_MAX_COST_USD`; admin console Topics tab. |
| 5 | `269ed077` | `traces compare` lists and flags turns that ran a plan matching their question. |
| 6 | `5f80e4b8` | Members facet (§6.4): discovery in the dry run, `--member` subjects with aliases, the anchors' cell, plan `members/discover/dropped`, new candidates in freshness. |

Deviations from the spec, each deliberate: `query topics run` orders rows by rank across cells (not grouped by
cell), so a budget cut keeps every cell's top documents; brief run passages are 500 characters; `max_docs` caps
cell slots (map calls), not distinct documents; the page nests child domains as `###` under one `##` per subject.

## What was measured (§11)

| Item | Result |
|---|---|
| 1 Gather, Q08 | 16 / 22 dated events with keyword-style facets (target ≥ 19 not met); one-word facets 7, question-style 14 |
| 2 Plan run, Q08 | 10 / 22 events in the first 37 of 68 rows under the 24k budget; per subject 8 + 3 |
| 3 Agent with plan, Q08 | partial, 3 / 7 must missed (run I 5, baselines 6–7); flagged "with plan" |
| 4 Page text, Q08 | 3 / 7 must found |
| 5 Banking Q15 | gather 5 / 5 key documents; run 4 / 5 (5 / 5 per subject); agent with plan 3 / 6 missed (= best baseline) |
| 6 The offer, Q08 without a plan | not met: 2 reruns reported match counts but made no offer (rule sharpened once) |
| 7 Members, Q05 (no LLM) | 94 candidates hold 6 of 8 expected places; gather 9 / 16 visits (discovered members), 13 / 16 (all) — target ≥ 14 not met |
| 8 Freshness | hermetic tests only; live check reports `fresh` on both plans |

## What is left

- Measurements that need an LLM build or a new note in brain01 (waiting on a yes): the Q05 members plan, its
  plan run and a Q05 rerun with the plan; Q11 and Q16 (same shape); §11 freshness live (add a matching journal
  day, expect `stale`, `new_documents: 1`, one map call plus that subject's reduces).
- The offer rule does not fire; it needs a different trigger than skill wording (e.g. making `coverage` part of
  the "Read every result" checklist), decided with the user.
- Keyword counts drive cell depth and undercount entries that use other words (§13): Q08 and Q05 both stop
  short of target for that reason. A vector-leg count, or a floor on `take`, is the next lever.
- Banking's whole-plan Q15 page (150k characters) is too long to grade or read; long split pages may want a
  per-domain cap.
- Dead code: `topics/anchor.py` and the old `gather.gather` are no longer called by `topic build`.
