# Alias identity: searchable aliases, per-name lookup, dangling groups, SAME_AS read

Branch `feat/alias-identity`. Comes out of the alias / same-as review (2026-11-15).

## Why

- `aliases` is stored on the Entity but is reached only by an exact, case-insensitive
  match. `entity_name_ft` indexes `name, description`, and the entity vector embeds
  `name: description`.
- Extraction is shown the ~25 names nearest to the first three chunks. A name that first
  appears later in the document gets no candidate, and nothing shows the extractor
  the aliases.
- A same-as group whose member keys no longer exist merges nothing, and nothing says so.
- `SAME_AS` edges are written in both directions, and no reader follows them.

## Tasks

1. **Aliases in fulltext.** `entity_name_ft` becomes `[name, description, aliases]`
   (`LIST<STRING>` is supported). Neo4j cannot alter an index, so `setup` reads
   `SHOW FULLTEXT INDEXES`, and if `aliases` is missing it drops the index and creates it again
   (`_ensure_entity_name_ft`). `entity-resolve`, `update` and every other reader get alias hits
   with no further change.
2. **Name embedding.** New `Entity.name_embedding` + vector index `entity_name_embedding`.
   The embedded text is `name_embedding_text(name, aliases)`, the name plus up to 8 aliases
   of 60 characters or less. The projection writes `_name_src`, a hash of that text (pure,
   recomputed on every rebuild). The entity embed sweep also embeds the name text when
   `_name_embed_key <> embed_key + ':' + _name_src`, through the embedding cache. `name_embedding`
   and `_name_embed_key` are preserved across rebuilds and stripped from output like `embedding`.
3. **Per-name lookup before canonicalization** (`canonicalize.lookup_name_candidates`).
   It looks up each distinct recurrent-class (name, class) the document extracted
   (up to 150), in two legs:
   - exact: an existing entity of the same class whose key equals the name's key, or
     one of whose aliases equals the name (case-insensitive); the name's own key is not
     shown, since it lands there anyway;
   - vector: the top 3 nearest on `entity_name_embedding`, same class and domain family, cosine
     ≥ `ARTMIND_NAME_LOOKUP_MIN_SIM` (default 0.80).
   The candidates go into the canonicalization prompt's existing `{{EXISTING_NAMES}}` block
   as a "closest existing names" list per name, so a vault's own `meta.yaml` needs no new
   token. It makes no LLM call, and the LLM still decides. `ARTMIND_NAME_LOOKUP=off` turns it off. Any
   failure means no candidates, never a failed ingest.
4. **Aliases in the up-front vocabulary.** `retrieve_vocabulary` also returns `aliases`, and
   `render_vocabulary` lists up to 3 short ones on their own indented line under the name.
5. **Dangling same-as groups.** Catalogue health field `same_as_dangling`: members of
   `same_as.yaml` groups in the domain family whose key has no `:Observation`.
6. **SAME_AS: one edge, read.** The rebuild writes one edge `(member)-[:SAME_AS]->(canonical)`.
   The existing clear already deletes both directions, so old pairs heal on rebuild.
   `entity-resolve` and `entity-context` rows carry `same_as: [{_id, name, entity_class,
   _domain}]` (deduplicated). `structural_schema` names `SAME_AS` for text2cypher. The
   artmind-query skill says what `same_as` means.

Not done: keeping curated aliases apart from raw extraction variants (the optional item).

## After merge, per graph

`artmind setup` inside each vault recreates `entity_name_ft` and creates
`entity_name_embedding`. The next embed sweep (`ingest embed-entities` / the next commit)
embeds the name texts, one embedding call per entity, served from the cache where it
can be. `projection rebuild --all` sets `_name_src` everywhere and leaves one SAME_AS edge per pair.
