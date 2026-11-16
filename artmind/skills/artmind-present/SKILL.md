---
name: artmind-present
description: Show an answer as a picture in the artmind chat UIs — a markdown table, a mermaid diagram, or an interactive artmind-graph block drawn live from the graph. Load it after retrieval when the user asks to show, draw, plot, map or graph something. It says which picture fits and how to write it.
---

# artmind Present

Retrieval comes first: answer with `artmind-query`. Then use this skill to present what retrieval
found. A picture never adds a fact.

## Choose the picture

| The answer is | Use |
|---|---|
| a list, or attributes compared across a few things | a markdown table |
| entities and their relationships, and the user says graph, network, map or connected, or wants to explore | an ` ```artmind-graph ` block, at any size |
| a short timeline, a process, or 5–15 facts read from note text rather than graph relationships | a ` ```mermaid ` diagram |

Only the admin console draws artmind-graph blocks. Your instructions say which UI you are running in. In the admin console, write the block when the table above calls for one; the UI draws it from the live graph as soon as your answer arrives, so write about the graph as shown. In the chat UI, use mermaid or a table: a block there shows as raw JSON.

Figures (counts per year, amounts) go in a table: there is no chart library.

## Mermaid rules

- Put quotes around every label: `A["Dr Rao"] --> B["Cardiology"]`.
- Write `<br/>` for a second line in a label; a raw newline breaks it.
- Keep every parenthesis and colon inside quotes.
- Draw at most 15 nodes. For more, use an artmind-graph block.
- Use `graph LR` for chains and hierarchies, and `timeline` for dated sequences.

## artmind-graph rules

The block is JSON. The UI runs its Cypher read-only against the live graph and draws the nodes
and relationships that the Cypher returns. The user can drag nodes, zoom, click a node to inspect
it, and copy the Cypher.

```artmind-graph
{
  "domain": "banking",
  "title": "Who the Credit Committee works with",
  "cypher": "MATCH (a:Entity {name: 'Credit Committee'})-[r:RELATES_TO]-(b:Entity) WHERE (a._domain IN $domains OR any(dom IN $domains WHERE a._domain STARTS WITH (dom + '.'))) AND (b._domain IN $domains OR any(dom IN $domains WHERE b._domain STARTS WITH (dom + '.'))) RETURN a, r, b LIMIT 100"
}
```

- Get the Cypher in one of two ways. Run `artmind query graph text2cypher "<question>" --domain D --dryRun --compact`, take its `generated_cypher` and rewrite its `RETURN`. Or write the pattern yourself from artmind-query's `references/graph-model.md` (`:Entity` nodes with `_id`, `name`, `entity_class`, `_domain`; `:RELATES_TO` edges with `rel_type`).
- Return whole nodes and relationships (`RETURN a, r, b`). Columns of names or counts draw nothing.
- Bind every relationship you want drawn (`-[r:RELATES_TO]-`, then return `r`).
- Scope every matched entity, the anchor too, with the `$domains` predicate shown above. The UI binds `$domains` to the block's `domain`.
- `$domains` is the only parameter. Write every other value (an `_id`, a name, a date) as a quoted literal. The `--dryRun` output keeps its other values in `parameters`: put them into the Cypher.
- Return bound entities, not paths, unless the path is the point. End with `LIMIT`. The UI stops at 500 nodes and says how many it left out.
- Use only `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `UNWIND`, `RETURN`, `ORDER BY`, `SKIP`, `LIMIT` and `UNION`, and plain functions (`count`, `size`, `any`, `labels`). The UI refuses everything else, including `CALL`, `USE`, comments and namespaced functions such as `apoc.*`.
- Write the Cypher on one line, with `domain`, `title` and `cypher` each on one line: a raw newline inside a JSON string breaks the block.
- Write one block per answer, unless the user asks for more.

## Honesty

- Keep the answer's caveats (an undercount, an ambiguous name) in the text beside the picture.
- In mermaid, draw only edges the results hold.
- An artmind-graph block redraws from live data each time it is opened. The answer text carries the claim, and the title says what was asked.
