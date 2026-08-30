"""`ingest sync`/`ingest async` driven by the vault manifest (docs/vault.md).

These assert on WHICH FILES were offered to ingestion and WITH WHAT DOMAIN, by
recording the calls -- never on summary counts, which can report success for
work that never happened (CLAUDE.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.cli as cli_module
import paths
from artmind.cli import cli


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A vault with a manifest, and ingestion stubbed to record its calls.

    `cli.py` reads `paths.ARTMIND_VAULT_DIR` fresh (`from paths import
    ARTMIND_VAULT_DIR` inside the function) rather than caching it at module
    load, but that module attribute itself is resolved once, at `paths`'
    first import -- long before this test's `tmp_path` exists. A `chdir`
    alone can't make `resolve_vault()` re-run mid-process, so the attribute
    is patched directly, the same pattern other test modules use for the
    names they import it under (see test/test_document_identity.py etc.).
    """
    (tmp_path / ".artmind").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def recorded(monkeypatch):
    """Record (filename, domain) for every ingest_file call."""
    calls: list[tuple[str, str | None]] = []

    def fake_ingest_file(source, image_model, domain=None, **kwargs):
        calls.append((Path(source).name, domain))
        return {"status": "ok", "domain": domain, "touched_path": source}

    monkeypatch.setattr(cli_module, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(cli_module, "ingest_to_kg", lambda *a, **k: True)
    return calls


def _manifest(vault_root: Path, body: str) -> None:
    (vault_root / ".artmind" / "vault.yaml").write_text(body)


def test_only_mapped_paths_are_ingested(vault, recorded):
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("# a")
    (vault / "attachments").mkdir()
    (vault / "attachments" / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code == 0, result.output
    assert [name for name, _ in recorded] == ["a.md"]


def test_each_file_gets_the_domain_its_folder_maps_to(vault, recorded):
    _manifest(vault, """
ingest:
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
""")
    for folder, name in (("policies", "p.md"), ("notes", "n.md")):
        (vault / folder).mkdir()
        (vault / folder / name).write_text("# x")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code == 0, result.output
    assert dict(recorded) == {"p.md": "banking.policy", "n.md": "personal_journal"}


def test_an_explicit_domain_is_the_fallback_for_unmapped_files(vault, recorded):
    """--domain does not override a mapping; it covers what nothing maps."""
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")

    result = CliRunner().invoke(cli, ["ingest", "sync", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert dict(recorded) == {"n.md": "personal_journal"}


def test_a_vault_with_no_mappings_ingests_everything_as_before(vault, recorded):
    """Back-compat: configuring no mappings must not mean ingesting nothing."""
    _manifest(vault, "ingest:\n  trigger: manual\n  mappings: []\n")
    (vault / "a.md").write_text("# a")
    (vault / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "sync", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert sorted(name for name, _ in recorded) == ["a.md", "b.md"]


def test_naming_a_file_directly_bypasses_the_mapping_filter(vault, recorded):
    """An explicit request is honoured even from an unmapped folder -- the
    filter is for walks, not for overriding what the user just asked for."""
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "scratch").mkdir()
    (vault / "scratch" / "one.md").write_text("# one")

    result = CliRunner().invoke(
        cli, ["ingest", "sync", "scratch/one.md", "--domain", "general"]
    )

    assert result.exit_code == 0, result.output
    assert [name for name, _ in recorded] == ["one.md"]


def test_a_malformed_manifest_stops_the_run(vault, recorded):
    """Ingesting into wrong domains because a mapping was mistyped is worse
    than refusing to start."""
    _manifest(vault, "ingest:\n  mappings:\n    - path: notes/**\n")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code != 0
    assert "domain" in result.output
    assert recorded == [], "nothing may be ingested after a manifest error"


def test_async_also_skips_unmapped_paths(vault, monkeypatch):
    """`ingest async` walks the same directories; without the filter it queues
    exactly what the manifest exists to keep out."""
    queued: list[list[str]] = []

    def fake_create_job(batch_files, **kwargs):
        queued.append([Path(f).name for f in batch_files])
        return "job-1"

    monkeypatch.setattr(cli_module, "_create_job", fake_create_job)
    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)

    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: general
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("# a")
    (vault / "attachments").mkdir()
    (vault / "attachments" / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "async", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert queued == [["a.md"]]


def test_async_refuses_a_manifest_naming_an_unknown_domain(vault, monkeypatch):
    """sync refuses up front; async must too, rather than queueing a job that
    fails per file at extraction long after the command returned success."""
    monkeypatch.setattr(cli_module, "_create_job", lambda *a, **k: "job-1")
    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: no_such_domain_xyz
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")

    result = CliRunner().invoke(cli, ["ingest", "async", ".", "--domain", "general"])

    assert result.exit_code != 0
    assert "no_such_domain_xyz" in result.output
