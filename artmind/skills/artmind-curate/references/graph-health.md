# Graph-health findings from insights

`artmind insights refresh` writes a snapshot per domain; its `health` findings are where the graph and
its schema disagree. The artmind-insights skill runs and tunes the detectors and hands the curation
findings here. Read them from the latest snapshot (no Neo4j needed):

```bash
artmind insights list --domain <d> --kind health --compact
artmind insights show <key> --domain <d> --compact          # one finding's stats and examples
```

A finding is a lead, not a verdict: check the examples before acting. Snapshots hold private corpus
data; never copy them into the code checkout.

| Finding | What it suggests | Act |
|---|---|---|
| `same_as_candidates` | two names for one entity | export to the queue: `insights same-as --export`, then review (same-as.md) |
| `split_signal` | a duplicate whose halves show opposite trends | the highest-priority same-as review: the split distorts trends |
| `fragments` | small groups of entities cut off from the main graph | read the examples. A member that duplicates a main-graph entity is a same-as case; a group no text links is an extraction gap (re-extract its documents) |
| `attribution_suspects` | targets of a focused relation whose class or type is rare for it, probably attached to the wrong subject | read their source passages. Wrong extraction: re-extract the notes (`artmind ingest sync --reextract --match "<glob>"`, artmind-ingestion-helper). A real case: leave it |
| `open_conflicts` | a class with open conflicts | conflicts.md |
| `kind_mismatch`, `vocabulary_sprawl`, `relationship_usage` | the schema and the data disagree (a property's values sprawl past its hint; relations used but undeclared, or declared but unused) | schema work, not curation: hand to artmind-create-schema |

After curation, `artmind insights refresh --domain <d>` shows whether the finding cleared; it
short-circuits when nothing changed.
