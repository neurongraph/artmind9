"""artmind.docx_prepare: a .docx copy with tracked changes accepted and a marker
where each comment's range ends. The packages are built by hand with only the
parts `prepare_docx` reads; every text is synthetic."""
import zipfile

import pytest

pytest.importorskip("lxml")

from artmind.docx_prepare import MARKER_RE, marker, prepare_docx  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
W14 = 'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'


def _r(text):
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _p(*parts):
    return "<w:p>" + "".join(parts) + "</w:p>"


def _package(tmp_path, body, comments=(), extended=()):
    """A .docx holding `body` (w:body content) and `comments` as
    (id, author, initials, date, text, para_id); `extended` as
    (para_id, parent_para_id, done)."""
    path = tmp_path / "in.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
        if comments:
            items = "".join(
                f'<w:comment w:id="{cid}" w:author="{a}" w:initials="{i}" w:date="{d}">'
                f'<w:p w14:paraId="{pid}">{_r(t)}</w:p></w:comment>'
                for cid, a, i, d, t, pid in comments
            )
            z.writestr("word/comments.xml", f'<?xml version="1.0"?><w:comments {W} {W14}>{items}</w:comments>')
        if extended:
            items = "".join(
                f'<w15:commentEx w15:paraId="{pid}"' + (f' w15:paraIdParent="{parent}"' if parent else "")
                + f' w15:done="{1 if done else 0}"/>'
                for pid, parent, done in extended
            )
            z.writestr("word/commentsExtended.xml", f'<?xml version="1.0"?><w15:commentsEx {W15}>{items}</w15:commentsEx>')
        z.writestr("word/styles.xml", "<styles/>")
    return path


def _prepare(tmp_path, body, **kw):
    target = tmp_path / "out.docx"
    comments = prepare_docx(_package(tmp_path, body, **kw), target)
    with zipfile.ZipFile(target) as z:
        return comments, z.read("word/document.xml").decode(), {n: z.read(n) for n in z.namelist()}


def _text(document_xml):
    import re

    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", document_xml))


def test_tracked_changes_are_accepted_in_paragraphs_and_table_cells(tmp_path):
    body = (
        _p(_r("The "), '<w:del w:id="1" w:author="A"><w:r><w:delText>Supply</w:delText></w:r></w:del>',
           '<w:ins w:id="2" w:author="A">' + _r("Services") + "</w:ins>", _r(" apply."))
        + "<w:tbl><w:tr><w:tc>" + _p(_r("Cell "), '<w:ins w:id="3" w:author="A">' + _r("inserted") + "</w:ins>")
        + '</w:tc></w:tr><w:tr><w:trPr><w:del w:id="4" w:author="A"/></w:trPr><w:tc>' + _p(_r("Deleted row"))
        + "</w:tc></w:tr></w:tbl>"
    )

    comments, xml, _ = _prepare(tmp_path, body)

    assert comments == []
    assert _text(xml) == "The Services apply.Cell inserted"
    assert "<w:ins" not in xml and "<w:del" not in xml and "delText" not in xml


def test_formatting_change_records_are_dropped(tmp_path):
    body = _p('<w:r><w:rPr><w:b/><w:rPrChange w:id="1" w:author="A"><w:rPr/></w:rPrChange></w:rPr><w:t>Bold</w:t></w:r>')

    _, xml, _ = _prepare(tmp_path, body)

    assert "rPrChange" not in xml and "<w:b/>" in xml


def test_a_marker_follows_each_comment_range_with_the_highlighted_words(tmp_path):
    body = (
        _p(_r("The budget is "), '<w:commentRangeStart w:id="7"/>', _r("10k"), '<w:commentRangeEnd w:id="7"/>',
           '<w:r><w:commentReference w:id="7"/></w:r>', _r(" per year."))
        + _p(_r("Next."))
    )
    comments, xml, _ = _prepare(
        tmp_path, body, comments=[("7", "Ann Lee", "AL", "2026-02-16T10:00:00Z", "Should be 12k.", "P1")],
    )

    assert [(c.number, c.author, c.initials, c.date, c.text, c.anchor) for c in comments] == [
        (0, "Ann Lee", "AL", "2026-02-16", "Should be 12k.", "10k"),
    ]
    assert _text(xml) == f"The budget is 10k{marker(0)} per year.Next."


def test_a_reply_joins_its_comment_and_gets_no_marker(tmp_path):
    body = _p(
        '<w:commentRangeStart w:id="1"/><w:commentRangeStart w:id="2"/>', _r("Heading"),
        '<w:commentRangeEnd w:id="1"/><w:commentRangeEnd w:id="2"/>',
    )
    comments, xml, _ = _prepare(
        tmp_path, body,
        comments=[("1", "Ann", "A", "2026-02-16T00:00:00Z", "Replace the letter?", "P1"),
                  ("2", "Bo", "B", "2026-03-09T00:00:00Z", "Yes.", "P2")],
        extended=[("P1", None, True), ("P2", "P1", False)],
    )

    assert len(comments) == 1
    assert comments[0].resolved is True
    assert [(r.author, r.text) for r in comments[0].replies] == [("Bo", "Yes.")]
    assert MARKER_RE.findall(xml) == ["0"]


def test_a_comment_on_deleted_words_keeps_its_place(tmp_path):
    body = _p(
        _r("Kept "),
        '<w:del w:id="9" w:author="A"><w:commentRangeStart w:id="3"/><w:r><w:delText>gone</w:delText></w:r>'
        '<w:commentRangeEnd w:id="3"/><w:r><w:commentReference w:id="3"/></w:r></w:del>',
        _r(" text."),
    )
    comments, xml, _ = _prepare(tmp_path, body, comments=[("3", "Ann", "", "", "Why delete?", "P1")])

    assert comments[0].anchor == ""
    assert _text(xml) == f"Kept {marker(0)} text."


def test_a_range_ending_between_paragraphs_marks_the_paragraph_before(tmp_path):
    body = (
        _p('<w:commentRangeStart w:id="5"/>', _r("First.")) + _p(_r("Second."))
        + '<w:commentRangeEnd w:id="5"/>' + _p(_r("Third."))
    )
    comments, xml, _ = _prepare(tmp_path, body, comments=[("5", "Ann", "", "", "Spans two.", "P1")])

    assert comments[0].anchor == "First. Second."
    assert _text(xml) == f"First.Second.{marker(0)}Third."


def test_other_parts_of_the_package_are_copied_unchanged(tmp_path):
    _, _, parts = _prepare(tmp_path, _p(_r("x")))

    assert parts["word/styles.xml"] == b"<styles/>"
