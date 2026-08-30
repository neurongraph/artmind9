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


def _manifest(*pairs) -> manifest.Manifest:
    return manifest.Manifest(
        mappings=[manifest.Mapping(path=p, domain=d) for p, d in pairs]
    )


def test_a_recursive_glob_covers_nested_paths():
    m = _manifest(("policies/**", "banking.policy"))

    assert m.domain_for("policies/policy_aml.md") == "banking.policy"
    assert m.domain_for("policies/sub/deep.md") == "banking.policy"


def test_an_unmapped_path_has_no_domain():
    m = _manifest(("policies/**", "banking.policy"))

    assert m.domain_for("attachments/photo.png") is None


def test_first_match_wins_so_a_specific_rule_can_precede_a_general_one():
    """The manifest reads top-down like a routing table."""
    m = _manifest(
        ("notes/archive/**", "general"),
        ("notes/**", "personal_journal"),
    )

    assert m.domain_for("notes/archive/old.md") == "general"
    assert m.domain_for("notes/today.md") == "personal_journal"


def test_an_unmapped_path_is_not_ingested():
    """This is the second job of the mapping: an attachments/ folder needs no
    separate ignore mechanism, it is simply not mapped."""
    m = _manifest(("notes/**", "personal_journal"))

    assert m.should_ingest("notes/a.md") is True
    assert m.should_ingest("attachments/photo.png") is False


def test_a_manifest_with_no_mappings_filters_nothing():
    """A vault that has not configured mappings must behave exactly as it did
    before this feature -- NOT suddenly ingest zero files."""
    empty = manifest.Manifest()

    assert empty.should_ingest("anything/at/all.md") is True
    assert empty.domain_for("anything/at/all.md") is None


def test_a_single_file_glob_matches_only_that_file():
    m = _manifest(("structured/customers.csv", "banking"))

    assert m.domain_for("structured/customers.csv") == "banking"
    assert m.domain_for("structured/agents.csv") is None
