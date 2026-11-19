# artmind-query: graph first, history reads, completion check

Status: draft for review (2026-11-19). Skill-only change; no code change.

## 1. Problem

After the temporal-model branch (merged 2026-11-18), benchmark runs on banking_corpus and
brain01 lose must-items the earlier runs found. On the regressed questions (banking 10,
brain01 12, Sonnet-graded, mean missed must-items per run):

| | baseline (2 runs) | new code + skill | new code, old skill | old code, migrated data |
|---|---|---|---|---|
| banking | 16.5 | 24.2 (4 runs) | 24.5 (2 runs) | 11.0 (2 runs) |
| brain01 | 27.0 | 36.0 (1 run) | 36.0 (2 runs) | 28.0 (2 runs) |

Reverting the skill changes nothing. Replaying the 129 commands the baseline agents ran,
on the old and the new binary against the same migrated data, gives the same content:
18 outputs differ, all by added `valid_from`/`valid_to` fields, a one-document count shift
from default-dated documents counting as undated, one row date in a topic plan, or the
removed `twins` hint on `entity-resolve` (occasions are no longer twins). No command lost
evidence. So the gap is in how the agent routes, which varies run to run; the stronger runs
share one route and the weaker ones another.

## 2. What the traces show

- **Weak route:** `orient` → `survey` → `find` ×N → answer. The answer stops at whatever the
  first survey ranked. Dated items in lower-ranked documents, and anything in a superseded
  document, are never read.
- **Strong route:** `orient` → `entity-resolve` → `entity-context --includeChunks` (one call per
  anchor entity) → `pattern8 --via mentions` or `chunks` for the rest → `survey` only for what
  the graph did not cover.

Recovery check (each missed must-item's dates, figures or names, looked up in the output of
the command that should have been run; private text not reproduced here):

| Question | Missed | Recovered by | Share |
|---|---|---|---|
| brain01 Q10 (a person's academic path) | 8 | `pattern8 --entityId <person> --entityClass EVENT --via mentions --all` | 7 of 8; the 8th by `happenings --with <person>` |
| brain01 Q03 (relationships with parents, kids) | 4 | `entity-context` on each resolved family member | 2 of 4 fully, 1 in part |
| brain01 Q08 (two hobbies over time) | 5 | `survey` itself (the items were in its documents) | 50–83% each: the agent did not open the rows |
| banking Q09 (how a policy changed) | 3 | `pattern10 --includeHistory` on the predecessor named in `replaced` | 3 of 3 |
| banking Q18 (an incident across policies) | 3 | `pattern10` on the incident plan named by its code | 2 of 3 |

Two things recover almost everything: **graph commands on the resolved anchor before survey**,
and **reading the predecessor when a document says it replaced one**. A third case (Q08) is
plain under-reading of a survey that already held the answer.

## 3. Changes to `artmind/skills/artmind-query/SKILL.md`

### 3.1 Graph first for an anchored part ("Plan the parts")

Add to the rules for choosing a command per part:

> **A part about a named person, thing or place goes to the graph first.** Resolve it
> (`entity-resolve`), then run `entity-context --shape brief --entityId <id> --includeChunks 8
> --query "<the part>"`. For a part that lists or traces what happened to it over time
> ("how has X progressed", "what has X done", "X's Y over the years"), also run
> `graph pattern8 --shape brief --entityId <id> --entityClass <EVENT|ACTIVITY|…> --via mentions --all`
> for each occurrent class orient lists, and `happenings --with <id>` when the part names a kind of
> happening. Use `survey` and `find` only for what those did not cover, and say so in the plan.
> A part with several anchors (parents, kids) gets one `entity-context` per anchor.

And change the parts-plan line format so the first command for an anchored part is `entity-resolve`,
not `survey`.

### 3.2 History reads when something was there earlier ("Read every result")

Add a numbered rule after rule 5:

> **A predecessor is evidence too.** When a row carries `replaced` (survey, vector-text) or
> `superseded_by`, or the question asks how something changed, what it was before, or what an
> earlier version said, read the earlier version before answering:
> `artmind query graph pattern10 --domain <d> --documentName "<the replaced name>" --includeHistory --compact`.
> `document-history` on the newer document lists observation changes of that one document's own
> versions; it does not open a predecessor document. State each version's dates and what it said.

Move the existing one-line "Superseded history" note (Retrieve) next to this rule and point it here.

### 3.3 Completion check before answering ("Every turn" step 4)

> **Before you answer, check what you read against what matched.** For a part that spans
> documents, compare the documents you opened with `coverage.matching_documents` and with the
> survey rows whose `gist` speaks to the part. Open the ones you skipped (`find`, or `chunks
> --idList`). For a part that traces something over time, check that each period with
> evidence is represented. Say what remains unread.

### 3.4 The `entity-resolve` twin hint

The temporal model stopped reporting `twins` for occasion and category classes, so five
"Photography" ACTIVITY rows no longer come with "treat them as one". Add to Resolve:

> Several rows with the same name and an occasion class are separate happenings on different
> days (orient's classes say `identity: occasion`), not duplicates: when the question is about
> the activity in general, read them together (`pattern8` on the person, or `happenings --match`).

### 3.5 Not changed

No CLI or code change. The `happenings` and `knownAsOf` rows the temporal branch added stay.

## 4. Test

- Subsets as in §1 (banking 10, brain01 12 questions), 3 runs per vault on the new skill,
  Sonnet-graded against the two baselines and the earlier new-skill runs.
- Pass: mean missed must-items at or below the baselines on both vaults, and no question
  worse than every baseline in 2 of 3 runs.
- Then a full run per vault (banking 36, brain01 29) before the change merges.
- Trace check: share of anchored parts whose first retrieval call is `entity-resolve` /
  `entity-context`, before vs after.

## 5. Out of scope

- Code changes to `document-history` or the survey `replaced` hint (a separate task).
- Any change to grading or the gate.
