"""The page check against the real docling on the pdf fixtures (slow).

Synthetic tests cannot show how docling really writes a page, so these pin
what it does on three fixtures: a prose pdf whose text layer is hyphenated at
line ends (nothing lost, nothing may be flagged), a pdf with a page of code
docling turns into a picture (text lost, that page flagged), and a pdf with no
text layer (skipped, never flagged).
"""

import pytest

from docling_real import convert_real

pytestmark = pytest.mark.slow_docling


def test_a_prose_pdf_with_hyphenated_lines_is_not_flagged(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTMIND_PAGE_REPAIR", raising=False)
    _, record = convert_real(monkeypatch, tmp_path, "fiction/The Adventure of the Blue Carbuncle.pdf")
    assert record["page_check"] == "detect" and record["pages"] == 10
    assert record["flagged"] == []


def test_a_page_docling_turned_into_a_picture_is_flagged(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTMIND_PAGE_REPAIR", raising=False)
    _, record = convert_real(monkeypatch, tmp_path, "technical_paper/How to Build a Graph RAG App.pdf")
    assert [f["page"] for f in record["flagged"]] == [8]
    assert record["unrepaired"] == [8]


def test_a_pdf_without_a_text_layer_is_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTMIND_PAGE_REPAIR", raising=False)
    _, record = convert_real(monkeypatch, tmp_path, "technical_paper/Rubric-Based Rewards for RL.pdf")
    assert record["page_check"] == "skipped" and record["why"] == "no text layer"
