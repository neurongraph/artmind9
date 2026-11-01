"""The converted pptx bodies against their golden snapshot (slow: runs the
real docling).

`test/data/golden/pptx_bodies/*.md` were recorded from the conversion before
`docling_render` (docling 2.145, docling's own markdown). What changed on
purpose since: tables unpadded with merged-cell copies written once, and
speaker notes added under their slide. So, notes aside, a body keeps every
distinct word of its golden body, in the same order, and only drops repeats;
and no slide is flagged by the page check.
"""

import re

import pytest

from artmind.docling_render import NOTES_MARKER
from docling_real import PPTX_FIXTURES, convert_real, golden_path

pytestmark = pytest.mark.slow_docling

_NOTES_BLOCK = re.compile(re.escape(NOTES_MARKER) + r"\n\n(?:>[^\n]*\n?)+")


def _words(md: str) -> list[str]:
    md = re.sub(r"^\|(\s*:?-+:?\s*\|)+\s*$", " ", md, flags=re.M)
    return md.replace("|", " ").split()


@pytest.mark.parametrize("rel", PPTX_FIXTURES)
def test_a_body_keeps_every_word_of_its_golden_body_in_order(monkeypatch, tmp_path, rel):
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    body, _ = convert_real(monkeypatch, tmp_path, rel)
    body_words = _words(_NOTES_BLOCK.sub("", body))
    golden = _words(golden_path(rel).read_text(encoding="utf-8"))

    assert set(body_words) == set(golden)
    remaining = iter(golden)
    assert all(word in remaining for word in body_words), "words reordered"


@pytest.mark.parametrize(
    "rel, slides_with_notes",
    [
        ("sales_collateral/Payment1.pptx", 2),
        ("sales_collateral/Data1.pptx", 1),
        ("project_governance/Sample_DataFunctions_Monthly_Governance.pptx", 3),
    ],
)
def test_speaker_notes_are_in_the_body(monkeypatch, tmp_path, rel, slides_with_notes):
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    body, _ = convert_real(monkeypatch, tmp_path, rel)
    assert len(_NOTES_BLOCK.findall(body)) == slides_with_notes


@pytest.mark.parametrize("rel", PPTX_FIXTURES)
def test_no_slide_is_flagged(monkeypatch, tmp_path, rel):
    monkeypatch.delenv("ARTMIND_PAGE_REPAIR", raising=False)
    _, record = convert_real(monkeypatch, tmp_path, rel)
    assert record["page_check"] == "detect"
    assert record["flagged"] == [] and record["pages"] > 0
