# Projection: rebuild, drift, synthesis

Entities are derived from observations by the projection. A rebuild runs on its own inside whatever
changed it (a document commit, `docs retire`, `sameas approve`). Call these directly only when no
other command will.

```bash
artmind projection status --compact                          # drift, unembedded entities/passages, unprojected keys, per domain
artmind projection rebuild [--domain <d>] --dryRun --compact # recompute entities from observations; no LLM
artmind projection rebuild --sweep --compact                 # every domain, clears drift, then both embedding sweeps
```

`--sweep` is the repair for drift after same-as edits and for anything left unembedded.

## Synthesis

`projection synthesize` rewrites an entity's description as one passage drawn from all its
observations. It is the one curation step that spends LLM budget on its own, so it is always a
separate, deliberate call with the user's yes:

```bash
artmind projection synthesize --domain <d> [--nameFilter <f>] [--limit N] --dryRun --compact
artmind projection synthesize --domain <d> [--nameFilter <f>] [--limit N] --compact
```

Run it after a bulk ingest or after a same-as approval that changed an entity's observations a lot.
It skips an entity with an open projection conflict, fewer than `--minObservations` (default 2), or an
unchanged observation set (unless `--force`). Each synthesis is a record file and travels, so other
machines get the same description with no LLM call. Syntheses made before records existed travel
only after `--force` re-runs them.

## Recovery

- High `failed_embedding` counts: check the embedding service or pass `--model`; re-running is safe
  (an unchanged entity is skipped, not billed again).
- An interrupted `sameas approve` or `projection rebuild`: run the same command again.
