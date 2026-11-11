# Hub entities and grounding
Read when a result carries `guide: hubs` (`hub: true` or a `cut`), or before pulling source text.

#### Hub entities

Some entities aggregate a lot — the protagonist of a novel series, the bank in a
policy corpus: hundreds of observations, hundreds of edges. `entity-resolve`'s
`observation_count`/`connection_count` show it up front. Their full dossier is far too
big to read (and agent harnesses truncate it — a `...output truncated...` notice means
you never saw the part that answers), so `entity-context` returns a bounded one:

- `connection_summary` — `{rel_type, direction, count}` over **every** edge: the shape
  of the entity. `connections_total` is the edge count; `connections` lists only the
  top `--maxConnections` (default 25), each `{rel_type, direction, target{id, name,
  entity_class}, observation_count, doc_count}`.
- `chunks_total` / `source_documents_total` count everything behind the entity;
  `chunks`, `more_chunks` (≤20) and `source_documents` (≤10, the returned chunks'
  documents first) are the cut.
- List properties keep their newest `--maxListItems` (default 10) entries;
  `truncated_properties` gives each cut list's full length.

Pass the question to `entity-context`; on a hub its `cut` block says what is hidden.


### 5. Ground — pull source text when narrative evidence is needed

Grounding has a deterministic path and a search path — prefer the deterministic one:

1. **You already have chunk ids** (doc_sources from patterns 2/3/4, evidence from
   conflicts) → `artmind query chunks --shape brief --domain <d> --idList <id> [--expand 1]`.
2. **The question is anchored on one resolved entity** → you should have used
   `entity-context` in Retrieve, which grounds in the same call.
3. **Otherwise** (no entity anchor, or the ids you pulled don't answer it):

```bash
artmind query vector-text --shape brief --domain <d1> --domain <d2> --topK 10 --compact "<question>"
```

vector-text searches passages (~1200-char spans of the source text) by meaning and by keyword, fuses both rankings, groups hits into their parent chunk and returns them in relevance order. Each row is a parent chunk: `chunk.text` (the whole chunk), `passage` (`char_start`/`char_end` of the span that matched, inside `chunk.text` — quote from there), `relevance` (0-1, how close the best passage is to the question; comparable across questions; `null` when only keywords matched), `matched_by` (`vector`, `fulltext` or both) and `score` (rank order only — never compare it across calls). Ask for 10 rows: the right chunk is often just below the top 5. `--context passage` returns only the matching passages (smaller, for scanning many hits). Use it for "where/when/how did X happen", motivations, quotes, or whenever graph output is too thin. In hybrid answers, take entity/relationship facts from the graph and narrative evidence from chunk text. When you picked several domains, Ground should query all of them together in one call so results can be compared side by side.
