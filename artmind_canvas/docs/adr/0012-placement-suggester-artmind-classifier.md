# Placement suggestion is an artmind classifier, not client-side logic

New knowledge — authored in the editor or via chat — needs its placement inferred:
`domain`, `area`, `project`, `tags`, and the target file/section. artmind has no
content→domain classifier today (domain is 100% manual, defaulting to `general`); the
only precedent is the structured store's `db propose` (column→entity-class within a given
domain, with confidence, persisted unconfirmed). The fork was: run the inference as a
client-side prompt, or build it as an artmind capability.

We decided (Q24): **build it as an artmind capability, consumed by the canvas.** Three
artmind-side pieces:
1. A new **classifier command** — given a block of text (plus optional context), it
   proposes `{domain, area, project, tags, target file/section}` with confidence,
   suggesting from the **existing controlled vocabulary** rather than inventing labels.
2. A new **vocabulary-retrieval command** — returns the distinct `project` / `area` /
   `tags` (and `domain`) values already in the graph, so proposals stay consistent
   (`Alpha`, not a fresh `proj-alpha`).
3. **Neo4j indices** on the filing-metadata properties (ADR 0010) so both vocabulary
   retrieval and metadata filtering are fast.

The interaction is **propose → review → confirm, never silent auto-filing**: the agent
proposes, the user accepts/adjusts in a **placement Card**, and only then is frontmatter
written and the doc re-ingested (the doc-first path, ADR 0002/Q14). Because a **domain**
change forces re-extraction (ADR 0006 (f) / Q23c), domain is **always explicitly
confirmed**; `area`/`project`/`tags` may default to accepted and be corrected freely.

Why: the user wants a reusable classifier — the chat-ui and admin-ui benefit too, and it
is a genuine artmind capability rather than a canvas-only trick; grounding it in the
graph's own vocabulary keeps labels consistent; indices keep it fast; propose → review →
confirm mirrors the proven `db propose` pattern.

Consequences:
- Adds to the **artmind workstream** (alongside ADR 0006), landing in the artmind
  package. The canvas stays a **pure client** (ADR 0003) consuming the classifier and
  vocabulary commands.
- The classifier only *suggests* — it never writes. All writing goes through the
  confirmed doc-first path.
