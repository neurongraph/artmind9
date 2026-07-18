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
- Report what was written (nodes created/updated, relationships written) after each confirm.

## Required Inputs

- `domain`: Auto-detect from input; ask if unclear.
- `text`: The user's natural language input (atomic fact, passage, todo, pasted text).

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

## Step 2b — Node-Level Supersession

Some facts don't just add information — they **replace** an existing node. "The
branch manager changed to Harry Potter" doesn't just add Harry Potter; it retires
whoever held that role before. Unlike a property change (link + update
properties), the old fact here lives on a *distinct existing node* that should be
marked historical, not deleted.

`update draft`'s response includes `supersession_candidates` — auto-detected via
the heuristic "the fact's source already has a same-rel_type edge to a different
target." Each entry looks like:

```json
{
  "source_temp_id": "e0", "source_name": "London Canary Wharf Branch",
  "target_temp_id": "e2", "new_target_name": "Harry Potter",
  "rel_type": "headed_by",
  "replaces": [{"node_id": "<elementId>", "name": "Branch Manager - James Chen", "entity_class": "PERSON"}]
}
```

If present, ask the user to confirm before acting on it — this is a suggestion,
never applied automatically:

```
This "headed_by" fact for Harry Potter looks like it replaces:
  - Branch Manager - James Chen (PERSON)
Mark the old one as superseded? [y/N]
```

If confirmed, add a `supersedes` list to the resolution for the *superseding*
entity (the `create`/`link` action for `target_temp_id`, e.g. `e2` above):

```json
[
  {"entity_temp_id": "e0", "action": "link", "node_id": "<canary wharf node_id>"},
  {"entity_temp_id": "e2", "action": "create", "node_id": null,
   "supersedes": [
     {"node_id": "<James Chen elementId>", "effective": "2026-07-18", "reason": "role holder changed"}
   ]}
]
```

`effective` defaults to today if omitted. The superseded node need not be one of
the extracted entities at all — if you spot a node to retire through some other
query, add it to `supersedes` the same way. The `node_id` field accepts either
identifier format: the elementId from `find_candidates`/`replaces` above, or
the `id` property returned by `entity-context`/`pattern2`/etc.

`confirm`'s response then includes `nodes_superseded` alongside the usual counts.

Superseded nodes are never deleted — they get `valid_to`, `superseded_by`, and
`status: 'superseded'`, and drop out of `--asOf`-filtered queries automatically.

## Step 3 — Confirm (write to graph)

```bash
artmind update confirm \
  --session <session_id> \
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, nodes_superseded, relationships_written, user_chat_id}`

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written, 1 node superseded."

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
- If the user's input has no extractable entities, report this clearly and ask if they want to rephrase.
- If extraction returns many entities, present all candidate batches in a single message grouped by entity.

## Resolving Similar Nodes

If during an update session you notice similar entity names that should be merged (e.g., "Alice" vs "Alice Smith" or "Project Alpha" vs "Alpha Project"), use the `refine-graph` command with the `--filter` option to detect and resolve duplicates:

```bash
artmind ingest refine-graph --domain <domain> --filter "<name1>,<name2>,..." --dry-run --output merges.json
```

This filters merge detection to only the specified entity names (comma-separated). Review the proposals in `merges.json`, then apply:

```bash
artmind ingest refine-graph --from-file merges.json
```

**Workflow**: During candidate resolution in an update, if you spot similar nodes that should be merged, note the entity names → use refine-graph with `--filter` to focus detection → merge → continue with the update.

Merges are for **duplicate** nodes (same real-world thing, two records). Node
supersession (Step 2b) is for **distinct** nodes where one temporally replaces
the other (different role-holders, different addresses over time) — don't run
refine-graph merge detection on those; it will find no similarity and no-op.

## Correcting Supersession Outside a Session

If a node should be marked superseded and it wasn't caught during a draft (e.g.
you're fixing history after the fact), use the standalone command instead of a
full update session:

```bash
artmind update supersede \
  --newer <id of the entity now current> \
  --older <id of the entity being retired> \
  --effective <ISO date, default today> \
  --reason "<optional free-text note>"
```

`--newer`/`--older` each accept either identifier format you might already
have: the `node_id` (Neo4j elementId) returned by `update draft`'s candidates,
or the `id` property returned by `query graph pattern2` / `query
entity-context`. Look up the id first if you don't already have it.

## Export Reference

To dump all user-added knowledge to markdown (outside this skill, via CLI):

```bash
artmind update export --format sequential --output data/chats/
artmind update export --format by-entity --output data/chats/
```
