---
name: artmind-build-topic
description: Builds a topic plan and its topic page for an overview or trend question that many documents answer (admin console only). Proposes subjects and slices, shows the dry run (documents, calls, cost, what leaves the machine), builds only after the user's explicit yes in this conversation, then runs the plan to answer. Use when the user accepts an offer to "build a plan" or asks for a topic page or plan on X.
---

# Build a topic plan

A **topic plan** is a saved retrieval program for a broad question: its subjects (the
facets you propose and the user keeps), its slices (per year or month, per child domain)
and how many documents each cell reads. `artmind topic build` writes the plan (for
agents, run live with `artmind query topics run`) and a topic page (for people, in
`_Topics/`). Building sends the gathered passages to the configured LLM provider: one
call per (document, subject) not already in the map cache, plus the reduces.

**Admin console only.** Never build without an explicit yes from the user in this
conversation, given after they saw the dry run. A yes to an earlier build is not a yes
to this one.

## 1. Propose

From the question and `artmind query orient --domain <d> --compact` (its `levels` block
lists existing plans: if one fits, run it instead and stop here):

- **Topic**: the question's subject in plain words (it names the slug).
- **Subjects** (`--facet`, 1–8): the parts of the answer, each in the words the documents
  use. Keyword counts drive each cell's depth, so prefer a few concrete words ("photo walk
  camera lens editing") over a sentence. One subject per part the question names.
- **Slices**: leave `--by auto --split auto` (year for a long dated journal, per child
  domain for a family of document types) unless the user wants otherwise.

## 2. Dry run (no LLM, writes nothing)

```bash
artmind topic build "<topic>" --domain <d> --facet "<subject 1>" --facet "<subject 2>" --dryRun --compact
```

Show the user, briefly:

- `grid.cells`: per subject and cell, `matching` documents and `take`. A subject with 0
  everywhere is a wrong facet: reword or drop it and dry-run again. `partial` cells or a
  `cut` say the depth is capped (`--perCell`, `--maxDocs`, max 300).
- `suggested_subjects`: anchor entities whose names match the topic; offer them as
  subjects the user may keep or drop.
- `documents`, `calls` (`map`, `map_cached`, `reduce`) and `effect.llm`
  (`calls`, `model`, `est_cost_usd`, an estimate).
- What leaves the machine: the passages of every mapped document go to the model.

Then ask one question: build it, change it, or stop.

## 3. Build (only after the yes)

Run the same command without `--dryRun`. Report `path` (the page), `plan_path`, `version`,
`calls.made` and `checks`. If the user wants the plan refreshed on its own, add
`--refresh on_stale` or `weekly` (their opt-in; capped by ARTMIND_TOPIC_MAX_COST_USD).

## 4. Answer from the plan

```bash
artmind query topics run <slug> --shape brief --compact
```

Answer the user's question from these rows as artmind-query would (cite passages and
documents; say which cells are `partial`). Do not read the page to answer: it is for people.

## Later

- `artmind query topics plan <slug> --compact` shows the plan, its SKILL.md and per-cell
  counts at build vs now; edit `SKILL.md` (when the plan applies, what it does not cover)
  and bump `version` in `plan.yaml` when you do. `artmind topic plan validate <slug>` checks it.
- `artmind topic refresh <slug> --dryRun` shows what a refresh would cost; run it only after a yes.
