# 19 — Retrospective: is deterministic routing the right direction?

Written 2026-11-08, after handover 18 (run F judged). A review of every change since `v0.11.0` (about 150
commits: about 6,800 lines of code, 7,000 of tests, 9,600 of docs), the redesign specs, and study notes 01–18.
Same privacy rule as the rest of this folder: brain01 appears by counts only; banking_corpus is public.

**Verdict.** Deterministic routing that *replaces* the agent's retrieval reasoning is the wrong direction, and
the evidence said so before P3 was built. The command-level tools improved along the way are a separate matter;
they are what made classic better. Keep them; freeze the bundle contract.

## 1. The fact that frames the rest

The classic skill (`artmind/skills/artmind-query/SKILL.md`) never calls `query plan` or `query run`. Every
planner change since `v0.11.0` (P2b, B1–B7, S5–S8, the survey leg, document opening) reaches users only
through shadow plans and the bundle contract. **None of it has shaped a classic answer.** Classic's gains came
from the tools: passages, survey and summaries, brief shape, bounded output, honest commands.

## 2. Where the direction went wrong

1. **The main reason for the planner was fixed early by tools.** Round 7 (note 12) estimated a deterministic
   route would have avoided 66–91 % of struggle, but that struggle was mechanical: hidden outputs, command
   syntax, loops. Round 8, on bounded output alone with the classic agent and no planner: persisted results
   158/158 → 0/449, tool-gate refusals 29 → 0, median calls per turn about 8 → 5. The planner went ahead as
   phased anyway.
2. **Round 9 was a stop signal read as a go signal** (note 16). High plans were 3 of 36 in banking and 3 of 18
   in brain01, three of them wrong; on disagreements "the agent's choice was the better one on almost all".
   Banking agreement rose from 10 to 28 of 36 because B2 taught the planner what the agent already did. P3 then
   ran low plans' programs anyway, as the agent's starting evidence. That put the least reliable part, search
   terms built by string rules (`slots.search_term_slot`), at the front of every turn.
3. **Architecture principle 1 ("the LLM does not pick commands") is the wrong cut.**
   - Code wins at: command syntax, budgets and paging, view parameter filling, narrator and twins, exhaustive
     counts, honest outputs.
   - The model wins at: **vocabulary** (the question says "close", the SOP says "closure" and "holds": banking
     18, note 18 §4), **decomposition** (multi-clause questions net −5 in brain01), and **iterating on
     results**.

   The bundle removed the second set and anchored the agent. Skill rules cannot restore it: they were broken in
   observed answers, and the agent added its own lookups in 24 of 36 banking and 13 of 14 brain01 turns. Each
   P3b fix (open documents, more passages, a 28,000 budget, four skill rules) moved the bundle toward classic
   behaviour: the band-aid loop.
4. **The planner repeats the disease the study diagnosed.** Note 03: "the skill grew by adding a rule per
   failure". The same happened in Python, where it is harder to see:
   - `planner.plan()` is about 490 lines with about 40 mutable locals;
   - the route is reassigned in at least six places (forced follow-up, chunks-only, superlative → structured,
     named class → enumeration, view, lifted view), the domain set in at least four (B1, B2 rescope, B5 stores
     rescope, `with_guidance`);
   - precedence between rules is only statement order; rules live in six places (`intents.yaml`,
     `routes.yaml`, `classify/lexical.py`, `domain_set.py`, `slots.py`, per-domain plan config);
   - each B fix is a conditional written for failures in 85 labels.

   Planner code grew from about 5,100 lines at `v0.11.0` to 7,600, plus 1,900 in the executor. The original
   goal, a classic skill of about 120 lines (note 03 §A), never happened: classic grew from 463 to 550 lines,
   and a second 167-line skill sits beside it.
5. **Proxies were optimised, not answers.** Replay correctness, shadow agreement and document reach all rose;
   judged answers did not. Bundle reach was 82 vs 71 of 101 expected documents while bundle lost 19 to 13 on
   answers: touching a document is not reading it. The answer judge (S10) came last, and the gate it feeds is
   noisy: 14 and 36 questions, an unpinned judge, a classic-vs-classic band of 0 to 6.

## 3. Keep, revert, freeze

**Keep** (independent of the planner, helps classic):

| Change | Why |
|---|---|
| B9: `domains-overview` `views` / `insights` per domain; classic skips `views list` at `views: 0` | removes empty calls (P10) |
| B8: spans for read-only `db` subcommands | measurement |
| Registry migrations under one write lock (`ab1f0879`) | real bug, parallel plugin commands |
| `survey --passages` up to 8 | classic's `survey --expand` reads deeper by rank |
| `traces compare` and the judge (S10) | the only tool that measured answer quality; reuse it for classic skill variants |
| Profile / benchmark contract switch (S9 plumbing, not the bundle skill) | generalise into A/B of skill variants |
| `query run --each` (optional) | batch claims (note 12, lesson 23) with a fixed program; keep only if used |
| Everything in `v0.11.0` | passages, survey, summaries, brief, bounded output and handles, honest commands, domain guard |

**Revert:** `ARTMIND_OUTPUT_BUDGET` 28,000 → 24,000. It was raised for the bundle, but `webui/agent.py` and
`backends/acp.py` apply it to classic agents too, leaving 2,000 characters under the harness's 30,000 inline
limit.

**Freeze, do not merge as agent-facing:** the `artmind-query-bundle` skill and P3b; planner patches,
bindings, shadow dry runs, previous-context plumbing, opening and bundle assembly; shadow planning itself.
With `ARTMIND_DECISION_PROVIDER=openrouter` in `~/.artmind/config.env`, shadow planning sends every traced
question off the machine to a decision model that was confidently wrong; `ARTMIND_PLAN_SHADOW=0` turns it off.

Master already holds P2b (B1–B9). Apart from B8 and B9 it is shadow-only; removing it is a separate decision.

## 4. Would the remaining steps have helped?

Mostly no. None addresses the two main loss causes: vocabulary and reading depth.

| Step | Assessment |
|---|---|
| S11 subplan + B11 splitter | Targets multi-clause losses, but a code splitter is another rules layer, and a good one needs a model call, which brings the LLM back into the plan. Classic already splits questions |
| S12 fast path, B12 guards | Apply to almost nothing (high plans 3 of 36 and 3 of 18, three wrong). They save cost, which is not the goal |
| S13 retire | The classic-wins branch of it is due |
| Assembly pass (episodes), catalogue `terms` / aliases, event calendar | Worth keeping: data-side, helps both contracts, if exposed as data or commands the agent queries rather than routed around it |

## 5. Next moves suggested

1. Tag `claude/p3-design-a9f633` for the record; `ARTMIND_AGENT_CONTRACT` stays `classic`.
2. Cherry-pick the keep list onto master; revert the budget; turn shadow planning off.
3. Do the original goal: slim the classic skill on top of the improved tools, measured with `traces compare`
   over at least three classic runs per vault.

On the DSL idea, one implication only: the evidence says the hard part is domain vocabulary and decomposition,
not command choice. A DSL earns its place by encoding domain knowledge that both code and agent read, not by
adding another routing layer.
