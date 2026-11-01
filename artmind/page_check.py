"""Per-page check of a conversion against the source file's own text (spec
docs/superpowers/specs/2026-10-31-conversion-page-check-design.md).

docling can lose text without raising (a table cell's last line, say), so the
extraction loss guard cannot see it. This compares, page by page, the text the
source file holds with the markdown docling made of that page, and flags a page
whose source text is not (nearly) all there. No model, nothing leaves the
machine, and nothing keys on what the document is about.

Source text: `pypdfium2` for a pdf, `python-pptx` for a pptx (shapes, tables,
grouped shapes and speaker notes); `None` for any other type. A pdf with no text
layer has nothing to compare against: it is `skipped`, never flagged.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from artmind import docling_render

PAGE_REPAIR_ENV = "ARTMIND_PAGE_REPAIR"
MODES = ("off", "detect", "repair")
DEFAULT_MODE = "detect"

#: A page whose source text is covered below this is flagged (and a
#: transcription must reach it to be accepted: `page_repair.verify`).
COVERAGE_THRESHOLD = 0.97
SHINGLE = 3
#: A header/footer line repeats on at least this share of pages (and on at
#: least three) to be dropped from the source text.
FURNITURE_SHARE = 0.4
FURNITURE_EDGE_LINES = 3

SUPPORTED_SUFFIXES = (".pdf", ".pptx")


def page_repair_mode(env: dict) -> str:
    """`ARTMIND_PAGE_REPAIR`: off | detect | repair; `detect` when unset or unknown."""
    raw = (env.get(PAGE_REPAIR_ENV) or "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw not in MODES:
        logger.warning("{}={!r} is not one of {}; using {!r}", PAGE_REPAIR_ENV, raw, "|".join(MODES), DEFAULT_MODE)
        return DEFAULT_MODE
    return raw


@dataclass
class PageFinding:
    page: int
    coverage: float
    reason: str
    #: A few source phrases the page lacks; for logs and the repair step, never
    #: written to conversion.json (they are the document's own text).
    missing: list[str] = field(default_factory=list)

    def record(self) -> dict:
        return {"page": self.page, "coverage": round(self.coverage, 3), "reason": self.reason}


@dataclass
class PageCheck:
    """`status`: `checked`, or `skipped` (with `why`): no page text to compare."""

    status: str
    pages: int = 0
    findings: list[PageFinding] = field(default_factory=list)
    why: str = ""
    #: The source text and docling markdown of each page, kept for the repair step.
    source: dict[int, str] = field(default_factory=dict)
    markdown: dict[int, str] = field(default_factory=dict)


# ── source text ───────────────────────────────────────────────────────────────


def source_pages(path: Path) -> list[str] | None:
    """The text of each page (slide) of `path`, or None for a type with no pages
    to compare. Raises nothing for an unreadable file: that is an empty list."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _pdf_pages(path)
        if suffix == ".pptx":
            return _pptx_pages(path)
    except Exception as e:
        logger.warning("Could not read the page text of {}: {}", path.name, e)
        return []
    return None


def _pdf_pages(path: Path) -> list[str]:
    import pypdfium2

    pdf = pypdfium2.PdfDocument(str(path))
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                pages.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
        return pages
    finally:
        pdf.close()


def _pptx_pages(path: Path) -> list[str]:
    from pptx import Presentation

    pages = []
    for slide in Presentation(str(path)).slides:
        lines: list[str] = []
        _shape_lines(slide.shapes, lines)
        if slide.has_notes_slide:
            frame = slide.notes_slide.notes_text_frame
            if frame is not None:
                lines.extend(frame.text.splitlines())
        pages.append("\n".join(lines))
    return pages


def _shape_lines(shapes, lines: list[str]) -> None:
    for shape in shapes:
        if getattr(shape, "shapes", None) is not None:  # a group
            _shape_lines(shape.shapes, lines)
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            lines.extend(_text_lines(shape.text_frame.text))
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    lines.extend(_text_lines(cell.text))


def _text_lines(text: str) -> list[str]:
    return [line for line in re.split(r"[\r\n\x0b]+", text) if line.strip()]


# ── normalisation and coverage ────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z0-9£$€%.]+(?:[-'/][A-Za-z0-9]+)*")
_PAGE_OF = re.compile(r"(?im)^\s*page\s+\d+(\s+of\s+\d+)?\s*$")


#: Characters a pdf text layer leaves where a word was hyphenated at a line end
#: (soft hyphen, U+FFFE / U+FFFF noncharacters): not part of any word.
_LAYER_JUNK = dict.fromkeys(map(ord, "\u00ad\ufffe\uffff"))


def _tokens(text: str) -> list[str]:
    text = html.unescape(text).replace("\xa0", " ").translate(_LAYER_JUNK)
    text = (
        text.replace("’", "'").replace("‘", "'").replace("“", '"')
        .replace("”", '"').replace("–", "-").replace("—", "-")
    )
    text = _PAGE_OF.sub("", text)
    return [w.lower().strip(".") for w in _WORD.findall(text) if w.strip(".")]


def _shingles(tokens: list[str], n: int = SHINGLE) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(max(0, len(tokens) - n + 1))}


def _fragment_shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    """Shingles taken inside one layout fragment (a line, split at runs of
    spaces), so table reading order cannot create phantom phrases."""
    found: set[tuple[str, ...]] = set()
    for line in text.splitlines():
        for fragment in re.split(r"\s{2,}", line):
            found |= _shingles(_tokens(fragment), n)
    return found


def strip_furniture(pages: list[str]) -> list[str]:
    """Drop running headers and footers from the pages' text: a line among a
    page's first or last three that, ignoring digits (so `Page 3 of 40` is one
    line), repeats on at least 40 % of pages (and at least three). Lines in the
    middle of a page are never touched, however often they repeat."""

    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip())

    def lines_of(page: str) -> list[str]:
        return [line for line in page.splitlines() if line.strip()]

    def edges(lines: list[str]) -> set[int]:
        size = FURNITURE_EDGE_LINES
        return set(range(min(size, len(lines)))) | set(range(max(0, len(lines) - size), len(lines)))

    counts: Counter[str] = Counter()
    for page in pages:
        lines = lines_of(page)
        counts.update({key(lines[i]) for i in edges(lines)})
    cut = max(3, int(len(pages) * FURNITURE_SHARE))
    cleaned = []
    for page in pages:
        lines = lines_of(page)
        drop = {i for i in edges(lines) if counts[key(lines[i])] >= cut}
        cleaned.append("\n".join(line for i, line in enumerate(lines) if i not in drop))
    return cleaned


def _squash(tokens) -> str:
    """Words run together without spaces or punctuation: docling sometimes writes
    `combi nation` or `6 . 30` for what the text layer holds as one word."""
    return re.sub(r"[^0-9a-z£$€%]", "", "".join(tokens))


def coverage(source_text: str, markdown: str, n: int = SHINGLE) -> tuple[float, list[str]]:
    """The share of the source text's phrases the markdown also holds, and a few
    of the phrases it lacks. 1.0 when the source has no phrase to compare.
    Phrases are compared with spaces and punctuation squashed out."""
    wanted = _fragment_shingles(source_text, n)
    if not wanted:
        return 1.0, []
    have = _squash(_tokens(markdown))
    missing = {m for m in wanted if (key := _squash(m)) and key not in have}
    return 1 - len(missing) / len(wanted), [" ".join(m) for m in sorted(missing)[:6]]


def structural_flags(markdown: str) -> list[str]:
    """Signs of a loss in the markdown itself: a long table cell that ends in a
    colon (what follows it was dropped), long table rows repeated."""
    flags = []
    rows = [line for line in markdown.splitlines() if line.lstrip().startswith("|")]
    for row in rows:
        for cell in row.strip().strip("|").split("|"):
            cell = cell.strip()
            if len(cell) > 60 and cell.endswith(":"):
                flags.append("cell-ends-with-colon")
                break
    if any(count > 1 and len(row) > 200 for row, count in Counter(rows).items()):
        flags.append("repeated-rows")
    return list(dict.fromkeys(flags))


# ── the check ─────────────────────────────────────────────────────────────────


def check_pages(
    source: list[str],
    page_md: dict[int, str],
    threshold: float = COVERAGE_THRESHOLD,
    spilled: dict[int, str] | None = None,
) -> list[PageFinding]:
    """Findings for the pages of `page_md` (page number -> markdown) whose
    source text, in `source` (index = page number - 1), is not covered, or whose
    markdown shows a structural sign of loss. `spilled` holds, by page, text
    docling recorded on that page but exports on an earlier one
    (`spilled_text`): misplaced, not lost."""
    source = strip_furniture(source)
    spilled = spilled or {}
    findings = []
    for page_no in sorted(page_md):
        text = source[page_no - 1] if 0 < page_no <= len(source) else ""
        # docling puts a paragraph that runs over a page break on the page it
        # starts on, so a phrase counts as present if a neighbouring page has it
        around = "\n".join(page_md.get(p, "") for p in (page_no - 1, page_no, page_no + 1))
        around += "\n" + spilled.get(page_no, "")
        cov, missing = coverage(text, around)
        structural = structural_flags(page_md[page_no])
        if cov < threshold:
            findings.append(PageFinding(page_no, cov, "missing-text", missing))
        elif structural:
            findings.append(PageFinding(page_no, cov, structural[0], []))
    return findings


def spilled_text(doc) -> dict[int, str]:
    """By page, the text of items docling recorded on that page and on an
    earlier one too. A per-page export shows such an item on its first page
    only, so without this its later pages would read as having lost the text
    (seen: a paragraph of page 39 merged into an item that starts on page 34)."""
    spilled: dict[int, list[str]] = {}
    for item, _ in doc.iterate_items():
        text = getattr(item, "text", "") or ""
        pages = sorted({prov.page_no for prov in getattr(item, "prov", [])})
        for page_no in pages[1:] if text else ():
            spilled.setdefault(page_no, []).append(text)
    return {n: "\n".join(texts) for n, texts in spilled.items()}


def check_document(path: Path, doc) -> PageCheck:
    """Compare `path`'s own page text with docling's pages in `doc`, each
    rendered as the body renders it (`docling_render.page_markdowns`; a
    pptx slide with its speaker notes). `doc`'s tables are expected tidied."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return PageCheck("skipped", why="no page text for this type")
    source = source_pages(path)
    if not source:
        return PageCheck("skipped", why="no page text in the source")
    if not any(_fragment_shingles(page) for page in source):
        return PageCheck("skipped", why="no text layer")
    markdown = docling_render.page_markdowns(doc, notes=suffix == ".pptx")
    if markdown is None:
        return PageCheck("skipped", why="docling's pages do not join to its document")
    if len(source) != len(markdown):
        return PageCheck(
            "skipped", pages=len(markdown),
            why=f"source has {len(source)} page(s), docling {len(markdown)}",
        )
    findings = check_pages(source, markdown, spilled=spilled_text(doc))
    stripped = strip_furniture(source)
    return PageCheck(
        "checked", pages=len(markdown), findings=findings,
        source={n: stripped[n - 1] for n in sorted(markdown)}, markdown=markdown,
    )


def summary(check: PageCheck, repaired: list[int] | None = None) -> dict:
    """Counts for the job-status line."""
    flagged = [f.page for f in check.findings]
    repaired = [p for p in (repaired or []) if p in flagged]
    return {"flagged": len(flagged), "repaired": len(repaired), "unrepaired": len(flagged) - len(repaired)}
