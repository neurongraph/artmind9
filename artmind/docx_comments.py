"""Word comments in a converted .docx's markdown.

docling reads a .docx's comments (author, initials, time, text) but keeps
them in the document's NOTES layer, and its markdown export writes the BODY
layer only: the CLI's `--to md` drops every comment. Exported with NOTES as
well, they all land at the end of the document, where a chunk sees a remark
("Should be 12k.") cut off from the paragraph it is about.

`markdown_with_comments` renders the markdown again from the JSON the same
docling run wrote, with each comment as an Obsidian callout right after the
block that holds its paragraph, so a comment and its text share a chunk::

    The budget is 10k.

    > [!comment]
    > [author: Ann Lee (AL), time: 2026-10-31T07:23:03.000+00:00]: Should be 12k.

A comment inside a list or a table follows the whole list or table. docling
links a comment to plain paragraphs only; one it left unlinked (on a list
item or a table cell) goes under a `## Document comments` heading at the end,
never dropped. Without comments the CLI's markdown stands as it is, so a
.docx without comments converts byte for byte as before.

Needs docling-core, part of the `[ingest]` extra; imported on call.
"""

import html
from pathlib import Path

CALLOUT = "> [!comment]"
UNANCHORED_HEADING = "## Document comments"


def _callout(text: str) -> str:
    return "\n".join([CALLOUT, *(f"> {line}" for line in html.escape(text, quote=False).splitlines())])


def _comment_text(doc, group) -> str:
    """The text of a comment group (a comment and any replies, one per line)."""
    texts = (getattr(child.resolve(doc), "text", "") for child in group.children)
    return "\n".join(t.strip() for t in texts if t and t.strip())


def markdown_with_comments(json_path: Path) -> str | None:
    """The markdown of the docling JSON at `json_path` with its comments
    inline, or None when it has none (keep the CLI's markdown). Images are
    referenced exactly as the CLI's `--image-export-mode referenced` writes
    them."""
    from docling_core.transforms.serializer.base import SerializationResult
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    from docling_core.types.doc.base import ImageRefMode
    from docling_core.types.doc.document import (
        DocItem, DoclingDocument, InlineGroup, ListGroup, ListItem,
    )
    from docling_core.types.doc.labels import GroupLabel

    doc = DoclingDocument.load_from_json(json_path)
    comments = {g.self_ref: g for g in doc.groups if g.label == GroupLabel.COMMENT_SECTION}
    if not comments:
        return None
    placed: set[str] = set()

    def is_block(item) -> bool:
        # A paragraph, heading, table, picture, list or inline group, not a
        # piece of one: a list item and anything inside a list or inline
        # group are written as part of their parent.
        if isinstance(item, ListItem) or item.parent is None:
            return False
        return not isinstance(item.parent.resolve(doc), (ListGroup, InlineGroup, ListItem))

    class _Serializer(MarkdownDocSerializer):
        def serialize(self, *, item=None, **kwargs):
            result = super().serialize(item=item, **kwargs)
            if item is None or not is_block(item):
                return result
            notes = []
            for doc_item in result.get_unique_doc_items():
                for ref in doc_item.comments if isinstance(doc_item, DocItem) else ():
                    group = comments.get(ref.cref)
                    if group is None or ref.cref in placed:
                        continue
                    placed.add(ref.cref)
                    if text := _comment_text(doc, group):
                        notes.append(_callout(text))
            if not notes:
                return result
            return SerializationResult(text="\n\n".join([result.text, *notes]), spans=result.spans)

    markdown = _Serializer(doc=doc, params=MarkdownParams(image_mode=ImageRefMode.REFERENCED)).serialize().text
    unanchored = [
        _callout(text) for ref, group in comments.items()
        if ref not in placed and (text := _comment_text(doc, group))
    ]
    if unanchored:
        markdown = "\n\n".join([markdown, UNANCHORED_HEADING, *unanchored])
    return markdown
