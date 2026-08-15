# Filing taxonomy is ingested graph metadata, merged with domain into one queryable space

The user organizes notes by project / area / tags. artmind had no such concept — its only
organizing axis is `domain`, and `domain` is *functional* (it selects the extraction
schema), not a label. The question was whether the canvas keeps a separate filing index
alongside the graph, or whether the filing taxonomy is folded into artmind itself.

We decided to **merge the stores, not the fields** (Q19): project / area / tags become
first-class **ingested metadata properties** in the graph, so the canvas queries a single
unified metadata space rather than maintaining a parallel filing index. `domain` stays a
distinct, functional axis.

Placement (the "designed carefully" part):
- **Document** carries the filing metadata authoritatively.
- **DocChunk** carries a denormalized copy for fast filtering.
- **Shared Entity nodes never carry it** — an entity can be extracted from documents in
  different projects, so "entities in project Alpha" is derived by traversal
  (`Entity ←EXTRACTED_FROM— DocChunk WHERE project = 'Alpha'`), not stored on the entity.
- Baseline fields `created_on` / `modified_on` / `title` are always present, defaulted
  from filesystem timestamps or first-ingest time when frontmatter omits them.

Source of truth (Q20): the metadata is authored in **YAML frontmatter** in the markdown
file (portable, git-diffable, survives external editors — consistent with ADR 0002).
Ingest reads it and projects it into the graph; the graph copy is a projection you filter
against. Folders are a human-browsing convenience only, never the authority.

Update semantics (Q23): a metadata-only edit updates the graph in place (no supersede);
content or domain changes re-ingest. See ADR 0006 (e)/(f) for the pipeline mechanics.

Why: one metadata space means "show me project Alpha" is a graph filter, not a join
across two systems; keeping `domain` separate preserves its functional role; keeping
metadata off shared entities avoids semantic nonsense and re-tag thrash.

Consequences:
- This is an artmind ingest enhancement (ADR 0006 workstream, artmind package). The
  canvas stays a pure client (ADR 0003) — it authors frontmatter and reads the graph.
- The graph's usefulness as the retrieval index now depends on the Vault and graph
  staying in sync — which sharpens the open retrieval/sync question (Q22).
