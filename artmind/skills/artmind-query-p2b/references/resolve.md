# Resolve names to entities
Read when a result carries `guide: resolve` (`twins`, `needs_disambiguation`, `no_match`).

### 3. Resolve — map question names to exact graph nodes

Most wrong answers come from name mismatch: the user says "Holmes", the graph has "Sherlock Holmes" AND "Mycroft Holmes", and substring matching silently merges them. Before running retrieval patterns:

1. Resolve every entity reference in the question:

```bash
artmind query entity-resolve --domain <domain> --topK 5 --compact "<name fragment or description>"
```

This combines Lucene full-text over entity names/descriptions with vector similarity over entity embeddings (RRF), so it handles both name fragments ("Holmes") and purely descriptive references ("the detective"). Each row's `entity` carries `_id`, `name`, `entity_class`, `description`, plus `observation_count` and `connection_count` — how much the entity has aggregated. `score` is the fused RRF score (rows from both legs rank highest; `matched_by` names the legs), comparable across rows. (Alternatives: `entity-listing --nameFilter` for plain fragments, `pattern7 --searchTerm` for fulltext-only.)

`entity-resolve` maps I/me to the narrator and groups same-class twins (`twins`): treat a twin group as one entity unless the text distinguishes them. Use the chosen entity's exact `_id` in retrieval (`--entityId` / `--entityId1` / `--entityId2` / `--entityIdList`).
