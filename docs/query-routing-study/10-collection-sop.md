# 10 — SOP: collecting traces and labels on every machine

Written 2026-11-02. The standing procedure for gathering the evidence that `query plan`, the
planner v2 and the admin-UI redesign are gated on
(`docs/superpowers/specs/2026-11-02-retrieval-curation-architecture-design.md` §9). Same
privacy rule as the rest of this folder: what comes back here is shapes and counts.

## Why an SOP

- **Traces never travel.** They live in `<vault>/.artmind/data/document_registry.db`, which
  each vault gitignores (machine-local). Laptop A's turns are invisible on laptop B and on the
  desktop, whatever Obsidian Git syncs.
- **Round 6 (`02-trace-evidence.md`) found the collection incomplete.** The desktop holds
  49 turns, 26 shadow plans and 2 ratings; none of the banking turns are on it.
- **Shadow agreement is not correctness.** Without ratings and labels, nothing can say
  whether the plan or the agent was right (P32).

## Roles

| Who | Does |
|---|---|
| The user, on each machine | §1 pre-flight, §2 collection sessions, §3 rating and notes in the UI, §4 hand over the registry DB |
| An analysis session (Claude Code, checkout, any machine) | §5: reads the copied DBs, probes per `05-probing.md`, drafts labels, appends a round |

## 1. Pre-flight (each machine, each vault, once per round)

1. Bring the code up to date: `git pull` in the checkout, then `just dev-install`.
2. Decide the vault's decision provider (`09` §3.1). With the machine-wide OpenRouter
   setting, every shadow-planned question and its candidate names leave the machine. For a
   private vault, either put the local Ollama settings in `<vault>/.artmind/config.env` or
   set `ARTMIND_PLAN_SHADOW=0` there (then the round collects agent routes only).
3. Restart the daemons **from inside the vault** so they load the new code and settings:
   `just dev-stop-daemons`, then start `artmind serve` and `artmind admin-ui` (and
   `chat-ui` if you use it) from the vault folder. A daemon started earlier serves stale
   code and records stale plans (CLAUDE.md §1).
4. Check: `curl -s http://127.0.0.1:8377/health` names this vault, and
   `artmind traces stats --since 1d --compact` runs.
5. Note which machine this is (`A`, `B`, `desktop`); the handover file's name carries it (§4).

## 2. Collection sessions

Target per corpus: **50 rated turns** across at least two sessions, in the admin console
(traced, shadow-planned) or the chat UI (traced since 2026-11-01). Spread them over the
shapes below. A shape with no turns stays unmeasured, and every planner and UI decision
waits on it.

| Shape | Turns | Example form (write your own; never paste these here) |
|---|---|---|
| Count / aggregate | 5 | how many X did PERSON do in YEAR |
| Entity profile | 4 | tell me about ENTITY |
| History / change over time | 4 | what did PERSON say about X over time; earlier version of DOC |
| Named document | 3 | summarise DOC; what does DOC say about X |
| Survey across documents | 6 | what do all the DOCTYPE say about X; compare A and B |
| Enumeration / superlative | 4 | all X; most frequent X |
| Structured (table) | 3 | numbers from a table, alone or with policy |
| Hierarchy / path | 3 | who is under X; how are X and Y related |
| First person (personal vaults) | 3 | my trips; my doctors |
| Follow-ups | 8 | a second question in the same conversation |
| Operator / non-retrieval | 4 | reformat the last answer; curate a duplicate you just noticed |
| Topic page asked for by name | 3 | show me the page on TOPIC |

Rules:

- Ask naturally; do not phrase for the planner.
- Prefer the Claude SDK backend in the console: turns on the ACP backend record no model,
  cost or call count (P33), so they cannot be compared on cost or effort.
- When you know the right answer, note it at once (§3), since it is cheap now and costly later.
- When an answer is wrong because of the data (a duplicate person, a missing fact), say so in
  the turn's note (§3); it is the evidence for the identity and extraction gaps (P29).

## 3. Rate and comment in the UI

Every traced turn in the admin console (and the chat UI) ends with **Good / Partial / Wrong**
buttons. Partial and Wrong open a note box ("What was expected?") with **Save note**. The
rating and the note go into the vault's registry DB (`trace_feedback`) beside the trace, so
nothing else needs writing during the session.

1. **Rate every turn.** `partial` means usable but incomplete (missed a document, a person, a
   version).
2. **Write the note for every Partial or Wrong**, and for a Good where you know the expected
   answer: what the right answer or documents were, and whether the cause is the data
   (duplicate, missing fact) or the answer. The note is the label: the analysis session
   drafts `plan_labels.yaml` / `retrieval_labels.yaml` entries from it and you confirm them
   (no hand-written YAML during collection).
3. Changing your mind is fine: rate again. The latest rating counts.

Known UI limits (design note, not blockers): the note box is hidden for Good, and a turn
reopened from the conversation sidebar shows the buttons unselected, so an earlier rating is
not visible there (it is still stored).

## 4. Hand over the registry DB (each machine, end of each round)

Traces, shadow plans, ratings and notes all live in one SQLite file per vault:
`<vault>/.artmind/data/document_registry.db`. It is gitignored, so it is copied by hand.

1. Take a consistent copy (the UI may be writing to it; a plain `cp` can miss the `-wal`
   file). From inside the vault:

   ```bash
   mkdir -p ~/artmind-trace-handover && sqlite3 .artmind/data/document_registry.db ".backup '$HOME/artmind-trace-handover/<machine>-<vault>-<date>.db'"
   ```

   Or stop `admin-ui`/`serve` first, then copy `document_registry.db` together with any
   `document_registry.db-wal` and `-shm` beside it.
2. Move the file to the analysis machine (AirDrop, USB, a private share). **Not** into the
   artmind checkout, and not into any git repository: it also holds conversations, job
   history and document records.
3. Tell the analysis session where the files are.

## 5. Analysis (analysis session)

1. Put each copy in its own folder, renamed to `document_registry.db`, and read it with
   `ARTMIND_DATA_DIR=<that folder> ARTMIND_NO_PROXY=1 artmind traces list|show|stats`, run
   from outside any vault (checked 2026-11-02 on a copy of brain01's DB: 36 of 36 turns
   read). Any per-turn listing goes to the scratchpad, never into this folder.
2. Probe per `05-probing.md`, one round for all machines, with a coverage table per machine
   and vault (turns, rated, shadow plans, shapes covered).
3. Draft labels from the notes into each vault's `benchmarking/`, for the user to confirm;
   then `artmind plan replay --classifier both --repeat 4` inside that vault (replay needs its
   live graph, so it runs on a machine that has one).
4. Append the round to `02-trace-evidence.md`; update `04-scenarios.md`, `06-open-questions.md`.
5. Report against the gates in the design §9: shapes covered, ratings, plan `wrong` rate.

## Done when

Every shape in §2 has at least 3 rated turns in at least two vaults, at least 40 labels
exist across vaults, and replay has run on each. Until then, phase P1 of the design can be
built but not switched on for the agent.
