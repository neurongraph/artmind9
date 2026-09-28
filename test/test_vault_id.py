"""`vault_id` in `.artmind/vault.yaml` (spec 2026-09-26 §6 A2): minted once by
`artmind init`, committed, and added to an existing vault without disturbing
the rest of the file."""
import uuid

import pytest
from click.testing import CliRunner

from artmind import manifest
from artmind.vault import VaultLayout


def _vault_yaml(root):
    return VaultLayout(root).vault_yaml


def test_scaffold_mints_a_quoted_uuid(tmp_path):
    from artmind.setup import scaffold_vault

    summary = scaffold_vault(tmp_path)

    vault_id = manifest.read_vault_id(tmp_path)
    assert str(uuid.UUID(vault_id)) == vault_id
    assert summary["vault_id"] == vault_id
    assert summary["vault_id_minted"] is True
    assert f'vault_id: "{vault_id}"' in _vault_yaml(tmp_path).read_text()


def test_rescaffolding_keeps_the_id_and_the_file_byte_identical(tmp_path):
    from artmind.setup import scaffold_vault

    first = scaffold_vault(tmp_path)
    before = _vault_yaml(tmp_path).read_bytes()

    second = scaffold_vault(tmp_path)

    assert second["vault_id"] == first["vault_id"]
    assert second["vault_id_minted"] is False
    assert _vault_yaml(tmp_path).read_bytes() == before


def test_an_existing_vault_gets_an_id_appended_and_nothing_else_changes(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    original = (
        "# my notes about this vault\n"
        "ingest:\n"
        "  trigger: manual\n"
        "  mappings:\n"
        "    - path: notes/**   # keep this comment\n"
        "      domain: general\n"
    )
    path.write_text(original)

    vault_id, minted = manifest.ensure_vault_id(tmp_path)

    assert minted is True
    text = path.read_text()
    assert text.startswith(original)
    assert text.count("vault_id:") == 1
    assert manifest.read_vault_id(tmp_path) == vault_id
    assert manifest.load(tmp_path).mappings[0].domain == "general"


def test_a_file_without_a_trailing_newline_is_not_glued_to_the_id(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("ingest:\n  trigger: manual")

    vault_id, _ = manifest.ensure_vault_id(tmp_path)

    assert path.read_text().startswith("ingest:\n  trigger: manual\n")
    assert manifest.load(tmp_path).trigger == "manual"
    assert manifest.read_vault_id(tmp_path) == vault_id


def test_an_empty_vault_id_is_refused_not_duplicated(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("vault_id:\n")

    with pytest.raises(manifest.ManifestError, match="present but empty"):
        manifest.ensure_vault_id(tmp_path)
    assert path.read_text() == "vault_id:\n"


@pytest.mark.parametrize(
    "trailer",
    [
        "...\n",
        "...",
        "...\n\n\n",
        "...  \n",
    ],
    ids=["marker-newline", "marker-no-newline", "marker-blank-lines", "marker-trailing-spaces"],
)
def test_a_trailing_document_end_marker_does_not_break_the_append(tmp_path, trailer):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    original_body = (
        "# my notes about this vault\n"
        "ingest:\n"
        "  trigger: manual\n"
        "  mappings:\n"
        "    - path: notes/**   # keep this comment\n"
        "      domain: general\n"
    )
    path.write_text(original_body + trailer)

    vault_id, minted = manifest.ensure_vault_id(tmp_path)

    assert minted is True
    text = path.read_text()
    # The resulting file must still be valid, loadable YAML.
    assert manifest.load(tmp_path).mappings[0].domain == "general"
    assert manifest.load(tmp_path).mappings[0].path == "notes/**"
    assert manifest.read_vault_id(tmp_path) == vault_id
    assert str(uuid.UUID(vault_id)) == vault_id
    # Original content's meaning is preserved even though the marker line
    # itself is removed (it only closed the previous YAML document).
    assert "# keep this comment" in text
    assert "notes/**" in text
    assert text.count("vault_id:") == 1


def test_read_vault_id_is_none_without_a_manifest_or_a_key(tmp_path):
    assert manifest.read_vault_id(tmp_path) is None
    _vault_yaml(tmp_path).parent.mkdir(parents=True)
    _vault_yaml(tmp_path).write_text("ingest:\n  trigger: manual\n")
    assert manifest.read_vault_id(tmp_path) is None


def test_init_prints_the_vault_id(tmp_path, monkeypatch):
    from artmind.cli import cli

    monkeypatch.chdir(tmp_path)

    first = CliRunner().invoke(cli, ["init"])
    second = CliRunner().invoke(cli, ["init"])

    vault_id = manifest.read_vault_id(tmp_path)
    assert first.exit_code == 0, first.output
    assert f"Vault id: {vault_id} (new" in first.output
    assert f"Vault id: {vault_id}\n" in second.output
