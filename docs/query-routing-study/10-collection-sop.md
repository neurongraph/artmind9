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
| The user, on each machine | §1 pre-flight, §2 collection sessions, §3 rating, §4 export |
| An analysis session (Claude Code, checkout, any machine) | §5: pulls the exports, probes per `05-probing.md`, appends a round |

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
5. Note the machine (`A`, `B`, `desktop`) and its role (which vaults ingest there) in the
   vault's `benchmarking/collection-log.md` (private, committed with the vault).

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
- When you know the right answer, note it at once (§3), since it is cheap now and costly later.
- When an answer is wrong because of the data (a duplicate person, a missing fact), say so in
  `collection-log.md` beside the trace id; it is the evidence for the identity and extraction gaps (P29).

## 3. Rate and label

1. **Rate every turn**: the console's thumbs, or
   `artmind traces feedback <trace_id> --rating good|partial|wrong`. It takes no note: put the reason (one line per trace id) in `benchmarking/collection-log.md`.
   `partial` means usable but incomplete (missed a document, a person, a version).
2. **Label the turns where you know the answer.** Add an entry to
   `<vault>/benchmarking/plan_labels.yaml` (format: query-plan spec §4.6): domains, route
   or view, view params, `expect: low` when no plan should be confident. For survey shapes,
   add the expected documents to `<vault>/benchmarking/retrieval_labels.yaml`.
3. `artmind plan labels validate` and `artmind retrieval labels validate` must pass.

Labels and notes hold real questions: they stay in the vault's private repository.

## 4. Export the traces into the vault (each machine, end of each round)

Until a `traces export` command exists (design §6, new commands), run this from inside the
vault. It is read-only on the registry and writes one JSON line per turn to
`<vault>/.artmind/trace-exports/<machine>-<date>.jsonl`. `.artmind/` is never walked by
ingest, and Obsidian Git commits the file to the vault's **private** repository like the
reviews folder.

```bash
MACHINE=A   # or B, desktop
python3 - "$MACHINE" <<'EOF'
import datetime, json, os, pathlib, subprocess, sys
env = dict(os.environ, ARTMIND_NO_PROXY="1")
run = lambda *a: json.loads(subprocess.run(["artmind", *a, "--compact"], capture_output=True, text=True, env=env, check=True).stdout)
out = pathlib.Path(".artmind/trace-exports"); out.mkdir(parents=True, exist_ok=True)
path = out / f"{sys.argv[1]}-{datetime.date.today().isoformat()}.jsonl"
with path.open("w") as fh:
    for t in run("traces", "list", "--limit", "1000")["turns"]:
        fh.write(json.dumps(run("traces", "show", t["trace_id"])) + "\n")
print(path)
EOF
```

Then let Obsidian Git commit and push. On re-export the same day the file is rewritten whole.

## 5. Analysis (analysis session, after pulling every vault)

1. Read the exports from `<vault>/.artmind/trace-exports/*.jsonl` in place; any per-turn
   listing goes to the scratchpad, never into this folder.
2. Probe per `05-probing.md`, one round for all machines, with a coverage table per machine
   and vault (turns, rated, shadow plans, shapes covered).
3. `artmind plan replay --classifier both --repeat 4` in each vault with labels.
4. Append the round to `02-trace-evidence.md`; update `04-scenarios.md`, `06-open-questions.md`.
5. Report against the gates in the design §9: shapes covered, ratings, plan `wrong` rate.

## Done when

Every shape in §2 has at least 3 rated turns in at least two vaults, at least 40 labels
exist across vaults, and replay has run on each. Until then, phase P1 of the design can be
built but not switched on for the agent.
