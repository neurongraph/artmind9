---
name: artmind-refine-graph
description: Clean up duplicate entities within an artmind domain (refine-graph), find and report cross-domain conflicts (detect-conflicts), reconcile document version history via supersession, and investigate a surprising merge or conflict. Use for "clean up duplicates in X", "find conflicts between domain A and B", "why did these get merged", or "is this a real disagreement or just an older document".
---

# artmind Refine-Graph

Use this skill for the graph-maintenance operations that sit behind `artmind-query` and `artmind-update`: deduplicating fragmented entities within one domain, detecting and materializing genuine disagreements *across* domains, and telling apart a document's older revision (supersession — not a conflict) from a live disagreement. Background: `docs/refine-merge-conflict-supersede-guide.md`.

> For a full single-domain refresh after ingestion (every step in dependency
> order, including description consolidation), use the `artmind-refine` skill,
> which drives `artmind ingest refine-pipeline`. This skill remains the right
> tool for the targeted workflows below — especially cross-domain conflict
> detection, which the single-domain pipeline does not cover.

## Grounding & Safety Rules

- **Never apply without a reviewed dry-run.** `refine-graph` apply deletes alias entity nodes — always run `--dry-run --output <file>` first, look at the proposals, then apply via `--from-file <file>`. `detect-conflicts` apply is non-destructive (MERGE-only) but still costs real LLM time — dry-run first regardless.
- **Warn about cost before running `detect-conflicts` for real** (not `--dry-run` alone — the dry-run itself does the LLM adjudication work). Default `--maxPairs 200` can mean up to 200 LLM calls; observed real-world timing is roughly 20 seconds per candidate (~65–75 minutes for 200 on a local model). Tell the user this **before** starting, and confirm they want to proceed rather than lowering `--maxPairs` first.
- **Don't trust alias-cluster size as a merge-quality signal.** A high alias count is evidence extraction over-fragmented one concept, not evidence the merge itself is correct — always spot-check a few aliases against source chunks (Workflow 3) before recommending an apply, especially for clusters above ~10 aliases.
- **Reconcile before detecting.** Run `detect-supersession` (and manual `ingest supersede` where notice-parsing can't find both documents) before `detect-conflicts`, so version-history noise doesn't get flagged as a live disagreement.
- **Cross-domain merges are guarded by design, not a bug to route around.** When refine-graph runs without `--domain` (or spans domains via `--allow-cross-domain-merge`), same-named entities across domains are skipped by default — this is what keeps them available for `detect-conflicts` to evaluate. Don't reach for `--allow-cross-domain-merge` just because a cluster looks obviously the same; it removes the very pairs conflict detection needs.

## Required Inputs

- `domain` (or `domains`, for cross-domain workflows): ask if not provided.
- The specific workflow the user wants (see below) — infer from phrasing, confirm if ambiguous.

## Workflow 1 — Clean up a domain's duplicate entities

1. `refine-graph` and `detect-conflicts` both warn (not block) if this domain has no prior `RefineRun` marker — no separate precondition check needed, just proceed and read the warning if one appears.
2. Dry-run:
   ```bash
   uv run artmind ingest refine-graph --domain <domain> --dry-run --output data/refine/proposed_merges_<domain>.json --compact
   ```
3. Review proposals before applying:
   - Sort/scan by alias-list length. For any cluster with **more than ~10 aliases**, or any alias name that looks like it could denote something distinct (e.g. an alternative rather than a synonym — see the "Biometric Authentication" over-merge case in the field guide §3.1), pull a source chunk and check:
     ```bash
     uv run artmind query vector-text --domain <domain> --topK 3 --compact "<the questionable alias name>"
     ```
     Judge: legitimately the same real-world thing, or a distinct concept that happens to co-occur? Report anything you'd flag as an over-merge to the user before applying.
   - Present the proposal summary to the user: cluster count, total merges, any flagged clusters, before asking to proceed.
4. Apply only after the user confirms:
   ```bash
   uv run artmind ingest refine-graph --from-file data/refine/proposed_merges_<domain>.json --compact
   ```
5. Report: nodes merged, aliases folded, any clusters you flagged as questionable and whether the user chose to keep or exclude them.

**Filtering to specific names** (e.g. mid-`artmind-update` session, cleaning up just the entities you touched):
```bash
uv run artmind ingest refine-graph --domain <domain> --filter "<name1>,<name2>,..." --dry-run --output merges.json
```

## Workflow 2 — Find and report cross-domain conflicts between domain A and domain B

1. Confirm both domains have been refined (Workflow 1) — if not, offer to run refine-graph first. Comparing un-deduplicated entities wastes LLM calls on candidate pairs that are really the same fragmented node.
2. Reconcile version history first — run supersession detection on each domain so genuine document revisions aren't mistaken for conflicts:
   ```bash
   uv run artmind ingest detect-supersession --domain <domain> --dry-run --compact
   ```
   Apply (drop `--dry-run`) for real matches; for notices that don't auto-match (older doc's markdown not locatable — see field guide §3.5), use the manual command:
   ```bash
   uv run artmind ingest supersede --domain <domain> --newer "<name>" --older "<name>" --effective <ISO-date> --compact
   ```
3. **State the cost/time estimate to the user and get confirmation before running the real detection pass** (see Safety Rules). Then dry-run:
   ```bash
   uv run artmind ingest detect-conflicts --domain <domainA> --domain <domainB> \
     --maxPairs 200 --dry-run --output data/refine/conflicts_<domainA>_<domainB>.json --compact
   ```
4. Review the dry-run proposals with the user, then materialize:
   ```bash
   uv run artmind ingest detect-conflicts --domain <domainA> --domain <domainB> \
     --from-file data/refine/conflicts_<domainA>_<domainB>.json --compact
   ```
5. Produce a human-readable summary, **grouped by root cause, not a flat list** — the field guide's real example collapsed 14 pairwise `Conflict` nodes into one root disagreement (two documents classifying the same set of address-verification documents into different tiers). Pull the materialized set:
   ```bash
   uv run artmind query graph conflicts --domain <domainA> --domain <domainB> --compact
   ```
   Cluster by shared `aspect` wording / shared entity sets before presenting; call out severity and cite both documents' provenance for each distinct finding.

## Workflow 3 — Investigate a surprising merge or conflict

Given an entity name (surprising merge) or a `Conflict` id/aspect (surprising conflict):

**For a merge**, pull the entity's full alias list and a few source chunks:
```bash
uv run artmind query graph pattern2 --domain <domain> --entityNameList "<canonical name>" --compact
uv run artmind query vector-text --domain <domain> --topK 5 --compact "<one alias that looks off>"
```
Render a judgment: reasonable (same real-world thing, different phrasing/context) / over-merged (should be split back out — note this to the user, there is currently no built-in "un-merge" command, so record the recommendation rather than attempting a fix) / needs human review (ambiguous from the evidence available).

**For a conflict**, pull its full evidence:
```bash
uv run artmind query graph conflicts --domain <domainA> --domain <domainB> --entityName "<name>" --compact
```
Read both sides' `evidence` chunk text. Check `status` and whether either underlying document has since been superseded (`valid_to` set) — if so, note the conflict may now be resolvable by re-running detect-conflicts, not by manual edit.

## Workflow 4 — "Is this a real conflict or just an older document?"

Given two documents that look like they disagree:
1. Check if either has `valid_to` set or a `SUPERSEDES` edge already:
   ```bash
   uv run artmind query graph timeline --domain <domain> --entityId <id> --compact
   ```
2. If neither is marked superseded but they're clearly the same document lineage (same title, sequential version numbers), look for a "Supersession Notice" section and apply Workflow 2 Step 2.
3. Once supersession is applied, re-answer time-qualified questions with `--asOf <date>` (present-tense) or without it (historical) — see `artmind-query` skill's Adjudicate step for how to phrase this to the user.
4. If the documents are genuinely from different domains/authorities describing the same real-world thing differently (not a revision relationship), this is a live conflict, not history — route to Workflow 2 instead.

## CLI Quick Reference

| Command | Effect | Destructive? |
|---|---|---|
| `ingest refine-graph --domain D --dry-run --output F` | Compute merge proposals | No (read-only) |
| `ingest refine-graph --from-file F` | Apply merges (deletes alias nodes) | **Yes** |
| `ingest refine-graph --domain D --filter "n1,n2"` | Scope merge detection to specific names | No (with `--dry-run`) |
| `ingest detect-supersession --domain D --dry-run` | Find explicit Supersession Notice sections | No |
| `ingest supersede --domain D --newer N --older O --effective DATE` | Manually assert a supersession | Sets `valid_to`/`superseded_by` — not deletion, but not reversible via CLI |
| `ingest detect-conflicts --domain A --domain B --dry-run --output F` | Candidate pairing + LLM adjudication | No (dry-run); costly (LLM time) |
| `ingest detect-conflicts --from-file F` | Materialize `Conflict` nodes | No (MERGE-only, additive) |
| `query graph conflicts --domain A --domain B [--entityId/--entityName] [--status]` | Read materialized conflicts | No |
| `query graph timeline --domain D --entityId ID` | Render an entity's ordered events/state-changes/supersessions | No |
| `query domains-overview` | Cheap per-domain routing summary | No |

Full flag reference: `docs/refine-merge-conflict-supersede-guide.md` §2.

## Known Caveats (tell the user when relevant)

- Re-running `detect-conflicts` is not a guaranteed no-op — `Conflict.id` hashes the aspect text, so re-phrased LLM output for the same underlying dispute creates a *new* node rather than updating the old one.
- `EVIDENCE` edges accumulate and are never pruned as new documents are ingested.
- `--allow-cross-domain-merge` only affects the clustering path, not `--from-file` applies.
- `detect-conflicts` cost (`maxPairs` × LLM call) is not bounded by any rate-limit guard beyond the flag itself.
