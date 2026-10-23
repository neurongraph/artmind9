# Query routing study — artmind-query

A standing dossier on how the `artmind-query` skill routes a question to CLI commands,
why it misroutes, and how to restructure it (shorter skill, routing in one place,
deterministic routing in the CLI). **Status: evidence gathering.** Nothing here is
actioned yet; specs come once enough scenarios are covered.

Opened 2026-10-19.

## Files

| File | Holds |
|---|---|
| [01-current-state.md](01-current-state.md) | Inventory of the skill as of commit `89da8fa`: sizes, every place routing lives, gaps. |
| [02-trace-evidence.md](02-trace-evidence.md) | What recorded traces show, one section per probe round. Append, don't rewrite. |
| [03-proposal.md](03-proposal.md) | The working proposal: restructure, single routing table, `query plan`, other ideas, phasing. Revise as evidence arrives; log revisions at its foot. |
| [04-scenarios.md](04-scenarios.md) | Scenario catalogue: each question shape seen, the ideal route, what actually happened, which proposal element fixes it. The coverage checklist for "enough to write specs". |
| [05-probing.md](05-probing.md) | How to probe a new vault's traces: commands, the extraction snippet, the checklist for each round. |
| [06-open-questions.md](06-open-questions.md) | Questions the evidence must settle before specs. |
| [07-benchmark-round3.md](07-benchmark-round3.md) | Per-question grades, routes and causes for the round-3 banking benchmark (36 questions). |
| [08-fix-and-redesign-plan.md](08-fix-and-redesign-plan.md) | Fix + redesign plan (2026-10-22): review of the dossier, `query plan` first, workstreams W0–W5, phasing and gates. Input to specs. |

## How a later session uses this

The user runs Q&A in some vault(s), then asks a new session to probe them. That session:

1. Reads this README, `03-proposal.md`, `04-scenarios.md` and `06-open-questions.md`.
2. Follows `05-probing.md` against the vault(s) and trace ids/time window the user names.
3. Appends a new round to `02-trace-evidence.md`, adds or updates scenarios in
   `04-scenarios.md`, answers or adds open questions, and revises `03-proposal.md` only
   where the new evidence changes it (with a line in its revision log).
4. Does **not** change code, skills or tests — this dossier is analysis only until the
   user says to move to specs.

## Privacy rule — this checkout is public

Traces, vault notes and structured tables hold private corpus data. **Nothing from them
goes into this folder verbatim** (the sample banking corpus's public file names and the benchmark question ids `Qnn` are allowed; answer text and customer data are not): no person, place, company, table-column or note names,
no quoted questions or answers. Record question *shapes* ("count of trips by PERSON to
LOCATION in YEAR"), placeholder names (`PERSON_A`, `LOCATION_B`, `TABLE_1`), command
sequences, counts and rates. View names and schema class names that are generic
(`trips_by_year`, `PERSON`) are fine. Before committing, grep the diff for any name seen
in the traces.

## Vaults seen so far

| Vault | Domains | Structured store | Traces |
|---|---|---|---|
| `brain01` | `personal_journal` | none | ~60 admin turns, 2026-10-12 → 2026-10-19 (round 1); 11 admin turns on `claude-sdk` / Haiku 4.5, 2026-10-22 (round 4) |
| `my_work` | `performance_management` (+ others) | 1 table, mappings mostly unconfirmed | none recorded at round 1; Neo4j was down |
| `banking_corpus` | 8 sibling document domains `FAMILY.*` + literal `FAMILY` (tables) | 5 tables | 1 Q&A turn, 2026-10-20 (round 2, Claude SDK); 36 benchmark turns, 2026-10-21 (round 3, Claude SDK, Haiku 4.5 + one Sonnet 5) |
