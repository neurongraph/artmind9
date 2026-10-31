"""page_repair: transcribing flagged pdf pages (synthetic text only, mocked model)."""

import json

import pytest

import artmind.ingest as ing
from artmind import page_check, page_repair
from test_page_check import _docling_doc, _pdf_bytes, _pdf_source, _sidecar, _stub_docling_json

SOURCE_PAGE_2 = "zeta eta theta iota kappa and the sum is 42 exactly"
GOOD = "zeta eta theta iota kappa and the sum is 42 exactly"


def _two_page_pdf(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf_bytes(["alpha beta gamma delta epsilon", SOURCE_PAGE_2]))
    return pdf


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    path = tmp_path / "page_repair_cache"
    monkeypatch.setattr(page_repair, "CACHE_DIR", path)
    return path


@pytest.fixture
def model_calls(monkeypatch):
    """Every transcription, counted; the answer is `GOOD` unless a test changes it."""
    calls = []

    def fake(image, model, env):
        calls.append((image.name, model))
        return GOOD

    monkeypatch.setattr(page_repair, "transcribe", fake)
    return calls


# ── verify ────────────────────────────────────────────────────────────────────


def test_a_full_transcription_passes():
    cov, reasons = page_repair.verify(GOOD, SOURCE_PAGE_2)
    assert cov == 1.0 and reasons == []


def test_a_transcription_missing_a_number_is_rejected():
    """The number sits alone on its line, so it forms no phrase: coverage alone
    would pass this transcription."""
    source = "zeta eta theta iota kappa\n42"
    cov, reasons = page_repair.verify("zeta eta theta iota kappa", source)
    assert cov == 1.0 and reasons == ["number absent: 42"]


def test_a_transcription_missing_text_is_rejected():
    cov, reasons = page_repair.verify("zeta eta theta", SOURCE_PAGE_2)
    assert cov < page_check.COVERAGE_THRESHOLD and any("coverage" in r for r in reasons)


# ── repair_pages ──────────────────────────────────────────────────────────────


def test_an_accepted_transcription_replaces_the_page(tmp_path, cache_dir, model_calls):
    pdf = _two_page_pdf(tmp_path)
    result = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path)
    assert result.repaired == {2: GOOD} and result.unrepaired == []
    assert model_calls == [("page-2.png", "vlm")]


def test_a_rejected_transcription_keeps_docling_page(tmp_path, cache_dir, monkeypatch, caplog):
    pdf = _two_page_pdf(tmp_path)
    monkeypatch.setattr(page_repair, "transcribe", lambda image, model, env: "zeta eta")
    result = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path)
    assert result.repaired == {} and result.unrepaired == [2]
    assert not cache_dir.exists() or not any(cache_dir.iterdir())  # rejects are not cached


def test_an_empty_transcription_is_unrepaired(tmp_path, cache_dir, monkeypatch):
    pdf = _two_page_pdf(tmp_path)
    monkeypatch.setattr(page_repair, "transcribe", lambda image, model, env: "  ")
    result = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path)
    assert result.unrepaired == [2]


def test_the_cache_answers_a_second_time_without_a_model_call(tmp_path, cache_dir, model_calls):
    pdf = _two_page_pdf(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path / "a")
    second = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path / "b")
    assert first.repaired == second.repaired == {2: GOOD}
    assert len(model_calls) == 1


def test_the_cache_is_keyed_by_model_and_prompt_version(tmp_path, cache_dir, model_calls, monkeypatch):
    pdf = _two_page_pdf(tmp_path)
    image = tmp_path / "page-2.png"
    page_repair.render_page(pdf, 2, tmp_path)
    assert page_repair.cache_path(image, "vlm-a") != page_repair.cache_path(image, "vlm-b")
    monkeypatch.setattr(page_repair, "PROMPT_VERSION", 2)
    assert page_repair.cache_path(image, "vlm-a").name.endswith("-2.md")


def test_an_empty_or_corrupt_cache_entry_is_ignored(tmp_path, cache_dir, model_calls):
    pdf = _two_page_pdf(tmp_path)
    image = page_repair.render_page(pdf, 2, tmp_path)
    cache_dir.mkdir(parents=True)
    page_repair.cache_path(image, "vlm").write_text("   \n", encoding="utf-8")
    result = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path)
    assert result.repaired == {2: GOOD} and len(model_calls) == 1


def _fake_render(pdf, page, out):
    """A page image without pdfium: the bytes only have to differ per page."""
    image = out / f"page-{page}.png"
    image.write_bytes(f"image of page {page}".encode())
    return image


# ── wired into the conversion ────────────────────────────────────────────────


def _no_client(monkeypatch):
    """A model client constructed here fails the test."""
    from artmind import llm_providers

    def boom(*a, **k):
        pytest.fail("a model client was constructed")

    monkeypatch.setattr(llm_providers, "_openrouter_client", boom)
    monkeypatch.setattr(llm_providers, "_ollama_client", boom)
    monkeypatch.setattr(page_repair, "transcribe", lambda *a, **k: pytest.fail("no transcription expected"))


@pytest.mark.parametrize("mode", ["detect", "off"])
def test_detect_and_off_never_construct_a_model_client(ingest_env, monkeypatch, mode):
    pdf = _pdf_source(ingest_env)
    doc = _docling_doc({1: "alpha beta gamma delta epsilon", 2: "zeta eta"})
    _stub_docling_json(monkeypatch, doc, "alpha beta gamma delta epsilon\n\nzeta eta\n")
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", mode)
    _no_client(monkeypatch)

    result = ing.ingest_file(pdf, "model", "general", chunk_size=6000)

    conversion = _sidecar()["conversion"]
    assert conversion["page_check"] == mode
    assert conversion.get("repaired", []) == [] and "model" not in conversion


def test_repair_puts_the_verified_page_in_the_body_and_records_it(ingest_env, monkeypatch, model_calls, cache_dir):
    pdf = _pdf_source(ingest_env)
    doc = _docling_doc({1: "alpha beta gamma delta epsilon", 2: "zeta eta"})
    _stub_docling_json(monkeypatch, doc, "alpha beta gamma delta epsilon\n\nzeta eta\n")
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "repair")
    monkeypatch.setenv("ARTMIND_IMAGE_MODEL", "vlm-test")
    monkeypatch.setattr(page_repair, "render_page", _fake_render)

    result = ing.ingest_file(pdf, "model", "general", chunk_size=6000)

    conversion = _sidecar()["conversion"]
    assert conversion["repaired"] == [2] and conversion["unrepaired"] == []
    assert conversion["model"] == "vlm-test" and conversion["prompt_version"] == page_repair.PROMPT_VERSION
    body = (ing.MARKDOWNS_DIR / "deck.md").read_text(encoding="utf-8")
    assert GOOD in body
    assert result["page_check"] == {"flagged": 1, "repaired": 1, "unrepaired": 0}


def test_a_rejected_page_stays_docling_and_is_listed_unrepaired(ingest_env, monkeypatch, cache_dir):
    pdf = _pdf_source(ingest_env)
    doc = _docling_doc({1: "alpha beta gamma delta epsilon", 2: "zeta eta"})
    _stub_docling_json(monkeypatch, doc, "alpha beta gamma delta epsilon\n\nzeta eta\n")
    monkeypatch.setenv("ARTMIND_PAGE_REPAIR", "repair")
    monkeypatch.setattr(page_repair, "render_page", _fake_render)
    monkeypatch.setattr(page_repair, "transcribe", lambda image, model, env: "zeta")

    ing.ingest_file(pdf, "model", "general", chunk_size=6000)

    conversion = _sidecar()["conversion"]
    assert conversion["repaired"] == [] and conversion["unrepaired"] == [2]
    body = (ing.MARKDOWNS_DIR / "deck.md").read_text(encoding="utf-8")
    assert "zeta eta" in body and GOOD not in body


def test_a_number_inside_a_longer_number_does_not_count():
    """`1` is not present just because `10` is."""
    cov, reasons = page_repair.verify("zeta eta theta iota 10", "zeta eta theta iota 1")
    assert reasons == ["number absent: 1"]


def test_thousands_separators_and_a_trailing_full_stop_do_not_matter():
    cov, reasons = page_repair.verify("zeta eta theta iota 1200", "zeta eta theta iota 1,200.")
    assert reasons == []


def test_transcription_goes_through_the_shared_image_model_call(tmp_path, monkeypatch):
    from artmind import extraction

    seen = {}

    def describe(image, model, prompt, timeout, env):
        seen.update(image=image.name, model=model, prompt=prompt, env=env)
        return "page text"

    monkeypatch.setattr(extraction, "describe_image", describe)
    monkeypatch.setattr(page_repair, "log_llm_call", lambda *a: None)
    image = tmp_path / "page-3.png"
    image.write_bytes(b"png")

    assert page_repair.transcribe(image, "vlm", {"ARTMIND_KG_LLM_PROVIDER": "ollama"}) == "page text"
    assert seen == {"image": "page-3.png", "model": "vlm", "prompt": page_repair.TRANSCRIBE_PROMPT,
                    "env": {"ARTMIND_KG_LLM_PROVIDER": "ollama"}}


def test_a_failed_model_call_leaves_the_page_unrepaired(tmp_path, cache_dir, monkeypatch):
    from artmind import extraction

    def describe(*a, **k):
        raise TimeoutError("model timed out")

    monkeypatch.setattr(extraction, "describe_image", describe)
    pdf = _two_page_pdf(tmp_path)

    result = page_repair.repair_pages(pdf, [2], {2: SOURCE_PAGE_2}, "vlm", {}, tmp_path)

    assert result.repaired == {} and result.unrepaired == [2]
