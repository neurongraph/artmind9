"""`ingest sync --dryRun`: the files by tier and an LLM-call estimate, from reads alone."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
from _dry_run import assert_effect_shape
from artmind.cli import cli
from conftest import stage_as_extracted

MANIFEST = """\
ingest:
  mappings:
    - path: "notes/**"
      domain: general
    - path: "decks/**"
      domain: general
    - path: "tables/**"
      domain: banking
"""


def _fake_docling(dest_path, image_model, **kwargs):
    return f"# {dest_path.stem}\n\n{dest_path.read_bytes().decode()}\n", {}


@pytest.fixture()
def pvault(ingest_env, tmp_path, monkeypatch):
    import paths

    vault, _ = ingest_env
    (vault / ".artmind" / "vault.yaml").write_text(MANIFEST)
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling)
    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "_get_available_domains", lambda: ["general", "banking"])
    return vault


def _write(vault: Path, rel: str, content) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content, encoding="utf-8")
    return path


def _ingest(path: Path, domain: str) -> None:
    from artmind.structured import is_structured_source
    from artmind.structured.pipeline import ingest_structured_file

    if is_structured_source(path):
        assert ingest_structured_file(path, domain)["status"] == "ok"
        return
    result = ing.ingest_file(path, "image-model", domain)
    assert result["status"] == "ok", result
    stage_as_extracted(ing.KG_DIR, result, domain)


LONG = "\n\n".join(f"## Section {i}\n\n" + ("Plain synthetic sentence about widgets. " * 60) for i in range(6))


def _build(vault: Path) -> None:
    _ingest(_write(vault, "notes/unchanged.md", "Same body.\n"), "general")
    _ingest(_write(vault, "notes/edited.md", "Body v1.\n"), "general")
    _ingest(_write(vault, "decks/same.pptx", b"deck bytes v1"), "general")
    _ingest(_write(vault, "tables/kept.csv", "id,name\n1,a\n"), "banking")
    edited = vault / "notes/edited.md"
    edited.write_text(edited.read_text().replace("Body v1.", "Body v2."))
    _write(vault, "notes/fresh.md", "A new note.\n")
    _write(vault, "notes/long.md", LONG)
    _write(vault, "decks/new.pdf", b"pdf bytes")
    _write(vault, "tables/new.csv", "id\n1\n")


def _dry(vault, *args):
    result = CliRunner().invoke(cli, ["ingest", "sync", str(vault), "--dryRun", "--compact", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _by_path(payload):
    return {p["path"]: p for p in payload["plan"]}


def test_reports_files_by_tier_and_estimates_llm_calls(pvault):
    _build(pvault)

    out = _dry(pvault)

    plan = _by_path(out)
    tiers = {path: p["tier"] for path, p in plan.items()}
    assert tiers["notes/unchanged.md"] == "unchanged" and tiers["decks/same.pptx"] == "unchanged"
    assert tiers["tables/kept.csv"] == "unchanged"
    for changed in ("notes/edited.md", "notes/fresh.md", "notes/long.md", "decks/new.pdf", "tables/new.csv"):
        assert tiers[changed] == "content", changed
    assert out["tiers"] == {"content": 5, "metadata": 0, "unchanged": 3}
    assert out["dry_run"] is True and "rows" not in out

    fresh, long = plan["notes/fresh.md"], plan["notes/long.md"]
    assert fresh["chunks"] == 1 and fresh["reason"] == "new" and not fresh["estimated"]
    assert fresh["llm_calls"] == 4, "three extraction steps + one chunk summary, no document summary"
    assert long["chunks"] > 1
    assert long["llm_calls"] == 3 * long["chunks"] + (long["chunks"] + 1) + 1
    assert plan["notes/edited.md"]["reason"] == "changed"
    assert plan["decks/new.pdf"]["estimated"] is True and plan["decks/new.pdf"]["kind"] == "binary"
    assert plan["tables/new.csv"]["kind"] == "table" and plan["tables/new.csv"]["llm_calls"] == 0

    effect = out["effect"]
    assert_effect_shape({**effect, "llm": {**effect["llm"], "calls": 0}})
    assert effect["llm"]["calls"] == sum(p["llm_calls"] for p in plan.values())
    kinds = {c["kind"]: c["count"] for c in effect["changes"]}
    assert kinds == {"document_ingest": 4, "table_change": 1}


def test_writes_and_converts_nothing(pvault, monkeypatch):
    _build(pvault)
    fresh = pvault / "notes/fresh.md"
    before = fresh.read_bytes()
    staging = sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*"))

    def boom(*a, **k):
        raise AssertionError("a dry run must not call this")

    monkeypatch.setattr(ing, "_convert_binary_via_docling", boom)
    monkeypatch.setattr(ing, "_llm_extract", boom)
    monkeypatch.setattr(ing, "_summary_llm", boom)
    monkeypatch.setattr(ing, "ingest_file", boom)
    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "ingest_file", boom)
    monkeypatch.setattr(cli_module, "ingest_to_kg", boom)
    monkeypatch.setattr(cli_module, "ingest_structured_file", boom)

    _dry(pvault)

    assert fresh.read_bytes() == before, "a new note is not stamped"
    assert sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*")) == staging
    assert not list(ing.MARKDOWNS_DIR.glob("new*")), "a new binary is not converted"


def test_content_tier_equals_the_files_the_real_sync_works_on(pvault, monkeypatch):
    """The contract: what the dry run lists as `content` is what the real sync extracts, converts or
    loads, and the chunk count it reports is the one the ingest splits the note into."""
    import artmind.cli as cli_module
    from artmind.structured.pipeline import ingest_structured_file

    _build(pvault)
    out = _dry(pvault)
    listed = {p["path"] for p in out["plan"] if p["tier"] == "content"}

    worked: dict[str, int] = {}
    real_ingest_file = cli_module.ingest_file

    def _recording(path, *args, **kwargs):
        result = real_ingest_file(path, *args, **kwargs)
        if result.get("status") == "ok" and ing.kg_work_was_done(result):
            worked[Path(path).resolve().relative_to(pvault).as_posix()] = result.get("chunk_count") or -1
        return result

    def _staging(result, domain, *a, **k):
        if ing.kg_work_was_done(result):
            stage_as_extracted(ing.KG_DIR, result, domain)
        return True

    def _structured(path, domain, **kwargs):
        result = ingest_structured_file(path, domain, **kwargs)
        if result["status"] == "ok":
            worked[Path(path).resolve().relative_to(pvault).as_posix()] = 0
        return result

    monkeypatch.setattr(cli_module, "ingest_file", _recording)
    monkeypatch.setattr(cli_module, "ingest_to_kg", _staging)
    monkeypatch.setattr(cli_module, "ingest_structured_file", _structured)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain=None, keys=None: {})

    result = CliRunner().invoke(cli, ["ingest", "sync", str(pvault), "--compact"])

    assert result.exit_code == 0, result.output
    real = json.loads(result.stdout)
    assert set(worked) == listed
    assert real["effect"] == out["effect"], "one effect shape serves the plan and the run"
    plan = _by_path(out)
    for path in ("notes/long.md", "notes/fresh.md", "notes/edited.md"):
        assert plan[path]["chunks"] == worked[path], path


def test_a_single_named_file_with_a_domain_is_planned_without_a_prompt(pvault):
    _write(pvault, "notes/plain.md", "A brand new note.\n")
    out = _dry(pvault / "notes/plain.md", "--domain", "general")
    assert out["plan"][0]["tier"] == "content" and out["plan"][0]["reason"] == "new"


def test_reextract_lists_unchanged_notes_as_content(pvault):
    _ingest(_write(pvault, "notes/unchanged.md", "Same body.\n"), "general")

    plain = _by_path(_dry(pvault))["notes/unchanged.md"]
    redone = _by_path(_dry(pvault, "--reextract"))["notes/unchanged.md"]

    assert plain["tier"] == "unchanged"
    assert redone["tier"] == "content" and redone["reason"] == "reextract"


def test_a_note_the_ingest_refuses_is_an_error_not_a_crash(pvault):
    _write(pvault, "notes/bad.md", "---\n_domain: ../escape\n---\n\nBody.\n")
    out = _dry(pvault)
    assert [e["path"] for e in out["errors"]] == ["notes/bad.md"]
    assert "not a plain folder name" in out["errors"][0]["error"]


def test_a_chunks_only_domain_estimates_summaries_only(pvault, monkeypatch):
    from artmind import ingest_plan

    assert ingest_plan.llm_calls(1, schema={"graph_extraction": False}) == 1
    assert ingest_plan.llm_calls(4, schema={"graph_extraction": False}) == 5
    assert ingest_plan.llm_calls(4, schema={"document_context": False}) == 12 + 5
    assert ingest_plan.llm_calls(4, schema={}) == 12 + 5 + 1
