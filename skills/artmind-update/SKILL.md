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
uv run artmind domains list
```

Inspect the user's first message for domain signals (e.g., project names, people, domain-specific vocabulary). If confident, announce the chosen domain and proceed. If ambiguous, show the domain list and ask the user to pick.

## Step 1 — Draft (extract + find candidates)

```bash
uv run artmind update draft \
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

## Step 3 — Confirm (write to graph)

```bash
uv run artmind update confirm \
  --session <session_id> \
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, relationships_written, user_chat_id}`

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written."

## Step 4 — Continue or Exit

Ask: "Anything else to add to this session?"

- If yes: go back to Step 1 with the same `--session <session_id>`.
- If no: report the full session summary and exit.

## Multi-turn Notes

- All turns in one skill invocation share the same `session_id`. Pass it in every `draft` call after the first.
- If the user's input has no extractable entities, report this clearly and ask if they want to rephrase.
- If extraction returns many entities, present all candidate batches in a single message grouped by entity.

## Export Reference

To dump all user-added knowledge to markdown (outside this skill, via CLI):

```bash
uv run artmind update export --format sequential --output data/chats/
uv run artmind update export --format by-entity --output data/chats/
```
