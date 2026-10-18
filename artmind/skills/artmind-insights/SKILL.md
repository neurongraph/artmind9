---
name: artmind-insights
description: Operate the artmind insights layer for a domain. Covers running and reading `artmind insights refresh`, tuning `domains/insights/<domain>.yaml`, and turning findings into action: schema edits, same-as curation, or saved views. Use for "refresh insights", "what did insights find", "why is this finding here", "tune the insights config", or "promote this insight to a view". Not for answering end-user questions; artmind-query reads insights for that.
---

# artmind insights (operator)

Insights are unprompted findings computed from a domain's graph and its schema, written to a snapshot in
`<vault>/.artmind/insights/<domain>/`, committed with the vault and private. Each refresh diffs against the
previous snapshot.

## Run and read

```bash
artmind insights refresh --domain <d> --compact      # needs Neo4j; prints counts, diff, skipped detectors and llm stats
artmind insights list --domain <d> [--detector X] [--kind corpus|health] [--new]
artmind insights show <key> --domain <d>
artmind insights runs --domain <d>
```

- **`up_to_date`** means the graph, schemas, curation, config and detector version are unchanged. Use `--force` to recompute anyway.
- **Skipped detectors list a reason.** "not configured" means a config block is missing (see Tune). Data reasons, such as "no document dates", are expected on some corpora.
- **Captions and circle names** come from the configured LLM. `--no-narrate` skips them. They are checked: a caption names only the finding's own subjects and uses only numbers in its stats. Prompts send entity names to the configured provider.

## Tune

```bash
artmind insights profile --domain <d> --compact      # class kinds, property vocabularies, relations, anchors
artmind insights config check --domain <d>           # errors exit 1; warnings name values never seen
```

Edit `domains/insights/<domain>.yaml`. Every key is optional:
- `anchors`, if the inferred anchor is wrong;
- `period`;
- `outcome`, which enables outcome_lift: a class, its property (default `type`), and positive and negative values;
- `open_loops`, which enables open_loops: a class, status property, and open and closed values;
- `transitions` (`extra_verbs`, `classes`);
- per-detector settings under `detectors:`.

Draft `outcome` and `open_loops` values from `insights profile`, which lists each property's vocabulary. Then run `config check`.

## Act on findings

- **Schema (kind_mismatch, vocabulary_sprawl, relationship_usage).** Propose the schema edit and hand off to **artmind-create-schema**. Examples:
  - change a class's `kind`;
  - list allowed values in a property hint;
  - fold synonym relationship types.

  Afterwards, `artmind ingest retime --redoProperties` refolds properties without the LLM.
- **Identity (same_as_candidates, split_signal).** Run:
  ```bash
  artmind insights same-as --export <path outside the checkout> --domain <d>
  artmind ingest refine-graph --domain <d> --from-file <path>
  ```
  This writes *proposals* to the same-as review queue. Review them with **artmind-curate**; nothing merges automatically. A split_signal pair is the highest priority, because the duplicate is distorting trends.
- **Curation (open_conflicts, fragments, attribution_suspects).** Hand off to **artmind-curate**. For attribution suspects, re-extracting the affected notes may be better: `artmind ingest sync --reextract --match <glob>`.
- **A finding worth asking again (any corpus finding).** Promote it to a view with **artmind-create-view**:
  - start from the finding's detector and stats as the question;
  - set `provenance.origin: promoted`;
  - use the finding's title as `source_question`.
- **Privacy.** Snapshots and exports hold vault content. Never copy them into the code checkout.
