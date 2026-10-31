"""A .docx converted to markdown with its comments and tables made usable.

`convert_docx` runs docling's Python API on a copy of the .docx that
`docx_prepare` made (tracked changes accepted, a marker where each comment's
range ends) and writes the markdown, its images exported beside it as the
docling CLI's `--image-export-mode referenced` does. On top of docling's
markdown:

Tables
    Tidied and laid out as for every format (`docling_render`): unpadded,
    a cell merged across columns written once, empty rows and columns
    dropped, every table a top-level block.

Comments
    Each comment is an Obsidian callout quoting the words it highlights, with
    its replies, right after its paragraph; for a list item, indented into
    the item; for a table row, after the whole table, naming the row::

        > [!comment] Comment on "Delivery Fees"
        > Ann Lee (AL), 2026-02-16: Does this replace the earlier rate card?
        > Reply: Bo King (BK), 2026-03-09: Yes.

    A comment whose marker docling did not carry through (its text sits in a
    header, a footer or a text box) goes under `## Document comments` at the
    end, never dropped.

Needs docling (the `[ingest]` extra); imported on call.
"""

import html
import re
import tempfile
from pathlib import Path

from loguru import logger

from artmind.docling_render import is_table_line, normalise_tables, tidy_tables
from artmind.docx_prepare import MARKER_RE, Comment, prepare_docx

UNPLACED_HEADING = "## Document comments"
_ROW_CONTEXT_CHARS = 80
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)")

_converter = None


def _docling_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter

        _converter = DocumentConverter(allowed_formats=[InputFormat.DOCX])
    return _converter


# ── comments ───────────────────────────────────────────────────────────────────


def _who(author: str, initials: str, date: str) -> str:
    who = f"{author} ({initials})" if initials else author or "Unknown"
    return f"{who}, {date}" if date else who


def callout(comment: Comment, *, row: str = "") -> list[str]:
    """The callout lines of `comment` and its replies."""
    esc = lambda s: html.escape(s, quote=False)  # as docling escapes the body text
    title = "Comment"
    if comment.anchor:
        title += f' on "{esc(comment.anchor)}"'
    if row:
        title += f' in the table row "{esc(row)}"'
    if comment.resolved:
        title += " (resolved)"
    lines = [f"> [!comment] {title}"]
    entries = [("", comment.author, comment.initials, comment.date, comment.text)] + [
        ("Reply: ", r.author, r.initials, r.date, r.text) for r in comment.replies
    ]
    for prefix, author, initials, date, text in entries:
        body = esc(text).split("\n") or [""]
        lines.append(f"> {prefix}{esc(_who(author, initials, date))}: {body[0]}")
        lines.extend(f"> {extra}" for extra in body[1:])
    return lines


def _row_context(row_line: str) -> str:
    cells = [c.strip() for c in row_line.strip().strip("|").split("|")]
    first = next((c for c in cells if c), "")
    first = re.sub(r"\s+", " ", first.replace("**", ""))
    return first if len(first) <= _ROW_CONTEXT_CHARS else first[: _ROW_CONTEXT_CHARS - 1].rstrip() + "…"


def place_comments(markdown: str, comments: list[Comment]) -> str:
    """`markdown` with each comment marker replaced by its callout (see the
    module docstring) and every marker removed."""
    by_number = {c.number: c for c in comments}
    placed: set[int] = set()
    out: list[str] = []
    table_notes: list[tuple[Comment, str]] = []
    lines = markdown.split("\n")
    for i, line in enumerate(lines):
        numbers = [int(n) for n in MARKER_RE.findall(line)]
        found = [by_number[n] for n in numbers if n in by_number and n not in placed]
        placed.update(c.number for c in found)
        line = MARKER_RE.sub("", line)
        if is_table_line(line):
            # A row that held nothing but markers (its text was deleted) goes;
            # its comments still follow the table.
            blank = not line.strip().strip("|").replace("|", "").strip()
            if not (found and blank and out and is_table_line(out[-1])):
                out.append(line)
            table_notes.extend((c, _row_context(line)) for c in found)
            if table_notes and not (i + 1 < len(lines) and is_table_line(lines[i + 1])):
                for comment, row in table_notes:
                    out.extend(["", *callout(comment, row=row)])
                out.append("")
                table_notes = []
            continue
        out.append(line)
        if not found:
            continue
        item = _LIST_ITEM_RE.match(line)
        if item:
            pad = " " * len(item.group(0))
            for comment in found:
                out.extend(pad + l for l in callout(comment))
        else:
            for comment in found:
                out.extend(["", *callout(comment)])
            out.append("")
    unplaced = [c for c in comments if c.number not in placed]
    if unplaced:
        out.extend(["", UNPLACED_HEADING])
        for comment in unplaced:
            out.extend(["", *callout(comment)])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


# ── conversion ─────────────────────────────────────────────────────────────────


def convert_docx(source: Path, md_file: Path, *, work_dir: Path | None = None) -> dict:
    """Convert `source` to `md_file`, its images in `<md stem>_artifacts/`
    beside it, referenced relative to the markdown, and return what the
    conversion did (`{"comments": n}`) for conversion.json. Raises when docling
    fails. A .docx whose XML cannot be prepared is converted as it is (no
    comments, tracked changes as docling reads them), with a warning.
    `work_dir` holds the prepared copy while docling reads it."""
    from docling_core.types.doc.base import ImageRefMode

    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        prepared = Path(tmp) / source.name
        try:
            comments = prepare_docx(source, prepared)
        except Exception as exc:
            logger.warning("Could not prepare {} (tracked changes, comments): {}", source.name, exc)
            prepared, comments = source, []
        doc = _docling_converter().convert(prepared).document

    tidy_tables(doc)
    md_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save_as_markdown(
        md_file,
        artifacts_dir=Path(f"{md_file.stem}_artifacts"),
        image_mode=ImageRefMode.REFERENCED,
        compact_tables=True,
    )
    markdown = place_comments(normalise_tables(md_file.read_text(encoding="utf-8")), comments)
    md_file.write_text(markdown, encoding="utf-8")
    if comments:
        logger.info("{}: {} comment(s) written inline", source.name, len(comments))
    return {"comments": len(comments)}
