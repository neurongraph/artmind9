# Same-as: duplicate and linked entities

A same-as group in `same_as.yaml` (`.artmind/same_as.yaml` in a vault, committed) is a human's
bounded assertion that several aggregate keys denote one thing. Removing the group and rebuilding
un-merges as cleanly as adding it merged; nothing here is destructive graph surgery. Proposals wait
in a review queue; only an approved group changes the graph.

## Find candidates

Four sources feed or point at the same queue. Pick by what the user is after:

| Source | Finds | Command |
|---|---|---|
| Embedding adjudicator | entities close in meaning, judged by an LLM | `artmind sameas propose --domain <d> [--domain <d2>] [--maxPairs 20] --compact` |
| Name clustering | spelling and naming variants in one domain (cheaper, no embeddings) | `artmind ingest refine-graph --domain <d> --dryRun --output merges.json`, review the file, then `artmind ingest refine-graph --domain <d> --fromFile merges.json` |
| Insights | probable duplicates scored on neighbourhood, co-mention, time span and type (kinship names, initials) | `artmind insights same-as --domain <d> --export <file>` then `artmind ingest refine-graph --domain <d> --fromFile <file>` |
| `entity-resolve` twins | same-class entities with the same normalised name, reported but never merged | `artmind query entity-resolve --domain <d> --compact "<name>"`: read `twins` |

**Scope of `sameas propose`.** It pairs entities of the same class. With **one leaf domain** it pairs
inside that domain. With **two or more domains, or a family root** (which expands to its child
domains), it pairs each entity only with the *other* domains: two entities in the same child domain
are never compared. For duplicates inside one domain, pass that leaf domain alone, or use name
clustering.

`sameas propose` and `ingest detect-conflicts` share one adjudication: a "same thing" verdict writes a
same-as proposal, a "conflicting claims" verdict writes a conflict (conflicts.md). `--maxPairs` bounds
the LLM cost (about 20 s per pair on a local model); start with 20. `--simThreshold` (default 0.75)
or refine-graph's `--threshold` (default 0.7) go lower when duplicates are visible but nothing is
proposed. The insights export holds entity names from the vault: write it outside the code checkout.

Twins are not in the queue. When they are one thing, propose them with `sameas propose --domain <d>
--nameFilter "<name>"`, or ask the user to add a group by hand (below).

The Obsidian plugin's Curate row runs `sameas propose` as **Find duplicates…** and `ingest
detect-conflicts` as **Detect conflicts…**; its counts are the same queues.

## Review

```bash
artmind sameas list --status open --compact
```

Each proposal names a `canonical`, its `members`, its `source` (`adjudicator` or `refine_graph`) and
a `reason`. For each, judge:

- **One real-world thing?** For anything not obvious, read a source passage:
  `artmind query vector-text --domain <d> --topK 3 --compact "<member>"`.
- **Versions are not identities.** "Policy v2" and "Policy v3" may be one document at two times: a
  supersession (documents.md), not a merge.
- **A large cluster means extraction fragmented one concept**, not that every member belongs:
  spot-check names that could denote something distinct.
- **Merge or link.** Members of one class in one domain merge into one entity. A group across classes
  or domains links (`SAME_AS`) and each side keeps its own facts: right for "this policy entity and
  this SOP entity are the same real thing", wrong when the shared name is a coincidence.

Reject what does not hold: `artmind sameas reject <id> --reason "<why>"`.

## Approve

```bash
artmind sameas approve <id> [--canonical <key>] --dryRun --compact   # preview
artmind sameas approve <id1> <id2> <id3> --compact                   # a batch: one write, one rebuild
```

Approval appends the group to `same_as.yaml` and rebuilds the domains it touches. Never approve in a
shell loop: N calls pay N rebuilds. `--canonical` (a single id only) picks the member whose name wins
when the proposer's choice is not the right display name.

A proposal that cannot be approved does not stop a batch. It is listed under `skipped` with the
reason: unknown, already decided, or its canonical (or every other member) is already in an earlier
group. Read `skipped` and any group with `status: partial` (`dropped_members` went to another group).
With a single id a refusal is an error.

**A skipped proposal is usually one more name for an approved group.** There is no command to extend a
group, so per proposal:

- the existing group is right as it is: reject, "already grouped under <canonical>";
- the name belongs in it: the user adds it to that group's `members` in `same_as.yaml`. A member is an
  aggregate key `name|CLASS|domain` (an entity id is ignored; the name part is folded to lower case).
  Then `artmind projection rebuild --sweep --compact`, and reject the proposal, "folded into
  <canonical>";
- it is a different thing that only looks alike: reject it with that reason.

**Finish a session with a sweep.** `sameas approve` rebuilds only the touched domains, so it never
clears the global drift flag. After the last approval:

```bash
artmind projection status --compact            # same_as_drift: true
artmind projection rebuild --sweep --compact   # clears it and re-embeds what changed
```

Skipping it is harmless to queries but leaves drift reported. Proposals were computed before the
merges: re-propose after a batch so the next pass sees the merged entities.

## Verify

```bash
artmind query entity-resolve --domain <d> --topK 3 --compact "<a merged name>"   # resolves to the canonical
artmind query entity-context --domain <d> --entityId <id> --compact              # one description, all sources
```

If the merge changed an entity's observations a lot, offer `projection synthesize` (projection.md).

## Un-merge, or check a past decision

To see what fed an entity: `artmind query graph pattern2 --domain <d> --entityNameList "<canonical>"
--compact`. Then:

- **right**: leave it;
- **over-merged**: the user removes the group, or the wrong member, from `same_as.yaml`; then
  `artmind projection rebuild --domain <d> --compact` (or `--sweep`). The member returns under its
  original deterministic id; confirm with `entity-resolve` on its name;
- **should be a link, not a merge**: remove it as above and propose again with `sameas propose` over
  just the two domains, so the adjudicator can choose a link.

Proposals stay on the machine that made them; approved groups travel (travel.md).
