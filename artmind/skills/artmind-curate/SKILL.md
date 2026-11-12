---
name: artmind-curate
description: Curation for artmind domains after ingestion — same-as identity review (merging duplicate entities, linking cross-domain identity), conflict adjudication, document supersession and retirement, graph-health findings from insights, description synthesis, the structured store's table classifications (grain, bridge columns, column→entity_class mappings), and mart dimension review. Use for "curate domain X", "clean up duplicates", "review the same-as queue", "find conflicts between A and B", "this document replaces that one", "retire/archive a document", "what did insights flag for curation", "review the proposed mappings", or "review the mart".
---

# artmind Curate

Curation is the human judgment artmind keeps out of ingestion: which names are one thing, which
disagreement is real, which document replaces which, what a table's columns denote. Each kind is a
few granular commands. This file says what is waiting and where to go; each kind's detail is in one
reference file, opened when the work needs it.

## Rules for every kind

- **Preview first.** A write command takes `--dryRun`: it plans from reads, writes nothing, calls no
  LLM and returns an `effect` block (what changes, what goes stale, LLM calls, how to undo it,
  whether it travels). Read `effect`, show the user, then run it for real. A few commands' `--dryRun`
  is not built yet (`db grain --set`, `docs restore-from-archive`): it fails with "not available ...
  yet; nothing was changed", so state the change to the user in words instead.
- **The user decides.** Propose; write only on the user's yes. Hand-editing `same_as.yaml` is always
  the user's call: name the group and the member, and wait.
- **Read the evidence before any decision.** A cheap undo does not make an unread approval cheap for
  the next reviewer.
- **Batch.** Approve several same-as proposals in one call; every call pays one rebuild.
- **Not mid-ingestion.** If worker jobs are still processing the domain (`artmind ingest
  jobs-active`), curate afterwards.
- **Reach for an `artmind` command before the filesystem.** Schemas, records and queues are read
  through the CLI.
- **Domains.** Ask which domain(s) when the user does not say.

## 1. What is waiting

No single command gathers the queues. Run the reads that match the request, or all of them for
"curate domain X", and tell the user the size of each before starting:

| Queue | Read (all read-only) | Empty means |
|---|---|---|
| Same-as proposals | `artmind sameas list --status open --compact` | no proposals waiting |
| Conflicts | `artmind query graph conflicts --domain <d> --compact` | no open conflict |
| Table classifications | `artmind db review --domain <d> --compact` | `pending_count` 0 |
| Projection drift / unembedded | `artmind projection status --compact` | no drift, nothing unembedded |
| Graph health | `artmind insights list --domain <d> --kind health --compact` | no health finding in the latest snapshot |
| Mart members | `artmind mart status --domain <d> --compact` | no unreviewed proposal (domains with a mart only) |
| Cross-machine merges | `artmind vault status --compact` | no unresolved record conflict |

An empty queue can also mean nothing has proposed anything yet: say so, and offer the proposer
from the matching reference.

## 2. Where to go

| The work | Open |
|---|---|
| Duplicate entities: find, review, approve or reject, un-merge, link vs merge; `entity-resolve` twins | [references/same-as.md](references/same-as.md) |
| Two sources disagree: detect, read, resolve or dismiss | [references/conflicts.md](references/conflicts.md) |
| A document replaces another, or should leave the indexes: supersede, unsupersede, retire, restore, archive | [references/documents.md](references/documents.md) |
| Insights health findings: fragments, attribution suspects, open conflicts, same-as candidates, split signals, schema findings | [references/graph-health.md](references/graph-health.md) |
| Projection rebuild, drift, description synthesis, recovery | [references/projection.md](references/projection.md) |
| Structured tables: grain, bridge columns, column mappings | [references/tables.md](references/tables.md) |
| Mart dimensions and named calendar periods | [references/mart.md](references/mart.md) |
| What travels to other machines; a record conflict after a pull | [references/travel.md](references/travel.md) |

Two kinds often lead into another: a conflict between two versions of one document is a
supersession (documents.md), and a same-as approval that changed an entity's observation set is a
reason to re-synthesize its description (projection.md).

## 3. Report

Say what was decided, what was written (with the `effect` summary), what was left open and why, and
what goes stale (topics, views) according to `effect`. Group conflicts by root cause, never as a flat
list.
