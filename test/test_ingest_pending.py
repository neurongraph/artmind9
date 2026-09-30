"""`artmind ingest pending` (spec 2026-09-30 §6 X2): the mapped files an
ingest would do work on, decided by the ingest's own rules.

Real throwaway vault (`ingest_env`: a git repo, the registry, `.artmind/`
staging), real structured store, real identity resolution. Only docling and
the LLM extraction are stood in for: `_convert_binary_via_docling` returns a
body, and `ingest_to_kg` stages the `document.json` an extraction would
leave (`stage_as_extracted`) instead of calling a model."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
from artmind.ingest_pending import pending, pending_files
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


def _fake_docling(dest_path, image_model):
    """A conversion whose body follows the binary's bytes, like docling's."""
    return f"# {dest_path.stem}\n\n{dest_path.read_bytes().decode()}\n", {}


@pytest.fixture()
def pvault(ingest_env, tmp_path, monkeypatch):
    """`ingest_env`'s vault, mapped by a manifest, with the CLI's own vault
    constant and the structured store pointed into it."""
    import paths

    vault, _ = ingest_env
    (vault / ".artmind" / "vault.yaml").write_text(MANIFEST)
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling)
    return vault


def _write(vault: Path, rel: str, content) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _ingest(path: Path, domain: str) -> None:
    """One real ingest of `path`, extraction stood in for: what a previous
    `ingest sync`/`async` left behind."""
    from artmind.structured import is_structured_source
    from artmind.structured.pipeline import ingest_structured_file

    if is_structured_source(path):
        assert ingest_structured_file(path, domain)["status"] == "ok"
        return
    result = ing.ingest_file(path, "image-model", domain)
    assert result["status"] == "ok", result
    stage_as_extracted(ing.KG_DIR, result, domain)


def _build(vault: Path) -> None:
    """Every case at once: new, changed and unchanged of each kind, a moved
    note, a note the ingest would refuse, and files no ingest walks."""
    _ingest(_write(vault, "notes/unchanged.md", "Same body.\n"), "general")
    _ingest(_write(vault, "notes/edited.md", "Body v1.\n"), "general")
    _ingest(_write(vault, "notes/moving.md", "Moves, unchanged.\n"), "general")
    _ingest(_write(vault, "decks/same.pptx", b"deck bytes v1"), "general")
    _ingest(_write(vault, "decks/redone.pptx", b"deck bytes v1"), "general")
    _ingest(_write(vault, "tables/kept.csv", "id,name\n1,a\n"), "banking")
    _ingest(_write(vault, "tables/grown.csv", "id,name\n1,a\n"), "banking")

    edited = vault / "notes/edited.md"
    edited.write_text(edited.read_text().replace("Body v1.", "Body v2."))
    (vault / "notes/sub").mkdir()
    (vault / "notes/moving.md").rename(vault / "notes/sub/moved.md")
    _write(vault, "decks/redone.pptx", b"deck bytes v2")
    _write(vault, "tables/grown.csv", "id,name\n1,a\n2,b\n")

    _write(vault, "notes/fresh.md", "A new note.\n")
    _write(vault, "decks/new.pdf", b"pdf bytes")
    _write(vault, "tables/new.csv", "id\n1\n")
    _write(vault, "notes/bad_domain.md", "---\n_domain: ../escape\n---\n\nBody.\n")
    _write(vault, "drafts/unmapped.md", "Not mapped.\n")
    _write(vault, "notes/_Inbox/wip.md", "Drafting.\n")


def test_lists_new_and_changed_files_by_kind(pvault):
    _build(pvault)

    report = pending(pvault)

    assert report["notes"] == [
        {"path": "notes/edited.md", "reason": "changed"},
        {"path": "notes/fresh.md", "reason": "new"},
    ]
    assert report["binaries"] == [
        {"path": "decks/new.pdf", "reason": "new"},
        {"path": "decks/redone.pptx", "reason": "changed"},
    ]
    assert report["tables"] == [
        {"path": "tables/grown.csv", "reason": "changed"},
        {"path": "tables/new.csv", "reason": "new"},
    ]
    assert [e["path"] for e in report["errors"]] == ["notes/bad_domain.md"]
    assert "not a plain folder name" in report["errors"][0]["error"]


def test_writes_nothing(pvault):
    """Read-only: no note is stamped, nothing is registered or staged."""
    _build(pvault)
    fresh = pvault / "notes/fresh.md"
    before = fresh.read_bytes()
    staging = sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*"))

    pending(pvault)

    assert fresh.read_bytes() == before, "a new note is not stamped"
    assert sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*")) == staging
    assert not (ing.MARKDOWNS_DIR / "new.md").exists(), "a new binary is not converted"


def test_equals_what_a_following_ingest_does_work_on(pvault, monkeypatch):
    """The contract: what `pending` lists is exactly what the next ingest of
    the vault extracts, converts or loads -- and afterwards nothing is
    pending. Drives the real `ingest sync <vault>`, recording each file's
    outcome; `ingest_to_kg` stages the extraction it would have made."""
    import artmind.cli as cli
    from artmind.structured.pipeline import ingest_structured_file

    _build(pvault)
    listed = {e["path"] for kind in ("notes", "binaries", "tables") for e in pending(pvault)[kind]}

    worked: set[str] = set()
    real_ingest_file = cli.ingest_file

    def _recording_ingest_file(path, *args, **kwargs):
        result = real_ingest_file(path, *args, **kwargs)
        if result.get("status") == "ok" and ing.kg_work_was_done(result):
            worked.add(Path(path).resolve().relative_to(pvault).as_posix())
        return result

    def _staging_ingest_to_kg(result, domain, *args, **kwargs):
        if ing.kg_work_was_done(result):
            stage_as_extracted(ing.KG_DIR, result, domain)
        return True

    def _recording_structured(path, domain, **kwargs):
        result = ingest_structured_file(path, domain, **kwargs)
        if result["status"] == "ok":
            worked.add(Path(path).resolve().relative_to(pvault).as_posix())
        return result

    monkeypatch.setattr(cli, "ingest_file", _recording_ingest_file)
    monkeypatch.setattr(cli, "ingest_to_kg", _staging_ingest_to_kg)
    monkeypatch.setattr(cli, "ingest_structured_file", _recording_structured)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain=None, keys=None: {})

    result = CliRunner().invoke(cli.cli, ["ingest", "sync", str(pvault)])

    assert result.exit_code == 0, result.output
    assert worked == listed
    after = pending(pvault)
    assert (after["notes"], after["binaries"], after["tables"]) == ([], [], [])


def test_pending_files_are_absolute_and_in_walk_order(pvault):
    _build(pvault)

    files = pending_files(pvault)

    assert files == sorted(files)
    assert [f.relative_to(pvault).as_posix() for f in files] == [
        "decks/new.pdf", "decks/redone.pptx", "notes/edited.md", "notes/fresh.md",
        "tables/grown.csv", "tables/new.csv",
    ]


def test_a_manifest_without_mappings_considers_every_supported_file(pvault):
    """No mappings maps nothing and so filters nothing (`Manifest.should_ingest`);
    such a file takes the worker's default domain."""
    (pvault / ".artmind" / "vault.yaml").write_text("ingest:\n  mappings: []\n")
    _write(pvault, "anywhere/note.md", "Hello.\n")

    assert pending(pvault)["notes"] == [{"path": "anywhere/note.md", "reason": "new"}]


def test_cli_prints_the_report_as_json(pvault):
    from artmind.cli import cli

    _build(pvault)

    result = CliRunner().invoke(cli, ["ingest", "pending", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == pending(pvault)
    assert "\n" not in result.stdout.strip(), "--compact is one line"


def test_cli_refuses_outside_a_vault(monkeypatch):
    import paths
    from artmind.cli import cli

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)

    result = CliRunner().invoke(cli, ["ingest", "pending"])

    assert result.exit_code != 0
    assert "Not inside an artmind vault" in result.output


def test_cli_reports_a_broken_manifest(pvault):
    from artmind.cli import cli

    (pvault / ".artmind" / "vault.yaml").write_text("ingest: [not, a, mapping]\n")

    result = CliRunner().invoke(cli, ["ingest", "pending"])

    assert result.exit_code != 0
    assert "'ingest' must be a mapping" in result.output
