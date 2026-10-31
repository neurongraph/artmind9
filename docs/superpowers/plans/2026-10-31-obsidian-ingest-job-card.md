# Compact ingest job card implementation plan

Approved design: `docs/superpowers/specs/2026-10-31-obsidian-ingest-job-card-design.md`.

1. Add a pure file-grouping helper and job-scoped transient group expansion state.
2. Reuse the file-row renderer in ordered groups, with five-row queued/completed
   previews, explicit expansion buttons and a collapsed skipped disclosure.
3. Capture and restore panel/list scroll and group-control focus at the panel
   render boundary; reset job-specific state for a different job.
4. Update existing ordering assertions and add synthetic large-job tests covering
   grouping, previews, totals, expansion, refresh, focus and job transitions.
5. Run `just obsidian-plugin-test` and `just obsidian-plugin-build`; inspect the
   diff. Leave installation/manual Obsidian validation to an explicitly requested
   vault target.
