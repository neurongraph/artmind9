"""Repair of flagged pdf pages by the image model (spec
docs/superpowers/specs/2026-10-31-conversion-page-check-design.md).

Only in `ARTMIND_PAGE_REPAIR=repair`: a page the page check flagged is
rendered, its image is sent to the image model (`ARTMIND_IMAGE_MODEL`,
through the provider image description uses), and the transcription replaces
docling's page only if `verify` accepts it. Page images leave the machine here
and nowhere else.

A transcription is cached by the sha256 of its page image, the model and
`PROMPT_VERSION`, so an unchanged file converts to the same body again (model
output is not deterministic; without the cache every re-conversion would
change the body hash and bump the version).
"""

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from artmind import page_check
from paths import DATA_DIR
from utils.functions import log_llm_call

#: Bump when the prompt changes: the cache is keyed on it.
PROMPT_VERSION = 1

TRANSCRIBE_PROMPT = (
    "Transcribe this page to GitHub-flavoured markdown. Copy every word and number "
    "exactly as printed; do not summarise, correct, reorder or omit anything. "
    "Render tables as markdown tables; where one cell spans the full row, put its text "
    "once in a single cell instead of repeating it. Skip running headers, footers and "
    "page numbers. Output only the markdown."
)

RENDER_DPI = 150
TRANSCRIBE_TIMEOUT = 180
CACHE_DIR = DATA_DIR / "page_repair_cache"

_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


@dataclass
class RepairResult:
    repaired: dict[int, str] = field(default_factory=dict)
    unrepaired: list[int] = field(default_factory=list)


def render_page(pdf: Path, page: int, out_dir: Path) -> Path:
    """Page `page` (1-based) of `pdf` as a PNG in `out_dir`."""
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(pdf))
    try:
        image = doc[page - 1].render(scale=RENDER_DPI / 72).to_pil()
        target = out_dir / f"page-{page}.png"
        image.save(target)
        return target
    finally:
        doc.close()


def _numbers(text: str) -> set[str]:
    """The numbers in `text`, thousands separators and a trailing full stop
    dropped, so `1,200.` and `1200` are one number."""
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text)}


def verify(transcription: str, source_text: str) -> tuple[float, list[str]]:
    """The transcription's coverage of the page's source text, and the reasons
    it fails (empty when it passes): coverage below the page check's
    threshold, or a number of the source text absent from it."""
    cov, missing = page_check.coverage(source_text, transcription)
    absent = sorted(_numbers(source_text) - _numbers(transcription))
    reasons = []
    if cov < page_check.COVERAGE_THRESHOLD:
        reasons.append(f"coverage {cov:.2f}")
        reasons.extend(missing[:3])
    reasons.extend(f"number absent: {n}" for n in absent[:3])
    return cov, reasons


def cache_path(image: Path, model: str) -> Path:
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    return CACHE_DIR / f"{digest}-{safe_model}-{PROMPT_VERSION}.md"


def _cached(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.strip() else None


def transcribe(image: Path, model: str, env: dict) -> str:
    """One image-model call for `image`; "" when the model fails."""
    from artmind.extraction import describe_image

    t0 = time.monotonic()
    try:
        text = describe_image(image, model, TRANSCRIBE_PROMPT, TRANSCRIBE_TIMEOUT, env) or ""
    except Exception as exc:
        logger.error("Page transcription failed for {} in {:.1f}s: {}", image.name, time.monotonic() - t0, exc)
        return ""
    log_llm_call("chat", model, f"[IMAGE: {image.name}]\n{TRANSCRIBE_PROMPT}", text)
    return text


def repair_pages(
    pdf: Path,
    pages: list[int],
    source: dict[int, str],
    model: str,
    env: dict,
    work_dir: Path,
) -> RepairResult:
    """Replace each page in `pages` by a verified transcription, or leave it
    as docling produced it. `source` maps a page number to its source text."""
    result = RepairResult()
    for page in pages:
        image = render_page(pdf, page, work_dir)
        key = cache_path(image, model)
        text = _cached(key)
        fresh = text is None
        if fresh:
            text = transcribe(image, model, env)
        if not text.strip():
            logger.warning("Page {} of {}: empty transcription, kept docling's page", page, pdf.name)
            result.unrepaired.append(page)
            continue
        cov, reasons = verify(text, source.get(page, ""))
        if reasons:
            logger.warning(
                "Page {} of {}: transcription rejected ({}); kept docling's page",
                page, pdf.name, "; ".join(reasons),
            )
            result.unrepaired.append(page)
            continue
        if fresh:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            key.write_text(text, encoding="utf-8")
        result.repaired[page] = text
        logger.info("Page {} of {} repaired by {} (coverage {:.2f})", page, pdf.name, model, cov)
    return result
