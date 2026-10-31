"""artmind.docling_render: tables tidied in docling's model, every table a
top-level block, pptx speaker notes under their slide, and per-page bodies.
Documents are built with docling-core; all text is synthetic."""
import pytest

pytest.importorskip("docling_core")

from artmind.docling_render import (  # noqa: E402
    NOTES_MARKER,
    normalise_tables,
    notes_by_page,
    page_markdowns,
    render_body,
    render_notes,
    strip_table_padding,
    tidy_table,
    tidy_tables,
)


# ── tables ─────────────────────────────────────────────────────────────────────


def _table(rows, *, header=True, spans=(), row_spans=()):
    """A docling document with one table of `rows` (lists of cell texts);
    `spans` lists (row, col, col_span) merges, `row_spans` (row, col, row_span)."""
    from docling_core.types.doc.document import DoclingDocument, TableCell, TableData

    col_span = {(r, c): n for r, c, n in spans}
    row_span = {(r, c): n for r, c, n in row_spans}
    covered = {(r, c + k) for r, c, n in spans for k in range(1, n)}
    covered |= {(r + k, c) for r, c, n in row_spans for k in range(1, n)}
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r, end_row_offset_idx=r + row_span.get((r, c), 1),
            start_col_offset_idx=c, end_col_offset_idx=c + col_span.get((r, c), 1),
            row_span=row_span.get((r, c), 1), col_span=col_span.get((r, c), 1),
            column_header=header and r == 0,
        )
        for r, row in enumerate(rows) for c, text in enumerate(row) if (r, c) not in covered
    ]
    doc = DoclingDocument(name="t")
    table = doc.add_table(data=TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells))
    return doc, table


def _texts(table):
    return [[c.text for c in row] for row in table.data.grid]


def test_empty_rows_and_columns_are_dropped():
    doc, table = _table([["", "Check", "Rule"], ["", "Licence", "Valid"], ["", "", ""], ["", "Permit", ""]])

    tidy_table(table.data, doc)

    assert _texts(table) == [["Check", "Rule"], ["Licence", "Valid"], ["Permit", ""]]


def test_a_body_cell_merged_across_columns_is_written_once():
    doc, table = _table([["Party", "Role", "Note"], ["Acme and Beta", "", ""]], spans=[(1, 0, 3)])

    tidy_table(table.data, doc)

    assert _texts(table) == [["Party", "Role", "Note"], ["Acme and Beta", "", ""]]
    assert "Acme and Beta |  |  |" in doc.export_to_markdown(compact_tables=True)


def test_a_header_cell_merged_across_columns_still_labels_each_column():
    doc, table = _table([["Costs", "", "Item"], ["10", "20", "x"]], spans=[(0, 0, 2)])

    tidy_table(table.data, doc)

    assert _texts(table)[0] == ["Costs", "Costs", "Item"]


def test_a_cell_merged_down_rows_keeps_repeating_for_each_row():
    doc, table = _table([["Group", "Item"], ["Fees", "Rent"], ["", "Rates"]], row_spans=[(1, 0, 2)])

    tidy_table(table.data, doc)

    assert _texts(table) == [["Group", "Item"], ["Fees", "Rent"], ["Fees", "Rates"]]


def test_a_row_made_only_of_cells_merged_from_above_is_dropped():
    doc, table = _table(
        [["Group", "Item"], ["Fees", "Rent"], ["", ""], ["Tax", "VAT"]],
        row_spans=[(1, 0, 2), (1, 1, 2)],
    )

    tidy_table(table.data, doc)

    assert _texts(table) == [["Group", "Item"], ["Fees", "Rent"], ["Tax", "VAT"]]


def test_tidy_tables_tidies_every_table():
    doc, first = _table([["A", ""], ["x", ""]])
    from docling_core.types.doc.document import TableCell, TableData

    second = doc.add_table(data=TableData(num_rows=1, num_cols=2, table_cells=[
        TableCell(text="y", start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=0, end_col_offset_idx=1),
    ]))

    tidy_tables(doc)

    assert (first.data.num_cols, second.data.num_cols) == (1, 1)


def test_tables_become_top_level_blocks_with_blank_lines_around():
    markdown = "1. Terms:\n    | A | B |\n| - | - |\n| x | y |\n2. Next."

    assert normalise_tables(markdown) == "1. Terms:\n\n| A | B |\n| - | - |\n| x | y |\n\n2. Next."


def test_padding_is_stripped_from_doclings_own_markdown_tables():
    markdown = "Intro | not a table\n\n| Name   | Value |\n|--------|:-----:|\n| a &#124; b | one   |"

    assert strip_table_padding(markdown) == (
        "Intro | not a table\n\n| Name | Value |\n| - | - |\n| a &#124; b | one |"
    )


def test_the_body_has_unpadded_tables():
    doc, _ = _table([["Check", "Rule"], ["Licence", "Valid until the end of the year"]])

    body = render_body(doc)

    assert "| Check | Rule |\n| - | - |\n| Licence | Valid until the end of the year |" in body


# ── pages and notes ────────────────────────────────────────────────────────────


def _deck(slides, notes=None, orphan=None):
    """A docling document with one page per entry of `slides` (lists of
    paragraphs), speaker notes per page in `notes`, and optionally a body
    paragraph with no page at all (`orphan`)."""
    from docling_core.types.doc.base import BoundingBox, Size
    from docling_core.types.doc.document import ContentLayer, DoclingDocument, ProvenanceItem
    from docling_core.types.doc.labels import DocItemLabel

    doc = DoclingDocument(name="deck")
    box = BoundingBox(l=0, t=0, r=1, b=1)
    for page_no, paragraphs in enumerate(slides, start=1):
        doc.add_page(page_no=page_no, size=Size(width=10, height=10))
        for text in paragraphs:
            doc.add_text(label=DocItemLabel.TEXT, text=text,
                         prov=ProvenanceItem(page_no=page_no, bbox=box, charspan=(0, len(text))))
    for page_no, text in (notes or {}).items():
        doc.add_text(label=DocItemLabel.TEXT, text=text, content_layer=ContentLayer.NOTES,
                     prov=ProvenanceItem(page_no=page_no, bbox=box, charspan=(0, len(text))))
    if orphan:
        doc.add_text(label=DocItemLabel.TEXT, text=orphan)
    return doc


def test_notes_are_collected_per_slide_and_quoted_under_the_marker():
    doc = _deck([["Intro."], ["Plan."]], notes={2: "Say this.\nThen that."})

    assert notes_by_page(doc) == {2: ["Say this.", "Then that."]}
    assert render_notes(["Say this.", "Then that."]) == f"{NOTES_MARKER}\n\n> Say this.\n>\n> Then that."


def test_notes_follow_their_slide_and_slides_without_notes_are_unchanged():
    doc = _deck([["Intro."], ["Plan."], ["End."]], notes={2: "Say this."})

    body = render_body(doc, notes=True)

    assert body == f"Intro.\n\nPlan.\n\n{NOTES_MARKER}\n\n> Say this.\n\nEnd."


def test_without_notes_or_replacements_the_body_is_the_whole_export():
    doc = _deck([["Intro."], ["Plan."]], notes={1: "Hidden."})

    assert render_body(doc) == "Intro.\n\nPlan."


def test_a_replacement_swaps_its_page_only():
    doc = _deck([["Intro."], ["Plan, lost."], ["End."]])

    assert render_body(doc, replacements={2: "Plan, repaired."}) == "Intro.\n\nPlan, repaired.\n\nEnd."


def test_pages_that_do_not_join_to_the_whole_fall_back_to_the_whole():
    doc = _deck([["Intro."], ["Plan."]], notes={1: "Say this."}, orphan="No page.")

    assert render_body(doc, notes=True, replacements={2: "X."}) == "Intro.\n\nPlan.\n\nNo page."
    assert page_markdowns(doc) is None


def test_page_markdowns_render_each_page_as_the_body_does():
    doc = _deck([["Intro."], ["Plan."]], notes={2: "Say this."})

    assert page_markdowns(doc, notes=True) == {1: "Intro.", 2: f"Plan.\n\n{NOTES_MARKER}\n\n> Say this."}
