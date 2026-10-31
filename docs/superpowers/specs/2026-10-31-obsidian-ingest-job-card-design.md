# Compact Obsidian ingest job card

## Goal

Keep current ingestion activity visible during large jobs without scrolling past
completed files, and stop polling refreshes from losing the user's place.

## Current behaviour

`obsidian/artmind-obsidian/src/views/jobCard.ts` renders every file in the CLI's
queue order inside one scrollable `.artmind-files` container. Completed files
accumulate above running and queued files. `renderPanel` replaces the entire panel
on every snapshot; section disclosure state is remembered through `PanelUi`, but
the file list's scroll position is not.

## Agreed presentation

Retain the existing job title, finished/total count, segmented status bar, legend,
entity/relationship totals, per-file meters and admin-console link. Display
non-empty file groups in this order:

1. **Running**: all processing files, with their existing step/chunk progress.
2. **Up next · N**: the first five queued files in queue order.
3. **Needs attention · N**: all failed files, retaining failure reasons.
4. **Recently completed · N**: the last five completed files in reverse queue
   order, so the latest position in the queue appears first.
5. **Skipped · N**: collapsed by default; expand to inspect skipped files in
   queue order.

Up next and Recently completed offer Show all N / Show fewer controls when they
contain more than five files. Their headings always count the full group, not
just the preview. Running and Needs attention are never truncated. Empty groups
are omitted. Unknown statuses retain the existing queued fallback.

The same grouping applies to the last finished job. The card does not add retry
controls: existing checklist actions remain responsible for retrying.

### Completion ordering limitation

The current plugin payload has no per-file completion timestamps. Recently
completed therefore means completed files nearest the end of the original queue,
not a promise of wall-clock completion order. This change is plugin-only; adding
timestamps or changing the CLI contract is out of scope.

## Refresh and interaction state

- Polling frequency and controller behaviour remain unchanged.
- Remember group expansion per displayed job ID across snapshots, including the
  transition from active job to last job. A different job resets group previews
  and scroll positions.
- Preserve the panel's scroll position and each job-group list's scroll position
  across a rerender. Clamp naturally if a list shrinks. Do not auto-scroll as
  files move between groups or new results arrive.
- Keep the existing bounded list styling for expanded long lists, rather than
  allowing hundreds of file rows to stretch the sidebar.
- Explicit user navigation (for example focusing a checklist row) can still
  scroll to its target after rendering.
- Use native buttons/disclosure controls with descriptive labels. Preserve
  keyboard focus on job-group controls across polling rerenders when the
  corresponding control still exists.

## Implementation boundaries

- A small pure grouping helper derives groups from the current files without
  mutating the CLI payload. Preserve queue order within source arrays and use the
  existing status classification consistently for both counts and groups.
- `jobCard.ts` owns grouped presentation and reuses the current per-file renderer
  for glyphs, names, errors and meters. Scaling and totals use all files, not
  just visible previews.
- `PanelUi` holds transient job identity and group expansion state. No plugin
  settings or persisted plugin data are added.
- The panel render boundary captures and restores scroll/focus state around DOM
  replacement. Avoid a broad rewrite to incremental DOM rendering.
- CSS adds compact group headings and controls using Obsidian theme variables.
  No dependencies, backend changes or unrelated panel redesign.

## Error handling and compatibility

Continue rendering older CLI payloads without optional progress/count fields.
Keep stalled-job treatment, failed-file reasons, retry routing and admin links
unchanged. All filenames and errors remain text, not HTML.

## Verification

Use synthetic plugin test data to cover:

- exact group order, including failures below Up next;
- five-file queued/completed previews and full group counts;
- reverse queue order for completed previews;
- all running/failed files shown, skipped collapsed, empty groups omitted;
- Show all / Show fewer and skipped disclosure surviving snapshot refreshes;
- new-job reset and active-to-finished state retention;
- scroll restoration for the panel and expanded lists, and control focus;
- unchanged full-job totals/meter scaling and existing stalled/retry behaviour.

Run the plugin test recipe and build/type checks from the justfile. Actual scroll
layout and refresh stability also need a manual Obsidian check with a long job;
DOM-only tests cannot validate browser layout. Do not install into a user's vault
without an explicit target and request.
