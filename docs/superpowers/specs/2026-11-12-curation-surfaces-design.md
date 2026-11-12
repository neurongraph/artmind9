# Curation surfaces: a Curate tab in the admin console, note commands in the plugin

Status: design, written for review 2026-11-12. Plan and build in a separate session. Tracks issue #15
(widened); closes #19 (a duplicate of #15's design). Builds on the archived Phase 8 design
`docs/admin-ui-curation-workflow.md` (removed in 323790cb, its text kept in issue #19), whose
blocker #12 is fixed and whose rebuild-per-approval cost is gone (`sameas approve` takes a batch: one
write, one rebuild).

## 0. Problem

Curation is the judgment artmind leaves to a person: which names are one thing, which disagreement is
real, which document replaces which, what a table's columns denote, where a place sits. The CLI covers
all of it and the `artmind-curate` skill (rewritten 2026-11-12, e7c667e0) routes it, but there is no
place where a person reviews a queue:

- The Obsidian plugin's Curate row counts open same-as proposals and conflicts, runs the two
  proposers, and hands the judgment to the admin console's **chat agent** as a prompt.
- The admin console has nine tabs (Ingest & browse, Insights, Benchmark, Maintenance, Structured data,
  CLI guide, Schemas, Views, Topics). Insights can send same-as candidates to the queue; Structured
  data can re-run a table's proposals. None shows a queue to decide.

Phase 8 showed the cost: 180 same-as proposals and 12 conflicts, reviewed through a hand-built page
because JSON in a terminal or chat does not scale to review.

## 1. Split

| Surface | Holds | Why there |
|---|---|---|
| Admin console, new **Curate** tab | every review queue: decide, preview, apply | review is browsing: grouping, filters, search, select-many, evidence side by side, and the agent in the same app |
| Obsidian plugin | the Curate row (counts, proposers) linking into the tab; **note commands** for acts on the open document | the plugin stays a thin CLI runner; acts about one document start from that document |

No curation logic is duplicated: both surfaces call what the CLI calls (the admin console in process,
the plugin through `artmind --compact`). Every write shows its `--dryRun` `effect` before it runs.

## 2. Curate tab (admin console)

A tenth tab, `data-tab="curate"`, in `templates/dashboard.html`; routes in a new
`artmind/webui/curate_routes.py` (`register_curate_routes(app)`, admin profile only, like the other
dashboard routes); page code in `static/curate.js`, styled by `dashboard.css`. Every text from the
graph is rendered with `textContent` (no `innerHTML`), as the Insights and Views tabs do.

### 2.1 Layout

- **Overview strip**: one count per queue (same-as open, conflicts open, tables with
  `pending_count`, graph-health findings, mart proposals, projection drift), from the reads in the
  skill's "What is waiting" table. A queue with nothing proposed yet says so and offers its proposer.
- **Queue panes**, one per kind, chosen from the strip. A new deep link
  `/dashboard?tab=curate&queue=<kind>` opens one (the dashboard reads no query parameters today; the
  plugin uses it, §3.1).
- A domain filter shared by all panes.

### 2.2 Same-as pane (first)

- Rows from `sameas.list_proposals(status)`, grouped by entity class, collapsible, with counts,
  search, and source (`adjudicator`, `refine_graph`) and status filters.
- Each row: canonical, members (aggregate keys shown as name, class, domain), reason, and a
  **Merge / Link** badge from the same planning `approve` uses (`sameas._plan_group`): merge when the
  members share class and domain, link (a `SAME_AS` edge, both entities kept) otherwise. A link is
  never a surprise.
- **Evidence** on demand per member: the entity's top source passages (the `vector-text` /
  `entity-context` reads the skill uses), loaded when a row is opened, not with the list.
- Select rows, then **Approve** or **Reject (with a reason)**. The action first calls the dry run
  (`approve_many(ids, dry_run=True)`, `reject(id, dry_run=True)`) and shows its `effect` (groups,
  merges vs links, domains rebuilt, what goes stale, undo); **Confirm** runs it. One approve call per
  batch: one write, one rebuild. `skipped` and `status: partial` are shown on the rows they concern.
- **Canonical override** on a single row (`--canonical`).
- After the last approval of a session the pane shows `projection status` drift and offers **Sweep**
  (`projection rebuild --sweep`), with its own preview.

Decisions are written when confirmed. The Phase 8 design's "decide now, apply later" status-only
writes are not needed now that a batch approve costs one rebuild; a reviewer selects a batch, previews
and confirms it.

### 2.3 Conflicts pane

- Rows from `query graph conflicts` (status filter open / resolved / dismissed / all), grouped by
  shared aspect wording and entities (the skill's "group by root cause").
- Each row shows both sides with their documents and evidence passages, and a hint when one side's
  document has a later version (`valid_to` set or a `SUPERSEDES` edge): "one side is an older version:
  record the supersession", linking to the plugin's note command or showing the CLI line.
- **Resolve** / **Dismiss** with a required reason, previewed (`resolve_conflict(..., dry_run=True)`).

### 2.4 Tables pane

- `db review --domain`: per table, unconfirmed mappings, bridge columns and grain, with each column's
  `distinct_sample` from `db schema` beside its proposal.
- Per row: confirm, clear (warning that it drops every class on the column), set a missed class;
  per table: confirm grain, accept all proposed (only after every row is expanded once). Then
  **Push to graph** (`db catalogue`).
- `db grain --set` has no dry run yet: the pane states the change in words before confirming.
- The existing Structured data tab keeps re-running proposals (`db propose`); this pane only judges.

### 2.5 Graph-health pane

- `insights list --kind health` for the domain, each finding with its examples.
- Actions by kind, as in the skill's `graph-health.md`: same-as candidates and split signals →
  **Send to same-as queue** (the existing `/api/insights/same-as/propose`); fragments and attribution
  suspects → evidence plus the re-extract command to copy; schema findings → a link to the Schemas tab.
- Read-only otherwise.

### 2.6 Mart pane (domains with a mart)

- `mart status` and `mart review list`; per member, **Set levels** (a `mart review set` user record,
  previewed), then **Rebuild mart** (`mart build`). `mart enrich --llm` stays in the CLI or chat: it
  spends LLM budget.

### 2.7 Ask the agent

Each pane has **Ask the agent about these**: it opens the console chat pre-filled (the existing
`/?ask=` path) with the pane's scope, e.g. "Review the 12 open same-as proposals in class PRODUCT and
suggest decisions with reasons". The agent proposes; the person decides in the pane. This replaces the
Phase 8 design's built-in "suggest the rest" LLM endpoint: no new LLM path, and its cost shows in the
chat like any agent turn.

### 2.8 Routes

All JSON, all scoped by `domain`, all writes taking `dry_run` (true returns the `effect` and writes
nothing):

| Route | Calls |
|---|---|
| `GET /api/curate/overview` | the queue counts of §2.1 |
| `GET /api/curate/sameas?status=&domain=` | `sameas.list_proposals` + merge/link classification |
| `GET /api/curate/sameas/{id}/evidence` | source passages per member |
| `POST /api/curate/sameas/approve` `{ids, canonical?, dry_run}` | `sameas.approve_many` |
| `POST /api/curate/sameas/reject` `{ids, reason, dry_run}` | `sameas.reject` per id |
| `GET /api/curate/conflicts?status=&domain=` | the `query graph conflicts` read |
| `POST /api/curate/conflicts/{id}/close` `{status, reason, dry_run}` | `conflicts.resolve_conflict` |
| `GET /api/curate/tables?domain=` | `db review` + `db schema` samples |
| `POST /api/curate/tables/{table}/mapping` `{action, column, entity_class?, dry_run}` | the `db mappings` subcommands |
| `POST /api/curate/tables/{table}/grain` `{grain}` | `db grain --set` |
| `POST /api/curate/tables/{table}/bridge` `{action, column, dry_run}` | `db bridge confirm|clear` |
| `POST /api/curate/catalogue` `{domain}` | `db catalogue` |
| `GET /api/curate/health?domain=` | `insights list --kind health` |
| `GET /api/curate/mart?domain=` / `POST /api/curate/mart/member` | `mart status` + `review list` / `mart review set` |
| `POST /api/curate/sweep` `{dry_run}` | `projection rebuild --sweep`, run as a background job with progress (the ingest dashboard's polling pattern) |

Where the logic sits in a Click command body rather than a library function, the build moves it into a
function both call; the CLI's output does not change (the plugin fixtures stay valid). Neo4j work runs
off the event loop (`asyncio.to_thread`), as `insights_routes` does.

## 3. Plugin

### 3.1 Curate row

Counts and the two proposers stay. Its review buttons open the Curate tab at the matching queue
(`/dashboard?tab=curate&queue=sameas|conflicts`) instead of sending a chat prompt; the chat prompt
stays reachable from the tab (§2.7). The row also counts tables with unconfirmed classifications
(`db review`), linking to the tables pane, and replaces the plugin's separate table review modal's
"ask admin UI" button with the same link.

### 3.2 Note commands

Commands on the active note, resolving it to its domain and document through the vault mappings the
plugin already reads:

| Command | Runs |
|---|---|
| artmind: This note supersedes… | pick the older note (fuzzy picker over the domain's documents), optional scope and effective date → `ingest supersede --dryRun`, show `effect`, confirm, run |
| artmind: Undo a supersession of this note | `ingest unsupersede`, same flow |
| artmind: Retire this note | `docs retire` |
| artmind: Restore this note | `docs restore` |
| artmind: Archive this note | `docs archive`, with a second confirmation naming the vault file it deletes |
| artmind: Show this note's history | `query document-history`, read-only, in a modal |

Each uses the existing confirm modal with the `effect` summary, travels like any curation record
(Obsidian Git commits the record file), and refreshes the checklist. Archive is the only command that
deletes a vault file; its modal says so and that `docs restore-from-archive` is the way back.

## 4. Not in scope

- Running `sameas propose`, `detect-conflicts` or `mart enrich --llm` from the tab (proposers stay in
  the plugin row and CLI; they spend LLM budget).
- Editing `same_as.yaml` groups by hand in the UI (extending or splitting a group): the skill's
  manual path stays; a later pane can add it.
- Projection-produced conflicts (no CLI lists them yet, issue #16).
- Multi-user locking: one operator at a time, as every admin console tab.
- A new LLM path ("suggest the rest" is the chat agent, §2.7).

## 5. Tests

- `test/test_curate_routes.py` (FastAPI `TestClient`, mocked graph): every write route sends no write
  with `dry_run: true` and returns the `effect`; with `dry_run: false` it calls the library function
  with the ids and parameters given (assert on the parameters sent, per CLAUDE.md §3); merge/link
  classification on synthetic proposals; the admin profile registers the routes, the chat profile does
  not.
- The CLI's JSON is unchanged where logic moved into shared functions (existing CLI tests and the
  plugin fixtures).
- Plugin (vitest): the Curate row's links carry the right `queue`; each note command builds the right
  argument list, shows the dry-run effect before running, and archive asks twice.
- Synthetic text only in tests and fixtures; no vault content.

## 6. Build order

1. Routes + same-as pane + overview + plugin row links (the Phase 8 pain).
2. Conflicts pane.
3. Plugin note commands (supersede first).
4. Tables pane (and the plugin's table review link).
5. Graph-health and mart panes.

Each step ships on its own. Measure step 1 on banking: review the open same-as queue in the tab end to
end, without the CLI or chat.
