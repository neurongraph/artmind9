# 01 — Current state of artmind-query (baseline)

Baseline: commit `89da8fa` (2026-10-19). Re-check against `git log -- artmind/skills/artmind-query`
before relying on any line number below.

## Size

| File | Words |
|---|---|
| `SKILL.md` | 5,031 (463 lines) |
| `references/structured-store.md` | 1,508 |
| `references/temporal.md` | 696 |
| `references/conflicts.md` | 664 |
| `references/hierarchy.md` | 657 |
| **Total** | **8,556** |

`SKILL.md` is loaded whole before the first command. Of it, only the grounding rule, the
entry calls, the routing table and Sources apply to every question; the rest is
situational.

## Where routing lives

### In the skill (9 places)

1. **"The first two commands, every time"** — `domains-overview`, then `views list` per domain.
2. **Insights** — a section placed before the protocol ("after Route and Views, before Discover").
3. **Route** — exact domain vs. several named domains vs. an "area" → a routing sub-agent
   that returns `{domains, views, resolved_entities, relevant_classes, relevant_rel_types}`.
4. **Views** — match on summary/examples, `views show`, `views run`, three `status` branches.
5. **Store routing** — nested *inside* Views: `db bridge`, then `structured-store.md`'s own
   4-way classification (narrative / analytical / hybrid / records-plus-guidance).
6. **Retrieve table** — 17 rows, question shape → command.
7. **Routing notes** under the table — 10 bullets (entity-context vs pattern4, 6 vs 5, 4 vs
   hierarchy, doc_sources, bounds, pagination, text2cypher paging, domains, `--asOf`,
   history).
8. **Hub entities** — another 4-step mini-procedure, including "a specific fact may be
   better served by search".
9. **Ground** (3-way choice) and the **Fallback ladder** (10 rungs, rungs 8–10 for SQL and
   hierarchy).

Each reference file then re-enters routing ("read this when…", "go straight to
Discover", its own numbered chains).

### In code (3 places)

- `artmind/webui/profiles.py` — `BENCHMARK_SYSTEM_APPEND` restates steps
  ("domains-overview, entity-resolve").
- `artmind/traces/path.py` — `_BEFORE_VIEWS_OK`, the views-first judgement, hard-codes the
  protocol order.
- `test/test_query_skill_views_step.py` — pins exact phrases and heading order of the
  prose; any restructure breaks it. (`test_query_skill_structural_schema.py` is the better
  pattern: it checks the prose against data in `structural_schema.py`.)

## Command surface (for reference)

- `query`: `domains-overview`, `vector-text`, `entity-resolve`, `chunks`, `entity-context`,
  `entity-history`, `document-history`, `text2sql`, `resolve-key`, `views list|show|run`,
  `insights list|show`.
- `query graph`: `metadata`, `structural-metadata`, `entity-listing`, `filing-listing`,
  `pattern1`–`pattern10`, `timeline`, `conflicts`, `hierarchy`, `text2cypher`.
- `db`: `bridge`, `list`, `schema`, `sql`, `timeline`, `mappings`, `catalogue`, …

Overlaps an agent must choose between: `pattern2`/`pattern3`/`pattern4`/`entity-context`;
`pattern6`/`pattern5`; `pattern7`/`entity-listing --nameFilter`/`entity-resolve`;
`metadata`+`entity-listing` vs `structural-metadata`; `pattern10` vs `chunks` vs
`vector-text` for text.

## Gaps found

- `query graph filing-listing` exists in the CLI and is never mentioned by the skill.
- Insights sits outside the protocol, so its position relative to Views/Store routing is
  stated in prose three times.
- Store routing's entry point is inside the Views section; a pure-graph domain still pays
  a `db bridge` call per conversation.
- The skill's frontmatter `description` describes the system, not the questions that should
  trigger it.
