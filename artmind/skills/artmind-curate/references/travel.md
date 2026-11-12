# What travels to other machines

In a vault, every curation decision is a file that Obsidian Git commits and `artmind vault sync`
applies on another machine, so a machine with its own Neo4j ends up with the same curation.

| Decision | File |
|---|---|
| Same-as groups | `.artmind/same_as.yaml` |
| Conflict resolutions | `.artmind/data/curation/conflicts/` |
| Supersessions | `.artmind/data/curation/supersessions/` |
| Retirements | `.artmind/data/curation/lifecycle/` |
| Syntheses | `.artmind/data/curation/syntheses/` |
| Mart members and periods | `.artmind/data/curation/mart_dimension/`, `mart_calendar/` |
| Table classifications | the table's `.meta.json`, rewritten by `db grain` / `db mappings` / `db bridge` / `db propose` |

What does not travel: the same-as **proposals** queue, and conflicts or proposals computed but not
yet decided. They stay on the machine that ran the proposer.

## A record conflict after a pull

When two machines wrote the same record before pulling, Obsidian Git's merge stops on that file.
`artmind vault status --compact` lists unresolved ones; `artmind vault resolve` settles them the same
way on every machine:

```bash
artmind vault resolve --dryRun --compact   # show its pick
artmind vault resolve --compact            # stage the pick; Obsidian Git concludes the merge
```

Its rules: a decision (resolved or dismissed) beats open, then the later change wins; a manual
supersession beats a detected one; the later synthesis wins. It never commits, and it never touches
`same_as.yaml`: a conflict there is reported, and the user merges the groups by hand.
