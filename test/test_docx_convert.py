"""artmind.docx_convert: each comment marker replaced by a callout beside its
text, and a .docx converted end to end. All text is synthetic."""
import zipfile

import pytest

pytest.importorskip("docling_core")

from artmind.docx_convert import UNPLACED_HEADING, callout, place_comments  # noqa: E402
from artmind.docx_prepare import Comment, Reply, marker  # noqa: E402


def _comment(n, text="Note.", anchor="", **kw):
    return Comment(number=n, author="Ann Lee", initials="AL", date="2026-02-16", text=text, anchor=anchor, **kw)


# ── comments ───────────────────────────────────────────────────────────────────


def test_a_callout_quotes_the_words_and_carries_replies_and_resolution():
    comment = _comment(0, text="Replace it?\nSecond line & more", anchor="Fees", resolved=True,
                       replies=[Reply(author="Bo King", initials="", date="2026-03-09", text="Yes.")])

    assert callout(comment) == [
        '> [!comment] Comment on "Fees" (resolved)',
        "> Ann Lee (AL), 2026-02-16: Replace it?",
        "> Second line &amp; more",
        "> Reply: Bo King, 2026-03-09: Yes.",
    ]


def test_a_paragraph_comment_follows_its_paragraph():
    markdown = f"Intro.\n\nThe budget is 10k{marker(0)}.\n\nNext."

    out = place_comments(markdown, [_comment(0, anchor="10k")])

    assert out == (
        'Intro.\n\nThe budget is 10k.\n\n> [!comment] Comment on "10k"\n'
        "> Ann Lee (AL), 2026-02-16: Note.\n\nNext."
    )


def test_a_list_item_comment_is_indented_into_its_item():
    markdown = f"1. Terms\n    - (a) obtain consent{marker(0)}; and\n    - (b) keep records."

    out = place_comments(markdown, [_comment(0)])

    assert out.split("\n") == [
        "1. Terms",
        "    - (a) obtain consent; and",
        "      > [!comment] Comment",
        "      > Ann Lee (AL), 2026-02-16: Note.",
        "    - (b) keep records.",
    ]


def test_a_table_comment_follows_the_table_and_names_its_row():
    markdown = f"| Check | Rule |\n| - | - |\n| **Licence** copy{marker(0)} | Valid |\n| Permit | Valid |\n\nAfter."

    out = place_comments(markdown, [_comment(0, anchor="copy")])

    assert out == (
        "| Check | Rule |\n| - | - |\n| **Licence** copy | Valid |\n| Permit | Valid |\n\n"
        '> [!comment] Comment on "copy" in the table row "Licence copy"\n'
        "> Ann Lee (AL), 2026-02-16: Note.\n\nAfter."
    )


def test_a_row_left_blank_by_its_marker_is_dropped_but_its_comment_kept():
    markdown = f"| Check | Rule |\n| - | - |\n| Licence | Valid |\n| {marker(0)} |  |"

    out = place_comments(markdown, [_comment(0, text="Not needed.")])

    assert out == (
        "| Check | Rule |\n| - | - |\n| Licence | Valid |\n\n"
        "> [!comment] Comment\n> Ann Lee (AL), 2026-02-16: Not needed."
    )


def test_a_comment_whose_marker_never_arrived_goes_to_the_end():
    out = place_comments("Body.", [_comment(0, text="In a footer.")])

    assert out == f"Body.\n\n{UNPLACED_HEADING}\n\n> [!comment] Comment\n> Ann Lee (AL), 2026-02-16: In a footer."


# ── end to end through docling ─────────────────────────────────────────────────


def test_a_docx_converts_with_changes_accepted_comments_inline_and_tidy_tables(tmp_path):
    pytest.importorskip("docling")
    docx = pytest.importorskip("docx")
    from artmind.docx_convert import convert_docx

    d = docx.Document()
    d.add_heading("Terms", 1)
    para = d.add_paragraph("The budget is 10k.")
    d.add_comment(para.runs, text="Should be 12k.", author="Ann Lee", initials="AL")
    table = d.add_table(rows=4, cols=3)
    for r, (check, rule) in enumerate([("Check", "Rule"), ("Licence", "Valid"), ("", ""), ("Permit", "Recent")]):
        table.cell(r, 1).text, table.cell(r, 2).text = check, rule
    d.add_paragraph("After.")
    source = tmp_path / "terms.docx"
    d.save(source)
    # A tracked insertion inside a table cell, which docling alone would drop.
    with zipfile.ZipFile(source) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["word/document.xml"] = parts["word/document.xml"].replace(
        b"<w:t>Recent</w:t></w:r>",
        b'<w:t>Recent</w:t></w:r><w:ins w:id="90" w:author="A" w:date="2026-01-01T00:00:00Z">'
        b'<w:r><w:t xml:space="preserve"> bill</w:t></w:r></w:ins>',
    )
    with zipfile.ZipFile(source, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)

    md_file = tmp_path / "out" / "terms.md"
    convert_docx(source, md_file, work_dir=tmp_path)
    markdown = md_file.read_text()

    assert "| Check | Rule |\n| - | - |\n| Licence | Valid |\n| Permit | Recent bill |" in markdown
    assert (
        'The budget is 10k.\n\n> [!comment] Comment on "The budget is 10k."\n'
        "> Ann Lee (AL)" in markdown
    )
    assert "QZCMT" not in markdown
    assert list(tmp_path.glob("tmp*")) == [], "the prepared copy is removed"
