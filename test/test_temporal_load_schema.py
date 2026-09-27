"""temporal.load_schema(domain, schemas_dir=...): `vault sync` reads schemas
committed at HEAD from a scratch copy, not DOMAIN_SCHEMAS_DIR (phase 1
review finding 1, spec 2026-09-26 §14 A1)."""
from artmind.temporal import load_schema


def test_load_schema_reads_from_the_given_directory(tmp_path):
    (tmp_path / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")

    assert load_schema("banking", schemas_dir=tmp_path) == {"entity_types": {"ACCOUNT": {"kind": "recurrent"}}}


def test_load_schema_merges_the_parent_temporal_block_from_the_same_directory(tmp_path):
    (tmp_path / "banking_schema.yaml").write_text("temporal:\n  defaults: {supersede_on_title_family: true}\n")
    (tmp_path / "banking.policy_schema.yaml").write_text("entity_types: {}\n")

    schema = load_schema("banking.policy", schemas_dir=tmp_path)

    assert schema["temporal"]["defaults"] == {"supersede_on_title_family": True}


def test_load_schema_with_a_missing_file_in_the_given_directory_is_empty(tmp_path):
    assert load_schema("banking", schemas_dir=tmp_path) == {}
