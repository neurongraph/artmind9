"""The markdown body of a docling document, the same way for every format.

docling's own markdown export needs four things done before artmind can
chunk and extract from it:

Tables (`tidy_tables`, in docling's model, before export)
    - a body cell merged across columns is written once, in its first column
      (docling repeats it in every column: on one contract 18 % of the words
      were such copies); a header cell still repeats, so every column under a
      group header keeps its label;
    - a cell merged down rows keeps repeating, so each row carries its own
      context, but a row made only of cells merged from the row above is a
      copy and goes;
    - rows and columns empty throughout are dropped.
    The export is then `compact_tables`: docling pads every cell to its
    column's width, about half the characters of a table-heavy document.

Layout (`normalise_tables`, on the markdown)
    Every table is a top-level block with a blank line before and after.
    docling writes a table inside a list item with only its first line
    indented and no blank line, which no markdown reader takes as a table.

Speaker notes (pptx)
    docling keeps them in the NOTES content layer, which its export leaves
    out. With `notes=True` each slide's notes follow it under `NOTES_MARKER`,
    one quoted line per paragraph, so extraction can tell a note from slide
    text.

Pages
    `page_markdowns` gives each page's markdown, rendered as the body is, for
    the page check; `render_body` takes `replacements` (page number -> text)
    for repaired pages. Either joins the per-page exports, which is trusted
    only when they spell the same words as the whole-document export;
    otherwise the whole export is used and the reason logged.

Needs docling-core (the `[ingest]` extra); imported on call.
"""

import re

from loguru import logger

#: Bump when the body rendered from the same docling document changes; recorded
#: in conversion.json so a body can be traced to the rendering that made it.
RENDER_VERSION = 1

NOTES_MARKER = "**Speaker notes:**"
PAGE_SEPARATOR = "\n\n"


# ── tables ─────────────────────────────────────────────────────────────────────


def tidy_table(data, doc) -> None:
    """Tidy one docling `TableData` of `doc` in place (see the module
    docstring). A rich cell (one holding docling items) is empty when it has
    no text and holds no picture or table."""
    from docling_core.types.doc.document import PictureItem, RichTableCell, TableItem

    for cell in data.table_cells:
        if not cell.column_header and cell.end_col_offset_idx - cell.start_col_offset_idx > 1:
            cell.end_col_offset_idx = cell.start_col_offset_idx + 1
            cell.col_span = 1

    def empty(cell) -> bool:
        if cell.text.strip():
            return False
        if not isinstance(cell, RichTableCell):
            return True
        root = cell.ref.resolve(doc)
        items = [root, *(item for item, _ in doc.iterate_items(root=root, with_groups=True))]
        return not any(isinstance(item, (PictureItem, TableItem)) for item in items)

    def copied_from_above(i: int, row) -> bool:
        return i > 0 and all(c.start_row_offset_idx < i and not c.column_header for c in row)

    grid = data.grid
    keep_rows = [
        i for i, row in enumerate(grid)
        if not all(empty(c) for c in row) and not copied_from_above(i, row)
    ]
    keep_cols = [j for j in range(data.num_cols) if not all(empty(row[j]) for row in grid)]
    if not keep_rows or not keep_cols:
        return
    row_at = {old: new for new, old in enumerate(keep_rows)}
    col_at = {old: new for new, old in enumerate(keep_cols)}
    cells = []
    for cell in data.table_cells:
        rows = [row_at[i] for i in range(cell.start_row_offset_idx, cell.end_row_offset_idx) if i in row_at]
        cols = [col_at[j] for j in range(cell.start_col_offset_idx, cell.end_col_offset_idx) if j in col_at]
        if not rows or not cols:
            continue
        cell.start_row_offset_idx, cell.end_row_offset_idx = rows[0], rows[-1] + 1
        cell.start_col_offset_idx, cell.end_col_offset_idx = cols[0], cols[-1] + 1
        cell.row_span, cell.col_span = len(rows), len(cols)
        cells.append(cell)
    data.table_cells = cells
    data.num_rows, data.num_cols = len(keep_rows), len(keep_cols)


def tidy_tables(doc) -> None:
    """`tidy_table` on every table of `doc`, in place."""
    for table in doc.tables:
        tidy_table(table.data, doc)


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|")


def normalise_tables(markdown: str) -> str:
    """Every run of table lines unindented, with a blank line before and after."""
    out: list[str] = []
    for line in markdown.split("\n"):
        if not is_table_line(line):
            if out and is_table_line(out[-1]) and line.strip():
                out.append("")
            out.append(line)
            continue
        if out and out[-1].strip() and not is_table_line(out[-1]):
            out.append("")
        out.append(line.strip())
    return "\n".join(out)


def strip_table_padding(markdown: str) -> str:
    """Table lines rewritten without cell padding, for markdown docling wrote
    itself (the fallback when its JSON cannot be used). docling escapes a pipe
    inside a cell as `&#124;`, so every `|` on a table line is a border."""

    def row(line: str) -> str:
        cells = [c.strip() for c in line.strip()[1:-1].split("|")]
        if all(c and set(c) <= set(":-") for c in cells):
            cells = ["-" for _ in cells]
        return "| " + " | ".join(cells) + " |"

    return "\n".join(row(line) if is_table_line(line) else line for line in markdown.split("\n"))


# ── pages and notes ────────────────────────────────────────────────────────────


def page_numbers(doc) -> list[int]:
    return sorted(doc.pages)


def _export(doc, page_no: int | None = None) -> str:
    from docling_core.types.doc.base import ImageRefMode

    return doc.export_to_markdown(page_no=page_no, image_mode=ImageRefMode.REFERENCED, compact_tables=True)


def notes_by_page(doc) -> dict[int, list[str]]:
    """Speaker-note paragraphs per page (slide) number, from the notes layer."""
    from docling_core.types.doc.document import ContentLayer

    notes: dict[int, list[str]] = {}
    for item, _ in doc.iterate_items(included_content_layers={ContentLayer.NOTES}):
        if item.content_layer != ContentLayer.NOTES:
            continue
        paragraphs = [p.strip() for p in (getattr(item, "text", "") or "").splitlines() if p.strip()]
        for page_no in sorted({prov.page_no for prov in item.prov}):
            notes.setdefault(page_no, []).extend(paragraphs)
    return notes


def render_notes(paragraphs: list[str]) -> str:
    """The marker, then one quoted line per paragraph."""
    return f"{NOTES_MARKER}\n\n" + "\n>\n".join(f"> {p}" for p in paragraphs)


def _words(markdown: str) -> list[str]:
    return markdown.split()


def _with_notes(text: str, paragraphs: list[str] | None) -> str:
    text = text.strip("\n")
    if not paragraphs:
        return text
    return f"{text}{PAGE_SEPARATOR}{render_notes(paragraphs)}" if text else render_notes(paragraphs)


def _per_page(doc) -> dict[int, str] | None:
    """Each page's export, or None when they do not join to the whole export
    (items with no page, a docling version that orders them differently)."""
    pages = page_numbers(doc)
    exported = {n: _export(doc, n) for n in pages}
    if _words(PAGE_SEPARATOR.join(exported[n] for n in pages)) != _words(_export(doc)):
        logger.warning(
            "docling's per-page export does not reproduce its whole-document markdown; "
            "using the whole markdown (no speaker notes, no page replacements)"
        )
        return None
    return exported


def page_markdowns(doc, *, notes: bool = False) -> dict[int, str] | None:
    """Each page's markdown as the body renders it (notes included when
    `notes`), or None when the pages cannot be told apart reliably."""
    exported = _per_page(doc)
    if exported is None:
        return None
    page_notes = notes_by_page(doc) if notes else {}
    return {n: normalise_tables(_with_notes(text, page_notes.get(n))) for n, text in exported.items()}


def render_body(doc, *, notes: bool = False, replacements: dict[int, str] | None = None) -> str:
    """The document body: the whole export, or the per-page exports joined
    when slides have notes to add or pages have replacements."""
    replacements = replacements or {}
    page_notes = notes_by_page(doc) if notes else {}
    exported = _per_page(doc) if (replacements or page_notes) else None
    if exported is None:
        return normalise_tables(_export(doc))
    parts = [_with_notes(replacements.get(n) or exported[n], page_notes.get(n)) for n in sorted(exported)]
    return normalise_tables(PAGE_SEPARATOR.join(p for p in parts if p))
