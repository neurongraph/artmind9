# 05 — How to probe a round of traces

Analysis only: read traces, never write to the graph, never change code or skills.
Run from the checkout with `ARTMIND_VAULT=<vault>` (traces live in that vault's registry
DB). `traces` commands are not proxied, so no daemon caveats apply.

## 1. Headline numbers

```bash
ARTMIND_VAULT=~/artmind_vaults/<vault> artmind traces stats --compact
```

Record per domain and week: turns, `views_first_rate`, `views_skipped_rate`,
`view_run_rate`, `views_listed_unused`, `fall_through_rate`,
`mean_graph_attempts_before_fall_through`, `answer_basis`.

## 2. Command sequence per turn

`traces list` gives `trace_id`, `question`, `answer_basis`, `status`; `traces show <id>`
gives `events` (each `tool_call` payload holds the Bash command) and `retrieval_path`.
This prints one line per turn, with call count and the command sequence (keep the output
in the scratchpad, never in this folder, since it holds questions verbatim):

```bash
ARTMIND_VAULT=~/artmind_vaults/<vault> artmind traces list --limit 100 --compact | python3 -c "
import json, re, subprocess, sys
for t in json.load(sys.stdin)['turns']:
    r = subprocess.run(['artmind', 'traces', 'show', t['trace_id'], '--compact'], capture_output=True, text=True)
    try:
        s = json.loads(r.stdout)
    except ValueError:
        continue
    cmds = []
    for e in s['events']:
        if e['type'] != 'tool_call':
            continue
        m = re.findall(r'artmind ((?:query (?:graph )?|db |insights )?[\w-]+(?: [\w-]+)?)', e['payload'] or '')
        cmds += m[:1] if m else ([] if e['name'] in ('bash', 'Bash') else [e['name']])
    print(len(cmds), t['trace_id'][:8], t['answer_basis'], (t['question'] or '')[:60].replace(chr(10), ' '), '=>', ' | '.join(cmds))
"
```

Then `artmind traces show <id>` on the interesting turns for the full answer and evidence.

## 3. Context to capture

- Which skill version: `graph_state.artmind_version` in `traces show`, plus
  `git log -1 -- artmind/skills/artmind-query` at the time of the round.
- Backend/model: `backend`, `model` on the turn.
- Saved views in each domain: `ARTMIND_VAULT=… artmind query views list --domain <d> --compact`
  (reads view files from disk; Neo4j not needed). Record names, params, summaries only.
- Structured store: `artmind db bridge --compact` inside the vault (registry only).
- Whether the turn loaded the skill (`Skill`/`skill` tool call) or was a follow-up.

## 4. Per round checklist

1. Numbers (step 1) into a new round section of `02-trace-evidence.md`.
2. Group turns by question shape; for each shape compare the observed route with the ideal
   route in `04-scenarios.md`. Note call counts, skipped views, text2cypher retries,
   loops, off-protocol reads, turns with no command at all.
3. Name new failure patterns `P<n>` (continue numbering across rounds), anonymised.
4. Update `04-scenarios.md`: new rows, evidence column, status.
5. Check each proposal element in `03-proposal.md` against the new evidence: supported,
   contradicted, or untested. Revise and log.
6. Answer/add items in `06-open-questions.md`.
7. Privacy pass: grep the diff of this folder for every person/place/table/column name
   seen in the round; there must be none.

## 5. Benchmark rounds (added in round 3)

A benchmark run gives 36 labelled turns, so grade them rather than eyeballing:

1. List the turns: `artmind traces list` inside the vault (benchmark turns carry
   `benchmark_run_id` and `benchmark_seq`). Dump each with `artmind traces show <id>` to
   scratch files outside the checkout (e.g. `/tmp/…`), never into the repo.
2. Build one bundle per question: benchmark answer block, evaluation comment, the command
   sequence (the snippet in §2), expected documents.
3. Grade with parallel graders, one rubric: grade (correct/partial/wrong), expected vs cited
   documents, version correct, conflict disclosed, SQL and policy separated, primary and
   secondary cause (routing / retrieval / synthesis / data), the ideal route.
4. Re-check graders' document-state claims read-only: `ARTMIND_VAULT=<vault>
   ARTMIND_NO_PROXY=1 artmind query document-history --documentName <file>.md`.
5. Record the per-question table in `07-benchmark-round<n>.md` (ids, grades, causes, no answer
   text) and the patterns in `02-trace-evidence.md`.
6. Light comparison with earlier runs (`benchmarking/*.md`): speed, turns, cost only; grades
   are not comparable unless the old run was graded with the same rubric.
