"""artmind.docx_comments: a .docx's Word comments written inline in its markdown,
rendered from the docling JSON. The documents are built with docling-core the
way docling's Word backend builds them: each comment is a `comment_section`
group in the NOTES layer, linked from the paragraphs it annotates."""
import pytest

pytest.importorskip("docling_core")

from docling_core.types.doc.document import ContentLayer, DoclingDocument, FineRef  # noqa: E402
from docling_core.types.doc.labels import DocItemLabel, GroupLabel  # noqa: E402

from artmind.docx_comments import UNANCHORED_HEADING, markdown_with_comments  # noqa: E402


def _comment(doc, text, *targets):
    group = doc.add_group(label=GroupLabel.COMMENT_SECTION, name="comment", content_layer=ContentLayer.NOTES)
    doc.add_comment(text=text, parent=group)
    for target in targets:
        target.comments.append(FineRef(cref=group.self_ref))
    return group


def _render(doc, tmp_path):
    path = tmp_path / "doc.json"
    doc.save_as_json(path)
    return markdown_with_comments(path)


def test_a_document_without_comments_keeps_doclings_markdown(tmp_path):
    doc = DoclingDocument(name="d")
    doc.add_heading("Plan")
    doc.add_text(label=DocItemLabel.TEXT, text="Phase one.")

    assert _render(doc, tmp_path) is None


def test_a_comment_follows_its_paragraph_not_the_end_of_the_document(tmp_path):
    doc = DoclingDocument(name="d")
    doc.add_heading("Plan")
    first = doc.add_text(label=DocItemLabel.TEXT, text="Phase one starts in March.")
    doc.add_text(label=DocItemLabel.TEXT, text="Phase two follows.")
    _comment(doc, "[author: Ann]: Slipped to April.", first)

    md = _render(doc, tmp_path)

    assert md == (
        "## Plan\n\n"
        "Phase one starts in March.\n\n"
        "> [!comment]\n> [author: Ann]: Slipped to April.\n\n"
        "Phase two follows."
    )


def test_a_multi_line_comment_stays_inside_its_callout_and_is_escaped(tmp_path):
    doc = DoclingDocument(name="d")
    para = doc.add_text(label=DocItemLabel.TEXT, text="Budget.")
    _comment(doc, "Too low <see sheet>\nand late & over", para)

    md = _render(doc, tmp_path)

    assert md.endswith("> [!comment]\n> Too low &lt;see sheet&gt;\n> and late &amp; over")


def test_a_comment_on_a_list_item_follows_the_whole_list(tmp_path):
    doc = DoclingDocument(name="d")
    items = doc.add_list_group()
    alpha = doc.add_list_item("Alpha", parent=items)
    doc.add_list_item("Beta", parent=items)
    doc.add_text(label=DocItemLabel.TEXT, text="After.")
    _comment(doc, "Drop alpha?", alpha)

    md = _render(doc, tmp_path)

    assert md == "- Alpha\n- Beta\n\n> [!comment]\n> Drop alpha?\n\nAfter."


def test_a_comment_linked_to_several_items_is_written_once(tmp_path):
    doc = DoclingDocument(name="d")
    a = doc.add_text(label=DocItemLabel.TEXT, text="One.")
    b = doc.add_text(label=DocItemLabel.TEXT, text="Two.")
    _comment(doc, "Spans both.", a, b)

    md = _render(doc, tmp_path)

    assert md.count("Spans both.") == 1
    assert md.index("Spans both.") < md.index("Two.")


def test_an_unlinked_comment_goes_under_a_heading_at_the_end(tmp_path):
    """docling links comments to paragraphs only; one on a table cell or list
    item is left unlinked, and must not be lost."""
    doc = DoclingDocument(name="d")
    para = doc.add_text(label=DocItemLabel.TEXT, text="Body.")
    _comment(doc, "Anchored.", para)
    _comment(doc, "On a table cell.")

    md = _render(doc, tmp_path)

    assert md == (
        "Body.\n\n> [!comment]\n> Anchored.\n\n"
        f"{UNANCHORED_HEADING}\n\n> [!comment]\n> On a table cell."
    )


def test_the_markdown_matches_doclings_own_export_apart_from_the_comments(tmp_path):
    doc = DoclingDocument(name="d")
    doc.add_heading("Plan")
    para = doc.add_text(label=DocItemLabel.TEXT, text="Costs_total & more.")
    items = doc.add_list_group()
    doc.add_list_item("Alpha", parent=items)
    _comment(doc, "Noted.", para)

    md = _render(doc, tmp_path)

    without = md.replace("\n\n> [!comment]\n> Noted.", "")
    assert without == doc.export_to_markdown()
