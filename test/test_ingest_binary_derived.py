"""Integration-level tests for artmind.ingest._ingest_binary_derived:
converting a binary source (pdf/pptx/docx) to markdown and storing it
permanently at `.artmind`'s own markdown store (docs/vault.md, "The
ownership rule"), against a real (temp) vault + git repo + registry, with
docling itself stubbed out (`_convert_binary_via_docling` is monkeypatched to
return a controllable body instead of shelling out to a real conversion).

Covers the one decision left after promotion was deleted -- no_op vs
convert, driven by comparing the incoming binary's hash against the
`source_sha256` its last extraction staged in document.json (spec 2026-09-26
R6; the converted markdown's own `_source_sha256` frontmatter is the legacy
fallback) -- for both a source copied in from outside the vault and a
vault-resident source.
"""
import json
import subprocess

import pytest

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted


def test_first_ingest_converts_and_mints_an_artmind_id(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["version"] == 1
    assert "artmind_id" in result
    derived_path = ing.MARKDOWNS_DIR / "deck.md"
    assert derived_path.exists()
    meta, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert meta == {"_artmind_id": result["artmind_id"], "_domain": "general"}
    assert result["provenance"]["source_type"] == "pptx"
    assert result["provenance"]["source_sha256"] == ing._compute_sha256(source)
    assert body == "# Deck\n\nBody v1.\n"

    # artmind never commits (spec 2026-09-26 D1): the derived markdown is left
    # for Obsidian Git to commit, so history still holds only the fixture's seed.
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" not in log
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert status.strip(), "the derived markdown is on disk, uncommitted"


def test_reingest_unchanged_binary_is_a_no_op(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    # Re-ingest the identical binary bytes -- docling must not even be called.
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )
    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["tier"] == "no_op"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert "chunks_dir" not in r2


def test_binary_changed_reconverts_and_bumps_version(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    source.write_bytes(b"fake binary v2 -- different bytes")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v2.\n"]))
    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"] + 1
    derived_path = ing.MARKDOWNS_DIR / "deck.md"
    _, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert body == "# Deck\n\nBody v2.\n"


def test_the_converted_markdown_stays_in_artmind(ingest_env, monkeypatch):
    """No `_derived/` in the vault: `.artmind/` owns the conversion, so it has
    one location for its whole life and nothing ever moves."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert not (vault / "_derived").exists()
    assert (ing.MARKDOWNS_DIR / "deck.md").is_file()


def test_a_vault_resident_binary_no_ops_on_an_unchanged_reingest(ingest_env, monkeypatch):
    """This is exactly the correctness gap `_source_sha256` was introduced to
    close: Task 5 left a vault-resident binary unable to tell if it changed
    (no separate persisted copy to diff bytes against), so it always
    reconverted, even byte-for-byte unchanged. Comparing the incoming
    binary's own hash against `_source_sha256` in the registered markdown's
    frontmatter fixes that regardless of where the source lives.
    """
    vault, _source = ingest_env
    resident = vault / "area1" / "deck.pptx"
    resident.parent.mkdir(parents=True)
    resident.write_bytes(b"fake binary v1")

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)
    assert r1["status"] == "ok"
    stage_as_extracted(ing.KG_DIR, r1)

    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )
    r2 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["tier"] == "no_op"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert "chunks_dir" not in r2


def test_an_unextracted_conversion_is_not_a_no_op(ingest_env, monkeypatch):
    """The source hash is recorded by the extraction (document.json), not at
    conversion: a conversion whose extraction never ran converts again
    rather than no-op forever on a markdown nothing was extracted from."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"] * 2))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails
    again = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert again.get("tier") != "no_op"
    assert "chunks_dir" in again, "converted and chunked again, ready to extract"
    assert again["version"] == 1


def test_a_legacy_conversion_no_ops_from_its_own_frontmatter(ingest_env, monkeypatch):
    """Backward compatibility: a conversion stamped by pre-R6 artmind (fat
    frontmatter, a document.json without `source_sha256`) still no-ops."""
    vault, source = ingest_env
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 3\n_domain: general\n_source_sha256: "
        + ing._compute_sha256(source) + "\n---\n\n# Deck\n"
    )
    folder = ing.KG_DIR / "general" / "deck"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "0192legacy", "version": 3}))
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert (result["tier"], result["version"], result["artmind_id"]) == ("no_op", 3, "0192legacy")


# ── --reextract on an unchanged binary (study item 2) ──────────────────────────
#
# A binary whose source bytes did not change used to return `no_op`, so the
# schema-changed re-extraction `--reextract` promises never happened for PDFs or
# decks. It now re-runs the LLM steps over the markdown already stored: no
# docling, no conversion, no write to the converted body, the source untouched.


def _no_docling(monkeypatch):
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling must not run on a re-extraction")),
    )


def _converted_once(ingest_env, monkeypatch, body="# Deck\n\nBody v1.\n"):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling([body]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    return r1


def test_reextract_of_an_unchanged_binary_reuses_the_markdown_and_runs_the_extraction(ingest_env, monkeypatch):
    vault, source = ingest_env
    r1 = _converted_once(ingest_env, monkeypatch)
    derived = ing.MARKDOWNS_DIR / "deck.md"
    body_before = derived.read_bytes()
    source_before = source.read_bytes()
    _no_docling(monkeypatch)

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert r2["status"] == "ok"
    assert r2["tier"] == "content", "an unchanged binary is re-extracted, not a no_op"
    assert r2["reextract"] is True
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"], "an unchanged body does not bump the version"
    assert derived.read_bytes() == body_before, "the converted markdown is not rewritten"
    assert source.read_bytes() == source_before, "the source is never edited or moved"
    assert int(r2["chunk_count"]) >= 1
    assert r2["chunks_dir"] == str(ing.MARKDOWNS_DIR / "deck_chunks")


def test_reextract_reaches_extract_kg_with_the_flag_and_the_same_document(ingest_env, monkeypatch):
    """extract_kg does the replacement (cache set aside, rows reset, graph
    contribution replaced on commit) -- the binary path must reach it with the
    same `reextract` flag a note does, the same identity, and the hash of the
    markdown it will read."""
    vault, source = ingest_env
    r1 = _converted_once(ingest_env, monkeypatch)
    _no_docling(monkeypatch)
    seen = {}

    def spy(result, domain, *a, **k):
        seen.update(result)
        return None  # stop before the graph; the wiring is what is under test

    monkeypatch.setattr(ing, "extract_kg", spy)
    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)
    assert ing.ingest_to_kg(result, "general", "m", "e", 6000) is False

    assert seen["reextract"] is True and seen["artmind_id"] == r1["artmind_id"]
    assert seen["sha256"] == ing._compute_sha256(ing.MARKDOWNS_DIR / "deck.md")
    assert seen["provenance"]["source_sha256"] == ing._compute_sha256(source)
    assert seen["provenance"]["source_type"] == "pptx"


def test_reextract_of_a_binary_whose_markdown_is_missing_converts_it_as_usual(ingest_env, monkeypatch):
    """Nothing to re-extract from: the stored markdown is gone, so the binary
    converts (docling runs) and the ordinary path takes over."""
    vault, source = ingest_env
    r1 = _converted_once(ingest_env, monkeypatch)
    (ing.MARKDOWNS_DIR / "deck.md").unlink()
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert r2["status"] == "ok" and r2.get("tier") != "no_op"
    assert "chunks_dir" in r2
    assert (ing.MARKDOWNS_DIR / "deck.md").exists()


def test_reextract_of_a_binary_never_extracted_is_an_ordinary_first_ingest(ingest_env, monkeypatch):
    """A binary converted but never extracted has no extraction to replace (the
    same case as `test_an_unextracted_conversion_is_not_a_no_op`): it converts
    again as usual, with or without --reextract."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"] * 2))
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)  # no stage_as_extracted

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert r2.get("tier") != "no_op"
    assert "chunks_dir" in r2 and r2["version"] == 1


def test_reextract_is_not_reported_as_ignored_for_a_binary(ingest_env, monkeypatch):
    vault, source = ingest_env
    _converted_once(ingest_env, monkeypatch)
    _no_docling(monkeypatch)
    import loguru

    messages = []
    handle = loguru.logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)
    finally:
        loguru.logger.remove(handle)
    assert not any("applies to vault notes only" in m for m in messages)


def test_reextract_of_a_binary_whose_markdown_was_edited_converts_it_again(ingest_env, monkeypatch):
    """The stored markdown is not the body the last extraction recorded: re-extracting
    it as-is would bump the version without registering the body. The unchanged source
    is converted again instead; its body matches the baseline, so the version stays."""
    vault, source = ingest_env
    r1 = _converted_once(ingest_env, monkeypatch)
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(derived.read_text().replace("Body v1.", "Body v1, edited."))
    conversions = []

    def convert(dest_path, image_model, **kwargs):
        conversions.append(dest_path)
        return "# Deck\n\nBody v1.\n", {}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", convert)

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert len(conversions) == 1, "the source is converted again"
    assert r2["status"] == "ok" and r2.get("tier") != "no_op"
    assert r2["reextract"] is True, "still a re-extraction, so extract_kg resets its cache"
    assert r2["version"] == r1["version"]
    assert "edited" not in derived.read_text()
    assert r2["provenance"]["content_sha256"] == r1["provenance"]["content_sha256"]


def test_reextract_of_an_edited_markdown_never_bumps_the_version_without_registering(ingest_env, monkeypatch):
    """A conversion that does change the body bumps the version AND registers it."""
    from artmind.db import _registry_row_by_artmind_id

    vault, source = ingest_env
    r1 = _converted_once(ingest_env, monkeypatch)
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(derived.read_text().replace("Body v1.", "Body v1, edited."))
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v2.\n"]))

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert r2["version"] == r1["version"] + 1
    row = _registry_row_by_artmind_id(r2["artmind_id"])
    assert row["content_sha256"] == r2["provenance"]["content_sha256"]


def test_docling_image_links_are_made_relative_to_the_markdown(tmp_path):
    """An absolute link would commit the home directory with the vault and send
    it to the LLM in every chunk that holds the image."""
    base = tmp_path / "markdowns"
    md = (
        f"Intro.\n\n![Image]({base}/Deck_artifacts/image_000000_ab.png)\n\n"
        f"![Image](<{base.resolve()}/Deck (1)_artifacts/image_000001_cd.png>)\n\n"
        "![Logo](https://example.com/logo.png)\n"
    )

    out = ing._relativise_image_links(md, base)

    assert str(tmp_path) not in out
    assert "![Image](Deck_artifacts/image_000000_ab.png)" in out
    assert "![Image](<Deck (1)_artifacts/image_000001_cd.png>)" in out
    assert "![Logo](https://example.com/logo.png)" in out


# ── Word comments and re-conversion of a .docx ─────────────────────────────────


def _fake_docling_run(markdowns, *, md, doc=None, image=None, calls=None):
    """A `run_command` stand-in for the docling CLI: writes `md` (and, when the
    command asks for JSON, `doc` saved as docling JSON) into `markdowns`, plus
    `image` as an exported picture."""
    def run(cmd, cwd=None):
        if calls is not None:
            calls.append(cmd)
        stem = cmd.rsplit("/", 1)[-1].rstrip('"').rsplit(".", 1)[0]
        (markdowns / f"{stem}.md").write_text(md, encoding="utf-8")
        if "--to json" in cmd and doc is not None:
            doc.save_as_json(markdowns / f"{stem}.json")
        if image is not None:
            artifacts = markdowns / f"{stem}_artifacts"
            artifacts.mkdir(exist_ok=True)
            (artifacts / image).write_bytes(b"png")
        return 0, "", ""
    return run


def _commented_doc():
    from docling_core.types.doc.document import ContentLayer, DoclingDocument, FineRef
    from docling_core.types.doc.labels import DocItemLabel, GroupLabel

    doc = DoclingDocument(name="report")
    para = doc.add_text(label=DocItemLabel.TEXT, text="The budget is 10k.")
    doc.add_text(label=DocItemLabel.TEXT, text="Next.")
    group = doc.add_group(label=GroupLabel.COMMENT_SECTION, name="c", content_layer=ContentLayer.NOTES)
    doc.add_comment(text="[author: B]: Should be 12k.", parent=group)
    para.comments.append(FineRef(cref=group.self_ref))
    return doc


def test_a_docx_conversion_writes_its_comments_inline_and_removes_the_json(ingest_env, monkeypatch):
    pytest.importorskip("docling_core")
    calls = []
    monkeypatch.setattr(ing, "run_command", _fake_docling_run(
        ing.MARKDOWNS_DIR, md="The budget is 10k.\n\nNext.", doc=_commented_doc(), calls=calls,
    ))

    body, err = ing._convert_binary_via_docling(ing.MARKDOWNS_DIR.parent / "report.docx", "m")

    assert err == {}
    assert "--to md --to json" in calls[0]
    assert body == "The budget is 10k.\n\n> [!comment]\n> [author: B]: Should be 12k.\n\nNext."
    assert (ing.MARKDOWNS_DIR / "report.md").read_text() == body
    assert not (ing.MARKDOWNS_DIR / "report.json").exists()


def test_a_docx_whose_json_cannot_be_read_keeps_doclings_markdown(ingest_env, monkeypatch):
    def run(cmd, cwd=None):
        (ing.MARKDOWNS_DIR / "report.md").write_text("Plain.", encoding="utf-8")
        (ing.MARKDOWNS_DIR / "report.json").write_text("{not json", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(ing, "run_command", run)

    body, err = ing._convert_binary_via_docling(ing.MARKDOWNS_DIR.parent / "report.docx", "m")

    assert (body, err) == ("Plain.", {})
    assert not (ing.MARKDOWNS_DIR / "report.json").exists()


def test_a_pdf_conversion_asks_docling_for_markdown_only(ingest_env, monkeypatch):
    calls = []
    monkeypatch.setattr(ing, "run_command", _fake_docling_run(ing.MARKDOWNS_DIR, md="Text.", calls=calls))

    body, _ = ing._convert_binary_via_docling(ing.MARKDOWNS_DIR.parent / "paper.pdf", "m")

    assert body == "Text."
    assert "--to json" not in calls[0]


def test_image_descriptions_are_reused_only_when_asked(ingest_env, monkeypatch):
    image = "image_000000_ab.png"
    md = f"Intro.\n\n![Image]({ing.MARKDOWNS_DIR}/paper_artifacts/{image})"
    monkeypatch.setattr(ing, "run_command", _fake_docling_run(ing.MARKDOWNS_DIR, md=md, image=image))
    described = []

    def describe(path, model):
        described.append(path.name)
        return "A red square."

    monkeypatch.setattr(ing, "_describe_image", describe)

    first, _ = ing._convert_binary_via_docling(ing.MARKDOWNS_DIR.parent / "paper.pdf", "m")
    (ing.MARKDOWNS_DIR / "paper_artifacts" / f"{image}_desc.md").write_text("A stored description.")
    reused, _ = ing._convert_binary_via_docling(
        ing.MARKDOWNS_DIR.parent / "paper.pdf", "m", reuse_image_descriptions=True,
    )
    fresh, _ = ing._convert_binary_via_docling(ing.MARKDOWNS_DIR.parent / "paper.pdf", "m")

    assert described == [image, image], "no model call when the stored description is reused"
    assert first == fresh == "Intro.\n\nA red square."
    assert reused == "Intro.\n\nA stored description."


def _docx_source(ingest_env):
    vault, deck = ingest_env
    source = deck.with_name("report.docx")
    source.write_bytes(b"fake docx v1")
    return source


def test_reextract_of_an_unchanged_docx_converts_it_again_reusing_image_descriptions(ingest_env, monkeypatch):
    """A .docx converted before its comments were written inline gains them on
    --reextract: it is converted again (no model call for its images), and the
    version bumps only because the body changed."""
    source = _docx_source(ingest_env)
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["Body.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    seen = []

    def convert(dest_path, image_model, **kwargs):
        seen.append(kwargs)
        return "Body.\n\n> [!comment]\n> Noted.\n", {}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", convert)

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert seen == [{"reuse_image_descriptions": True}]
    assert r2["status"] == "ok" and r2["reextract"] is True
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"] + 1
    assert "> [!comment]" in (ing.MARKDOWNS_DIR / "report.md").read_text()


def test_reextract_of_an_unchanged_docx_with_the_same_body_keeps_its_version(ingest_env, monkeypatch):
    source = _docx_source(ingest_env)
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["Body.\n", "Body.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000, reextract=True)

    assert r2["reextract"] is True and "chunks_dir" in r2
    assert r2["version"] == r1["version"]


def test_an_ordinary_conversion_does_not_reuse_image_descriptions(ingest_env, monkeypatch):
    source = _docx_source(ingest_env)
    seen = []

    def convert(dest_path, image_model, **kwargs):
        seen.append(kwargs)
        return "Body.\n", {}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", convert)

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert seen == [{"reuse_image_descriptions": False}]
