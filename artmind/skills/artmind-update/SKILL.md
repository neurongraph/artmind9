---
name: artmind-update
description: Add and update facts in the artmind knowledge graph through natural language. Supports atomic facts, passages, pasted text, and todos. Domain-scoped, auditable, with ambiguity resolution before writing to the graph.
---

# artmind Update

Use this skill to let a user add or update facts in the artmind knowledge graph through conversational natural language input. You orchestrate domain detection, LLM extraction, candidate disambiguation, and graph writing via the CLI.

## Grounding Rules

- Never write to the graph until the user has confirmed candidate resolutions.
- Derive domain from the user's first message where possible; ask if ambiguous.
- Present all candidate choices for a single input in one batch, not one-by-one.
- Report what was written (nodes created/updated, relationships written, facts retracted) after each confirm.
- **Pass `--text` verbatim — never paraphrase, expand, or add facts, context, or
  outside knowledge the user did not actually state.** `--text` becomes
  `raw_text` on the graph's `UserChat` node, stored exactly as given: it is
  provenance, the permanent record of what the user actually said, and
  extraction runs over it — so embellishing it before the call doesn't just
  misrecord the input, it can plant entities and relationships in the graph
  that trace back to your own elaboration rather than anything the user
  asserted. Fixing an obvious typo is fine; supplying a fact, a date, a cause,
  or a "such as" the user never mentioned is not — this is the same grounding
  principle `artmind-query` applies to its answers, applied here to the input
  instead of the output. If the user's own phrasing is genuinely ambiguous or
  underspecified, ask them to clarify rather than filling the gap yourself.

## Required Inputs

- `domain`: Auto-detect from input; ask if unclear.
- `text`: The user's natural language input, passed to `--text` verbatim (atomic
  fact, passage, todo, pasted text) — see the grounding rule above.

## Session Setup

At skill start, load available domains:

```bash
artmind domains list
```

Inspect the user's first message for domain signals (e.g., project names, people, domain-specific vocabulary). If confident, announce the chosen domain and proceed. If ambiguous, show the domain list and ask the user to pick.

## Step 1 — Draft (extract + find candidates)

```bash
artmind update draft \
  --domain <domain> \
  --text "<user input>" \
  [--session <session_id>]
```

Output JSON contains:
- `session_id` — carry this for all subsequent turns
- `extracted_entities` — list of `{temp_id, name, entity_class, properties}`
- `extracted_relationships` — list of `{source_temp_id, target_temp_id, rel_type}`
- `candidates_per_entity` — list of `{entity, temp_id, top_n: [{node_id, name, entity_class, match_score, context_snippet}]}`

## Step 2 — Present Candidates and Collect Resolutions

For each entity in `candidates_per_entity`:

- If `top_n` is empty: automatically use `action: "create"` (no ambiguity).
- If `top_n` has candidates: present them to the user:

```
Found "Alice" — did you mean:
  1. Alice Smith (PERSON, linked to Acme Corp)
  2. Alice Johnson (PERSON, linked to Project Alpha)
  3. None of these — create new
```

Batch all entities into one message. Collect all answers before proceeding.

Build the resolutions JSON array:

```json
[
  {"entity_temp_id": "e0", "action": "link", "node_id": "<node_id from top_n>"},
  {"entity_temp_id": "e1", "action": "create", "node_id": null},
  {"entity_temp_id": "e2", "action": "skip", "node_id": null}
]
```

## Step 2b — Retraction (fact-level, not node-level)

Some facts don't just add information — they **retract** an existing one. "The
branch manager changed to Harry Potter" doesn't just add Harry Potter; the old
`headed_by` fact is no longer true. There is no node-level supersession any
more (entities are recomputed from observations on every rebuild, so nothing
can be marked superseded and have it stick) — the retraction targets the
specific **observation**, or the specific `ASSERTS_RELATION` edge behind a
relationship fact, never the entity node itself.

`update draft`'s response includes `supersession_candidates` — auto-detected via
the heuristic "the fact's source already has a same-rel_type edge to a different
target." Each entry looks like:

```json
{
  "source_temp_id": "e0", "source_name": "London Canary Wharf Branch",
  "target_temp_id": "e2", "new_target_name": "Harry Potter",
  "rel_type": "headed_by",
  "replaces": [{"node_id": "<elementId>", "name": "Branch Manager - James Chen",
                "entity_class": "PERSON", "relation_observation_ids": ["<id>", ...]}]
}
```

If present, ask the user to confirm before acting on it — this is a suggestion,
never applied automatically:

```
This "headed_by" fact for Harry Potter looks like it replaces:
  - Branch Manager - James Chen (PERSON)
Retract the old relationship? [y/N]
```

If confirmed, add a `retracts` list to the resolution for the *new* fact's
entity (the `create`/`link` action for `target_temp_id`, e.g. `e2` above),
naming the `relation_observation_ids` from `replaces` above:

```json
[
  {"entity_temp_id": "e0", "action": "link", "node_id": "<canary wharf node_id>"},
  {"entity_temp_id": "e2", "action": "create", "node_id": null,
   "retracts": ["<relation_observation_id from replaces above>"]}
]
```

A retraction can also name a plain **observation id** directly (e.g. one
returned by `query entity-history`) — `retracts` accepts a bare id string, a
list of id strings, or the dict shape above; `id_` naming doesn't matter, only
`id` / `observation_id` / `relation_observation_id` keys are read from a dict
entry.

`confirm`'s response then includes `nodes_retracted` alongside the usual
counts. A retracted observation is never deleted — it is relabelled to
`:ObservationHistory` (or, for a relationship, its `ASSERTS_RELATION` edge is
removed) and drops out of the projection on the next rebuild, which already
runs as part of this same `confirm` call.

## Step 3 — Confirm (write to graph)

```bash
artmind update confirm \
  --session <session_id> \
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, nodes_retracted, relationships_written, user_chat_id}`

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written, 1 fact retracted."

Optionally verify the write landed as intended — useful after creating new entities or when relationships were involved:

```bash
artmind query graph pattern2 --domain <domain> --entityNameList "<new entity name>" --compact
```

## Step 4 — Continue or Exit

Ask: "Anything else to add to this session?"

- If yes: go back to Step 1 with the same `--session <session_id>`.
- If no: report the full session summary and exit.

## Multi-turn Notes

- All turns in one skill invocation share the same `session_id`. Pass it in every `draft` call after the first.
- A session is pinned to the domain it was created with — `confirm` writes with
  that domain, not the one passed to a later `draft`. Pass the same `--domain`
  on every resumed turn; if the user switches domain mid-conversation, start a
  new session (drop `--session`) rather than reusing this one. Resuming with a
  different `--domain` is rejected.
- If the user's input has no extractable entities, report this clearly and ask if they want to rephrase.
- If extraction returns many entities, present all candidate batches in a single message grouped by entity.

## Resolving Similar Nodes

If during an update session you notice similar entity names that should be
merged (e.g., "Alice" vs "Alice Smith" or "Project Alpha" vs "Alpha Project"),
use `refine-graph --filter` to scope name-similarity clustering to just those
names, which writes a same-as **proposal** — it does not merge directly:

```bash
artmind ingest refine-graph --domain <domain> --filter "<name1>,<name2>,..." --dry-run --output merges.json
# review merges.json, then:
artmind ingest refine-graph --domain <domain> --from-file merges.json
```

Approving is a separate, explicit step — hand off to artmind-curate:

```bash
artmind sameas list --status open --compact
artmind sameas approve <proposal_id>
```

**Workflow**: During candidate resolution in an update, if you spot similar
nodes that should be merged, note the entity names → scope `refine-graph
--filter` to them → point the user at artmind-curate to review and approve →
continue with the update.

Same-as proposals are for **duplicate** entities (same real-world thing, two
records). Retraction (Step 2b) is for **distinct** entities where one
temporally replaces the other (different role-holders, different addresses
over time) — don't run `refine-graph` on those; it will find no name
similarity and no-op.

## Export Reference

To dump all user-added knowledge to markdown (outside this skill, via CLI):

```bash
artmind update export --format sequential --output data/chats/
artmind update export --format by-entity --output data/chats/
```
