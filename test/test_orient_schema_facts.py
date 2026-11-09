"""Facts orient reads from schema files only (spec 2026-11-09 §3, §4.1)."""
import pytest

from artmind.orient import schema_facts

SCHEMA = """
name: journal
description: Daily notes written in the first person. They record events, people and plans. A third sentence that is long enough to push the whole description past the two-hundred character limit set for the purpose field.
temporal:
  document:
    valid_from: [Date, date]
  document_patterns:
    valid_from:
      - filename: '^(\\d{4}-\\d{2}-\\d{2})$'
entity_types:
  EVENT:
    kind: occurrent
    description: Any significant occurrence.   Not routine background actions.
    relates_to:
      PLACE: [occurs_at]   # in the schema, but orient does not read it (spec §4.1)
  PERSON:
    kind: continuant
    description: A person.
"""

POLICY = """
name: bank.policy
description: Formal policy documents.
temporal:
  document:
    valid_from: [Effective Date, effective_date]
    version: [Version]
entity_types: {}
"""


@pytest.fixture
def schemas(tmp_path, monkeypatch):
    from artmind import temporal

    (tmp_path / "journal_schema.yaml").write_text(SCHEMA)
    (tmp_path / "bank.policy_schema.yaml").write_text(POLICY)
    monkeypatch.setattr(temporal, "DOMAIN_SCHEMAS_DIR", tmp_path)
    return tmp_path


def test_purpose_is_cut_at_a_sentence_under_the_limit(schemas):
    facts = schema_facts.read("journal")
    assert facts["purpose"] == "Daily notes written in the first person. They record events, people and plans."
    assert len(facts["purpose"]) <= schema_facts.PURPOSE_MAX


def test_dating_rule_names_fields_and_filename_pattern(schemas):
    j = schema_facts.read("journal")
    assert j["time_rule"] == {"valid_from": "document field 'Date', or filename ^(\\d{4}-\\d{2}-\\d{2})$"}
    assert j["filename_pattern"] == "^(\\d{4}-\\d{2}-\\d{2})$"
    p = schema_facts.read("bank.policy")
    assert p["time_rule"] == {"valid_from": "document field 'Effective Date'", "version": "document field 'Version'"}
    assert p["filename_pattern"] is None


def test_classes_carry_kind_and_whole_definition_only(schemas):
    classes = schema_facts.read("journal")["classes"]
    assert classes["EVENT"] == {"kind": "occurrent",
                                "definition": "Any significant occurrence. Not routine background actions."}
    assert classes["PERSON"] == {"kind": "continuant", "definition": "A person."}


def test_missing_schema_is_none_and_broken_schema_raises(schemas):
    assert schema_facts.read("nope") is None
    (schemas / "bad_schema.yaml").write_text("name: [x\n")
    with pytest.raises(schema_facts.SchemaFactsError):
        schema_facts.read("bad")
