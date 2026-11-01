"""A .docx made ready for docling: tracked changes accepted, comments marked.

docling's Word backend gets two things wrong that matter for contracts:

- Tracked changes. It keeps an insertion in body text but drops one inside a
  table cell, so a cell can lose an inserted word while the rest of the
  document reads as accepted.
- Comments. It links a comment to the paragraph it annotates only for some
  paragraphs (none of the numbered clauses most contracts are made of, few
  table cells), so most comments end up detached from their text.

`prepare_docx` writes a copy of the .docx with every tracked change accepted
(insertions and moves kept, deletions and moved-from text removed, a deleted
table row dropped) and a marker run (`marker(n)`) where each comment's range
ends, then returns the comments: author, date, text, the words they
highlight, and their replies. docling carries a marker through like any other
text, so `docx_convert` can place each comment next to its own text. A reply
gets no marker of its own: it is written with the comment it answers.

Plain XML (lxml); no docling.
"""

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
NS = {"w": W, "w14": W14, "w15": W15}

# Letters and digits only: docling escapes nothing in it and splits no word on it.
MARKER_RE = re.compile(r"QZCMT(\d+)QZ")

# Tracked-change elements whose content stays (accepted) / goes.
_KEEP_CONTENT = ("ins", "moveTo")
_DROP = ("del", "moveFrom", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")
# Records of the formatting before a change; accepting keeps the current one.
_PROPERTY_CHANGES = (
    "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange",
    "tcPrChange", "tblGridChange", "numberingChange", "cellIns", "cellDel", "cellMerge",
)
_ANCHOR_CHARS = 120


def marker(n: int) -> str:
    return f"QZCMT{n}QZ"


@dataclass
class Reply:
    author: str
    initials: str
    date: str
    text: str


@dataclass
class Comment:
    """A top-level comment as written in the markdown, with its replies."""

    number: int  # what `marker(number)` carries
    author: str
    initials: str
    date: str  # YYYY-MM-DD, or "" when Word recorded none
    text: str
    anchor: str = ""  # the highlighted words, shortened
    resolved: bool = False
    replies: list[Reply] = field(default_factory=list)


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _text_of(element) -> str:
    """The visible text under `element`: w:t, a tab or break as a space,
    paragraphs separated by a newline."""
    parts: list[str] = []
    for node in element.iter():
        if node.tag == _w("t") and node.text:
            parts.append(node.text)
        elif node.tag in (_w("tab"), _w("br"), _w("cr")):
            parts.append(" ")
        elif node.tag == _w("p") and parts and node is not element:
            parts.append("\n")
    return "".join(parts)


def _rescue_comment_marks(node) -> None:
    """Move the comment range marks and references inside `node`, about to be
    dropped as deleted text, to just before it: a comment on words that were
    deleted still belongs where they were."""
    parent = node.getparent()
    for mark in list(node.iter(_w("commentRangeStart"), _w("commentRangeEnd"), _w("commentReference"))):
        if mark.tag == _w("commentReference"):
            run = etree.Element(_w("r"))
            run.append(mark)
            mark = run
        parent.insert(parent.index(node), mark)


def _accept_changes(root) -> None:
    for row in list(root.iter(_w("tr"))):
        if row.find("w:trPr/w:del", NS) is not None:
            row.getparent().remove(row)
    for tag in _PROPERTY_CHANGES + _DROP:
        for node in list(root.iter(_w(tag))):
            parent = node.getparent()
            if parent is not None:
                if tag in ("del", "moveFrom"):
                    _rescue_comment_marks(node)
                parent.remove(node)
    for tag in _KEEP_CONTENT:
        for node in list(root.iter(_w(tag))):
            parent = node.getparent()
            if parent is None:
                continue
            index = parent.index(node)
            for offset, child in enumerate(list(node)):
                parent.insert(index + offset, child)
            parent.remove(node)


def _marker_run(n: int):
    run = etree.Element(_w("r"))
    etree.SubElement(run, _w("t")).text = marker(n)
    return run


def _insert_marker(end, n: int) -> None:
    """Put `marker(n)` right after `end` (a commentRangeEnd or the run holding
    a commentReference). Inside a paragraph every container of `end` (the
    paragraph, a hyperlink, an inline content control, ...) takes a run; a
    range that ends between paragraphs or cells gets it at the end of the
    paragraph before (or the start of the one after)."""
    if any(a.tag == _w("p") for a in end.iterancestors()):
        parent = end.getparent()
        parent.insert(parent.index(end) + 1, _marker_run(n))
        return
    before = end.xpath("preceding::w:p[1]", namespaces=NS)
    if before:
        before[0].append(_marker_run(n))
        return
    after = end.xpath("following::w:p[1]", namespaces=NS)
    if after:
        after[0].insert(1 if after[0].find("w:pPr", NS) is not None else 0, _marker_run(n))


def _anchors(root) -> dict[str, str]:
    """The text each comment range covers, by comment id."""
    open_ranges: dict[str, list[str]] = {}
    anchors: dict[str, str] = {}
    for node in root.iter():
        tag = node.tag
        if tag == _w("commentRangeStart"):
            open_ranges[node.get(_w("id"))] = []
        elif tag == _w("commentRangeEnd"):
            cid = node.get(_w("id"))
            if cid in open_ranges:
                anchors[cid] = "".join(open_ranges.pop(cid))
        elif open_ranges and (tag == _w("t") and node.text or tag in (_w("tab"), _w("br"), _w("p"))):
            piece = node.text if tag == _w("t") else " "
            for parts in open_ranges.values():
                parts.append(piece)
    return anchors


def _shorten(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= _ANCHOR_CHARS else text[: _ANCHOR_CHARS - 1].rstrip() + "…"


def _read_comments(archive: zipfile.ZipFile) -> tuple[dict[str, dict], dict[str, tuple[str | None, bool]]]:
    """Comments by id ({author, initials, date, text, para_id}) and, by the
    paraId of a comment's last paragraph, (parent paraId, resolved)."""
    names = set(archive.namelist())
    comments: dict[str, dict] = {}
    if "word/comments.xml" in names:
        for c in etree.fromstring(archive.read("word/comments.xml")).iter(_w("comment")):
            paras = c.findall("w:p", NS)
            comments[c.get(_w("id"))] = {
                "author": c.get(_w("author")) or "",
                "initials": c.get(_w("initials")) or "",
                "date": (c.get(_w("date")) or "")[:10],
                "text": "\n".join(t for t in (_text_of(p).strip() for p in paras) if t),
                "para_id": paras[-1].get(f"{{{W14}}}paraId") if paras else None,
            }
    threads: dict[str, tuple[str | None, bool]] = {}
    if "word/commentsExtended.xml" in names:
        for ex in etree.fromstring(archive.read("word/commentsExtended.xml")).iter(f"{{{W15}}}commentEx"):
            threads[ex.get(f"{{{W15}}}paraId")] = (ex.get(f"{{{W15}}}paraIdParent"), ex.get(f"{{{W15}}}done") == "1")
    return comments, threads


def prepare_docx(source: Path, target: Path) -> list[Comment]:
    """Write `source` to `target` with tracked changes accepted and a marker
    after each top-level comment's range; return those comments, numbered as
    their markers are. Every other part of the package is copied unchanged."""
    with zipfile.ZipFile(source) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        raw, threads = _read_comments(archive)

        _accept_changes(root)
        anchors = _anchors(root)

        by_para = {c["para_id"]: cid for cid, c in raw.items() if c["para_id"]}
        parent_of = {
            cid: by_para.get(threads.get(c["para_id"], (None, False))[0])
            for cid, c in raw.items()
        }
        comments: list[Comment] = []
        number_of: dict[str, int] = {}
        for cid, c in raw.items():
            if parent_of[cid] is not None:
                continue
            number_of[cid] = len(comments)
            comments.append(Comment(
                number=len(comments), author=c["author"], initials=c["initials"], date=c["date"],
                text=c["text"], anchor=_shorten(anchors.get(cid, "")),
                resolved=threads.get(c["para_id"], (None, False))[1],
            ))
        for cid, c in raw.items():
            parent = parent_of[cid]
            while parent is not None and parent not in number_of:
                parent = parent_of.get(parent)
            if parent is not None and cid not in number_of:
                comments[number_of[parent]].replies.append(
                    Reply(author=c["author"], initials=c["initials"], date=c["date"], text=c["text"]),
                )

        placed: set[str] = set()
        for end in list(root.iter(_w("commentRangeEnd"))):
            cid = end.get(_w("id"))
            if cid in number_of and cid not in placed:
                placed.add(cid)
                _insert_marker(end, number_of[cid])
        for ref in list(root.iter(_w("commentReference"))):
            cid = ref.get(_w("id"))
            run = ref.getparent()
            if cid in number_of and cid not in placed and run is not None:
                placed.add(cid)
                _insert_marker(run, number_of[cid])

        document = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                data = document if info.filename == "word/document.xml" else archive.read(info.filename)
                out.writestr(info, data)
    return comments
