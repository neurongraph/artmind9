# Insights and the catalogue
Read for broad or retrospective questions, or to see what a domain already answers.

## Insights — for broad or retrospective questions

Some questions are about patterns rather than facts. Examples:
- "what's changed this year";
- "who matters most at work now";
- "what tends to go with bad days";
- "who have I lost touch with".

The domain owner's insights snapshot already answers those. Check it after Orient and Views (a saved view still comes first), before Discover:

```bash
artmind query insights list --domain <domain> --compact
```

Each finding has a `title`, a `narration` (a checked one-line caption, or null) and its `subjects` with their `_id`s.
- Use `--detector` to focus: for example `outcome_lift` for associations, `transitions` for life changes, `dormancy` for people gone quiet, or `communities` for circles.
- Use `--new` for "what changed".
- Use `artmind query insights show <key> --domain <domain> --shape brief --compact` for the numbers behind a finding.

When you answer from insights:
- **Ground each subject** you name with `artmind query entity-context --shape brief --domain <domain> --entityId <_id> --compact`, and cite as usual.
- **outcome_lift findings** always carry the caption "Co-occurrence on the same day, not cause." Pass it on. Never describe a lift as a cause.
- **Freshness:** state the snapshot's `as_of`. If an `artmind:` stderr line says the graph is behind the vault, say that the insights may be older than the latest notes.
- **Refresh:** never run `artmind insights refresh` yourself. If there is no snapshot, say so; the domain owner runs it.

## Catalogue and assets — what is already known

Before building an answer from the raw graph, check what the system already holds for the domain:

```bash
artmind query assets --domain <domain> --like "<the question>" --compact
artmind query catalogue --domain <domain> --compact
```

`assets` lists saved views, topic plans, insight findings and summary coverage, each with an `open` command and a `stale` flag. An insight is a lead to the cited sources, never evidence itself: ground what you take from it as usual. `catalogue` reports the domain's classes, stores, time coverage and `health`; `health` may be `absent`, and you must not run `artmind catalogue refresh` yourself.
