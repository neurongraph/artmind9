# B12: confidence guards for three shapes the decision model plans wrongly at high confidence

Written 2026-11-07, before P3 (`query run`). Findings: `docs/query-routing-study/16-round9-shadow-collection.md` §3, §6.
Pattern: B3a (`intent.instruction`, cap 0.5). Privacy: label ids and counts only for brain01 and nbs.

## 1. Problem

Round 9: the decision model's only wrong labels in replay are three confident plans (brain01 L038, L047; banking
L037). Lexical plans them low. On P3's fast path a wrong high plan answers from the wrong evidence.

## 2. Guards

A guard is a pure check on the finished plan's facts. When it fires, a stage `("guard", GUARD_CAP)` joins the
confidence stages (`GUARD_CAP` = 0.5, below every threshold, same as `INSTRUCTION_CAP`), so the plan is `low`,
`confidence.weakest` is `guard`, and the route and steps stay as `suggested_steps`. The payload gains
`confidence.guards: [{id, why}]` (empty list when none). Nothing is rerouted. Guards apply to every backend.

| Id | Fires when | Evidence | Label |
|---|---|---|---|
| `path.unnamed` | route `path` and both entities the program uses (`_used_entities` primary and second) were found only by the catalogue gazetteer (`via: catalogue`), none spelled out in the question (capitalised, quoted) and none the narrator | L038: "parents" and "kids" are group entities the gazetteer matched; the evidence needed was two relationships of the narrator | brain01 L038 |
| `follow_up_why.names` | intent `follow_up_why` and the question names 2 or more entities (non-first-person mentions) | L047 names four; the right plan resolves each one (`existence`-style), not a survey of the sentence. A single named entity (brain01 L024, `expect: high`) stays high | brain01 L047 |
| `conflicts.version` | route `conflicts` and the question carries a superseded-version cue (`old|older|previous|earlier|outdated|obsolete|superseded|replaced` + policy/version/procedure/document, `which version`, `current version`, `latest version`) | the question asks which of two versions to follow, which `time_history` answers; `graph conflicts` returns disagreements, not versions | banking L037 |

Cues live in `artmind/query_plan/guards.py` (one module: `GuardResult`, `evaluate(...)`), not in the planner. The
cap is applied in `planner.plan` next to the instruction stage. Reroute is not done: the evidence names no single
right route (`existence`, `time_history` and `entity_context` are each plausible), and a low plan hands the
agent the suggested steps anyway.

## 3. Follow-ups never used the previous turn (finding, small fix)

In brain01 the shadow plan was given the previous turn in every follow-up (`previous_given` true), yet
`previous_used` was false. Two causes:

1. `intents.refers_to_previous` fires only for context-only messages and for messages of at most 8 words with a
   pointing phrase. "Yes - do run a broader sweep as I have dealt with quite a few more" has 15 words and real
   content words, so the previous turn's rules never run and the plan searches for the sentence.
2. A `follow_up_why` takes its route from the previous turn (`forced_route`), but `previous_used` reports only the
   intent rules, so the flag undercounts.

Fix (small, included): (a) a message that opens with an affirmative (`yes`, `yeah`, `yep`, `ok`, `okay`, `sure`)
and carries an action word (`do`, `run`, `go ahead`, `proceed`, `continue`, `please`), at most 20 words, and no cue
of its own, counts as pointing back (rule 1 then inherits the previous offer or intent, as for "yes please");
(b) `previous_used` is also true when a previous turn's route was forced. Not changed: a long standalone question
that merely starts with "yes" and has no action word keeps its own text.

Addendum: a plan built from an inherited question (`effective_question` set, no saved view) gets guard
`inherited.question`, same cap. Found by replay: without it, brain01 lexical L046 inherited L045's high plan and was wrong.

## 4. Gates and tests

TDD with invented text. Per vault, before and after: replay `--classifier both --repeat 4`, route parity
(`p2a_plan_parity.py`), banking `--reach` (not below 76 of 101), the full suite. Target: decision-model wrong
labels 0 in brain01 and banking; no new wrong labels; no new confident lookups for non-lookup messages. The
fix in §3 changes L046's plan (it should now use L045's question); its label is `expect: low`, so no gate moves.

Baseline (before): the shared notes folder, `b12/before/` (replay JSON per vault).

## 5. Questions

1. Cap at 0.5 for all three (recommended), or a lower cap for `path.unnamed`?
2. Include the §3 fix now (recommended), or only report it?
