"""The ingest manifest, `.artmind/vault.yaml` (docs/vault.md)."""
from __future__ import annotations

import pytest

from artmind import manifest


def _write(tmp_path, body: str):
    (tmp_path / ".artmind").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".artmind" / "vault.yaml").write_text(body)
    return tmp_path


def test_a_missing_manifest_is_not_an_error(tmp_path):
    """A vault predating this feature, or one mid-init, must still ingest."""
    loaded = manifest.load(tmp_path)

    assert loaded.mappings == []
    assert loaded.trigger == "manual"


def test_reads_trigger_and_mappings(tmp_path):
    _write(tmp_path, """
ingest:
  trigger: manual
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
""")

    loaded = manifest.load(tmp_path)

    assert loaded.trigger == "manual"
    assert [m.domain for m in loaded.mappings] == ["banking.policy", "personal_journal"]


def test_an_empty_manifest_parses(tmp_path):
    """`scaffold_vault` writes `mappings: []` — that must not crash."""
    _write(tmp_path, "ingest:\n  trigger: manual\n  mappings: []\n")

    loaded = manifest.load(tmp_path)

    assert loaded.mappings == []


def test_a_malformed_manifest_names_the_file(tmp_path):
    """The user hand-edits this; a parse error must say which file and why."""
    _write(tmp_path, "ingest: [this is not a mapping]\n")

    with pytest.raises(manifest.ManifestError, match="vault.yaml"):
        manifest.load(tmp_path)


def test_a_mapping_without_a_domain_is_refused(tmp_path):
    _write(tmp_path, "ingest:\n  mappings:\n    - path: notes/**\n")

    with pytest.raises(manifest.ManifestError, match="domain"):
        manifest.load(tmp_path)


def test_a_mapping_without_a_path_is_refused(tmp_path):
    _write(tmp_path, "ingest:\n  mappings:\n    - domain: general\n")

    with pytest.raises(manifest.ManifestError, match="path"):
        manifest.load(tmp_path)


def test_an_unknown_trigger_is_refused(tmp_path):
    """Silently treating a typo as `manual` would leave someone believing
    ingestion is automatic when it is not."""
    _write(tmp_path, "ingest:\n  trigger: whenever\n  mappings: []\n")

    with pytest.raises(manifest.ManifestError, match="trigger"):
        manifest.load(tmp_path)
