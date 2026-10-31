"""`ingest._convert_binary_via_docling` for a pdf or pptx: the body is rendered
from the docling JSON (`docling_render`), a pptx gains its speaker notes, the
JSON is not kept, and an unusable JSON never fails the conversion. docling's
command is stubbed to write synthetic output (no docling run, no model)."""

import json

import pytest

import artmind.ingest as ing
from artmind import docling_render

PADDED_MD = """\
# Heading

| Name        | Value       |
|-------------|-------------|
| alpha       | one         |
| beta        | two         |

Body text.
"""


def _stub_docling(monkeypatch, md: str, doc=None, json_text=None):
    """Make the docling command write `md` (and `doc` as JSON, or `json_text`)
    where docling would."""
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append(cmd)
        (ing.MARKDOWNS_DIR / "deck.md").write_text(md, encoding="utf-8")
        if json_text is not None:
            (ing.MARKDOWNS_DIR / "deck.json").write_text(json_text, encoding="utf-8")
        elif doc is not None:
            doc.save_as_json(ing.MARKDOWNS_DIR / "deck.json")
        return 0, "", ""

    monkeypatch.setattr(ing, "run_command", run)
    monkeypatch.setattr(ing, "_describe_image", lambda *a, **k: pytest.fail("no image expected"))
    return calls


def _deck(notes: dict[int, str], table=False):
    pytest.importorskip("docling_core")
    from docling_core.types.doc import (
        BoundingBox, ContentLayer, DocItemLabel, DoclingDocument, ProvenanceItem, Size,
    )
    from docling_core.types.doc.document import TableCell, TableData

    def prov(n, text):
        return ProvenanceItem(page_no=n, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, len(text)))

    doc = DoclingDocument(name="deck")
    for n in (1, 2):
        doc.add_page(page_no=n, size=Size(width=100, height=100))
        text = f"Slide {n} text."
        doc.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(n, text))
    if table:
        cells = [
            TableCell(text=t, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                      start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=r == 0)
            for r, row in enumerate([["Name", "", "Value"], ["alpha", "", "one"]]) for c, t in enumerate(row)
        ]
        doc.add_table(data=TableData(num_rows=2, num_cols=3, table_cells=cells), prov=prov(2, "table"))
    for n, text in notes.items():
        doc.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(n, text), content_layer=ContentLayer.NOTES)
    return doc


def _pdf(ingest_env):
    vault, source = ingest_env
    return source.with_name("deck.pdf")


def test_docling_is_asked_for_markdown_and_json_in_one_run(ingest_env):
    vault, source = ingest_env
    assert ing._docling_command(source).startswith("uv run docling --to md --to json ")


def test_the_body_is_rendered_from_the_json_with_tidy_tables(ingest_env, monkeypatch):
    pdf = _pdf(ingest_env)
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    _stub_docling(monkeypatch, "docling's own markdown", doc=_deck({}, table=True))

    body, err = ing._convert_binary_via_docling(pdf, "model")

    assert "| Name | Value |\n| - | - |\n| alpha | one |" in body
    assert "docling's own markdown" not in body
    assert err["conversion"] == {"render_version": docling_render.RENDER_VERSION, "page_check": "off"}
    assert not (ing.MARKDOWNS_DIR / "deck.json").exists()


def test_a_pptx_body_gets_its_speaker_notes(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    _stub_docling(monkeypatch, "", doc=_deck({2: "Say this aloud."}))

    body, _ = ing._convert_binary_via_docling(source, "model")

    assert body.index("Slide 2 text.") < body.index("**Speaker notes:**") < body.index("> Say this aloud.")


def test_a_pdf_never_gets_notes(ingest_env, monkeypatch):
    pdf = _pdf(ingest_env)
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    _stub_docling(monkeypatch, "", doc=_deck({2: "Not a slide note."}))

    body, _ = ing._convert_binary_via_docling(pdf, "model")

    assert "Speaker notes" not in body and "Not a slide note." not in body


@pytest.mark.parametrize("json_text", [None, "{not json"])
def test_a_missing_or_unreadable_json_falls_back_to_doclings_markdown(ingest_env, monkeypatch, json_text):
    pdf = _pdf(ingest_env)
    _stub_docling(monkeypatch, PADDED_MD, json_text=json_text)

    body, err = ing._convert_binary_via_docling(pdf, "model")

    assert "| Name | Value |\n| - | - |\n| alpha | one |" in body and "Body text." in body
    assert err["conversion"]["page_check"] == "skipped"
    assert err["conversion"]["why"] == "docling JSON unusable"


def test_a_json_that_fails_to_render_does_not_fail_the_conversion(ingest_env, monkeypatch):
    vault, source = ingest_env
    _stub_docling(monkeypatch, "Slide 1 text.\n\nSlide 2 text.", doc=_deck({1: "A note."}))

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(docling_render, "render_body", boom)

    body, err = ing._convert_binary_via_docling(source, "model")

    assert body == "Slide 1 text.\n\nSlide 2 text." and "conversion" in err


def test_docling_failure_keeps_the_error_path(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "run_command", lambda *a, **k: (1, "", "boom"))

    body, err = ing._convert_binary_via_docling(source, "model")

    assert body is None and err == {"status": "failed", "error": "Docling conversion failed"}


def test_conversion_json_records_the_conversion_beside_the_provenance(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    _stub_docling(monkeypatch, "", doc=_deck({}))

    result = ing.ingest_file(source, "model", "general", chunk_size=6000)

    sidecar = json.loads((ing.MARKDOWNS_DIR / "deck_chunks" / ing.CONVERSION_SIDECAR).read_text())
    assert sidecar["conversion"]["render_version"] == docling_render.RENDER_VERSION
    assert {"source_sha256", "source_type", "source_path"} <= set(sidecar)
    assert set(result["provenance"]) >= set(ing._CONVERSION_KEYS)


def test_a_stubbed_conversion_leaves_no_conversion_record(ingest_env, monkeypatch):
    from conftest import _fake_docling

    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody.\n"]))

    ing.ingest_file(source, "model", "general", chunk_size=6000)

    sidecar = json.loads((ing.MARKDOWNS_DIR / "deck_chunks" / ing.CONVERSION_SIDECAR).read_text())
    assert "conversion" not in sidecar


def test_a_reextract_from_the_stored_markdown_keeps_the_conversion_record(ingest_env, monkeypatch):
    from conftest import stage_as_extracted

    vault, source = ingest_env
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "off")
    _stub_docling(monkeypatch, "", doc=_deck({}))
    first = ing.ingest_file(source, "model", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, first)
    monkeypatch.setattr(ing, "run_command", lambda *a, **k: pytest.fail("no conversion on a re-extraction"))

    ing.ingest_file(source, "model", "general", chunk_size=6000, reextract=True)

    sidecar = json.loads((ing.MARKDOWNS_DIR / "deck_chunks" / ing.CONVERSION_SIDECAR).read_text())
    assert sidecar["conversion"]["page_check"] == "off"
