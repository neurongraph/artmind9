# Conversion rendering and page check — design

Status: implemented on `feat/conversion-page-repair`. Supersedes the 2026-10-28
"conversion page repair" design (branch `worktree-docling-page-repair`, never
merged), which was written against docling 2.115 and before .docx conversion
moved in-process. Its measurements and decisions carry over; its mechanism
does not.

## Problem

`_convert_binary_via_docling` took docling's markdown as the document body.
On real documents that body has defects that raise no error:

1. **Table padding.** docling pads every cell to its column's width: about
   half the characters of a table-heavy document, so about twice the chunks
   and extraction calls for the same words.
2. **Merged cells copied into every column.** A full-width cell becomes the
   same text once per column (18 % of a 40-page contract's words).
3. **Lost text.** A table cell's last line (a formula) dropped, a page of
   code turned into a picture. Found only by comparing with the source.
4. **pptx speaker notes dropped.** docling keeps them in its NOTES layer,
   which the markdown export leaves out.

## What changed from the 10-28 design

| 10-28 design | This design | Why |
|---|---|---|
| `convert_cleanup`: regexes over docling's markdown to strip padding and collapse repeated cells/rows | `docling_render.tidy_tables`: the same fixes in docling's model (cell spans), then docling's own `compact_tables` export | The model knows which cells are merged; text heuristics guess. On the 40-page contract both give 9,226 words (from 11,134), differing only in separator style. The same routine already served .docx, so one implementation covers every format. |
| Body = docling's markdown, recomposed from per-page JSON exports only for notes or repairs | Body = always rendered from the JSON | One rendering path; tables tidied for every pdf and pptx. docling's markdown stays the fallback when the JSON cannot be used. |
| Conversion record passed through a module-level dict keyed by path | Returned: `_convert_binary_via_docling` → `(body, {"conversion": record})` | No hidden state; failure still returns `(None, err)`. |
| Page repair duplicated `_describe_image`'s provider dispatch | `extraction.describe_image`, shared | One place picks the provider. |
| Number check: substring (`"1" in "10"` passes) | Whole numbers, thousands separators ignored | A transcription that turned `1` into `10` was accepted. |
| A page whose text docling filed under an earlier page was flagged | `page_check.spilled_text` counts it present | Seen on the contract: a page-39 paragraph merged into an item starting on page 34. Flagging it would make `repair` duplicate that text. |
| Goldens recorded on docling 2.115 | Re-recorded on docling 2.145 from the pre-change conversion | Bodies changed with the docling upgrade alone. |

Unchanged from 10-28 (decided then): speaker notes are included in pptx
bodies under a per-slide marker; page repair is opt-in (`detect` by default,
no page image leaves the machine); pptx is never repaired; docx has no pages
and is not checked.

## Design

### Rendering (`docling_render`)

- `tidy_tables(doc)`, in place on docling's model:
  - a body cell merged across columns keeps only its first column; a header
    cell keeps repeating so every column under a group header has its label;
  - a cell merged down rows keeps repeating (each row carries its context),
    but a row made only of cells merged from the row above is a copy and goes;
  - rows and columns with no content go (a rich cell holding a picture or
    table counts as content).
- `render_body(doc, notes=, replacements=)`: docling's whole export with
  `compact_tables` and referenced images; per-page exports joined when a pptx
  slide has notes or a page is replaced — trusted only when those exports
  spell the same words as the whole export, otherwise the whole is used.
- `normalise_tables`: every table a top-level block with blank lines around
  it (docling 2.145 indents only a nested table's first line).
- `RENDER_VERSION` is recorded in `conversion.json`.

### Conversion flow (pdf, pptx, other non-docx)

1. `docling --to md --to json --image-export-mode referenced` as a
   subprocess (keeps docling's layout models out of the worker).
2. Load the JSON (docling's bbox-clamping warnings silenced), `tidy_tables`.
3. Page check per `ARTMIND_PAGE_REPAIR`; in `repair`, transcribe flagged pdf
   pages.
4. `render_body` (pptx with notes; repaired pages replaced); write the body;
   delete the JSON. Any failure here keeps docling's markdown, unpadded
   (`strip_table_padding`), and records `page_check: skipped`.
5. Image-link relativising and image descriptions, as before.

.docx keeps its in-process path (`docx_convert`) and now uses the same
`tidy_tables`/`normalise_tables`; its record says `page_check: skipped`
(no pages) and how many comments were placed.

### Page check (`page_check`)

As measured in the 10-28 design: source text from `pypdfium2` / `python-pptx`
(shapes, tables, groups, notes); normalised (entities, quotes, nbsp, soft
hyphens, `Page n of m`); 3-word shingles inside one layout fragment; running
headers/footers dropped only from a page's first/last three lines when they
repeat on ≥ 40 % of pages; a phrase counts if the page, a neighbour, or text
docling filed under an earlier page has it. Flag below 0.97 coverage, or a
long table cell ending in `:`. No text layer → `skipped`, never flagged.

### Repair (`page_repair`)

Only in `repair`, only pdf. Render at 150 dpi, transcribe with
`ARTMIND_IMAGE_MODEL` through `extraction.describe_image`, accept only at
≥ 0.97 coverage with every source number present; cache by
`sha256(image)-model-PROMPT_VERSION` under `<data dir>/page_repair_cache/`.

### Record

`conversion.json` keeps `source_sha256`, `source_type`, `source_path` and
gains `conversion`: `render_version`, `page_check` (`off|detect|repair|skipped`),
`why`, `pages`, `flagged` (`page`, `coverage`, `reason` — no document text),
`repaired`, `unrepaired`, `model`, `prompt_version`, and for .docx `comments`.
A `--reextract` from the stored markdown keeps the previous record. Flagged
counts reach `ingestion_job_files.page_check` and `job-status`.

## Measurements (docling 2.145)

Fixtures in `test/data/docs` (5 pdf, 7 pptx), against the pre-change body:
no distinct word lost; pdfs without tables byte-identical; pptx with tables
11.8k → 9.8k and 16.8k → 12.6k characters; decks gain their notes; every
table still parses as a table; only the pdf page docling turns into a picture
is flagged. A 40-page contract: 132k → 62k characters, 30 → 16 chunks, no
distinct word lost; flagged its two cells ending in `:` (the formula losses
the 10-28 design found by hand, still present in docling 2.145) and one page
whose signature stamp docling exported as a picture.

## Not done

- `repair` has not been run against a real provider on this branch (it sends
  page images off the machine).
- Existing documents change only when converted again.
