# Turn check: verify a turn's work from its trace before the agent answers (2026-11-11)

Status: **future enhancement**, scheduled after item E (P2b skill restructure) in the work plan of
`2026-11-08-classic-baseline-and-skill-simplification-design.md`. Not built.

## 1. Why

Run M (study note 22) put a check-off list in the orient skill: before answering, the agent rewrote its `Parts:`
list with a status per line and ticked a part only when its evidence, `next`, partial results, mart counts and
warnings were dealt with. It moved no group beyond run-to-run noise, added about 5 s a turn in banking, and the
status lines reached the user in about half the answers. The agent ticked for the reader, not as a gate: banking Q11
ticked a part while its own caveat said it had not opened the documents that would confirm it.

What did move scores was a fact on the result itself (run L: a `text2sql` result read from the mart carries a
lower-bound warning and a survey `next`). The lesson: a check works when the system makes it, not when the agent
reports it. Most checklist items are already facts in the turn's trace.

## 2. What it is

A deterministic check of the current turn, made from its trace spans, with no LLM call:

```
artmind query check [--parts FILE|-] [--skip N:"reason" ...] --compact
```

It reads the turn's spans through the trace id the agent's Bash already carries (`ARTMIND_TRACE_CONTEXT`, as
`TracedGroup` does) and returns the open items, one per row, each with the command that would close it:

```json
{"command": "query check", "warnings": [], "next": "artmind query survey --domain d --shape brief --compact '…'",
 "open": [{"n": 1, "kind": "mart_unchecked", "span": "…", "close": "artmind query survey …"}],
 "closed": 4, "skipped": []}
```

`open: []` means the turn may answer. Not proxied (it reads the caller's own trace), traced as its own layer.

## 3. The checks

| kind | Open when | Closed by |
|---|---|---|
| `next_unfollowed` | a span returned `next` and no later span ran its first command | running it, or `--skip` with a reason |
| `partial_relied_on` | a span came back `truncated`, `complete: false`, with a `cut` or `rows_total` above its rows, and no later `query page` / `--all` / cut `rest` | paging, or `--skip` ("needs one row") |
| `mart_unchecked` | a `text2sql` whose SQL reads `__mart_` returned rows and no later `survey` / `vector-text` ran | the survey its `next` names |
| `unread_part` | a span's `warnings` names a part not read (orient, catalogue, survey summaries) | rerunning when it can be read, or `--skip` and saying so in the answer |
| `identity_open` | `entity-resolve` returned `twins` or `needs_disambiguation` and a later span used one of the names | a span with the chosen `--entityId`, or `--skip` |
| `part_without_evidence` | only with `--parts`: a part none of whose terms appear in the query of a span that returned rows | a search for that part |

The first five need nothing from the agent. `part_without_evidence` is the one fuzzy check (term overlap between a
part line and span args); it is advisory until measured.

Skips are recorded on the check's span (kind, reason), so traces show what was waived and why.

## 4. Enforcement: a Stop hook

A command the skill asks for can be skipped, as the M checklist was. On the Claude SDK backend
(`artmind/webui/agent.py`, which already registers `PreToolUse` and `SubagentStart` hooks), a `Stop` hook runs the
check in process when the agent tries to end its turn:

- no open items → the turn ends;
- open items → the hook blocks once, with the open rows and their `close` commands as the reason; the agent closes or
  skips them and answers;
- at most one block per turn, so it never loops; a second stop always ends the turn, and the trace records what was
  still open.

The exchange stays between the hook and the agent, so nothing reaches the answer. The ACP backend has no stop hook:
there the check is a skill instruction only, and measured as such.

## 5. Measuring it

Two runs per vault (one run moves up to 9 points with unchanged code), against the run before it. Beyond must items:
from the trace, how many items were open at the first stop, of which kinds, and how many were closed or skipped. If
items are closed and scores do not move, the checks are the wrong ones and the kinds say which.

## 6. Open questions

- Which `next` values are safe to treat as optional by default (a `views list` hint, an orient re-read) so they never
  open an item.
- Whether `part_without_evidence` needs the parts written to the trace by the agent (a `--parts` file) or can read
  them from the assistant text the trace already holds.
- Latency budget: the block adds one round only when something is open; cap the work the agent may do after it.

## 7. Non-goals

No LLM judging inside the check, no change to answers' text, no planner (`artmind/query_plan/` stays frozen).
