"""Every shipped schema declares `identity` on every class (spec 2026-11-15 §5.3, plan Q3)."""
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

ROOT = Path(__file__).resolve().parent.parent / "artmind"
SCHEMAS = ROOT / "domains" / "schemas"
PATHS = sorted(SCHEMAS.glob("*_schema.yaml")) + sorted(
    (ROOT / "skills" / "artmind-create-schema" / "assets").glob("*_schema.yaml"))
JOURNAL = {"PERSON": "name", "LOCATION": "name", "ORGANIZATION": "name", "IDEA": "name", "OBJECT": "name",
           "EVENT": "occasion", "ACTIVITY": "occasion", "STATE_CHANGE": "occasion", "PLAN": "occasion",
           "EMOTION": "category"}
BANKING_OCCASIONS = {"TRANSACTION", "SAR", "INCIDENT_EVENT", "GOVERNANCE_DECISION", "AUDIT_FINDING",
                     "IMPACT_ASSESSMENT", "RETENTION_DECISION", "REGULATORY_UPDATE"}


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", PATHS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_class_declares_identity(path):
    types = _load(path).get("entity_types") or {}
    assert [c for c, d in types.items() if "identity" not in (d or {})] == []


@pytest.mark.parametrize("path", [SCHEMAS / "personal_journal_schema.yaml",
                                  ROOT / "skills" / "artmind-create-schema" / "assets" / "personal_journal_schema.yaml"],
                         ids=["shipped", "skill-asset"])
def test_journal_identities(path):
    schema = _load(path)
    types = schema["entity_types"]
    assert {c: sv.class_identity(schema, c) for c in types} == {c: JOURNAL[c] for c in types}
    assert types["ACTIVITY"]["kind"] == "occurrent" and types["PLAN"]["kind"] == "occurrent"
    assert "recurring or single" not in types["ACTIVITY"]["description"]
    assert types["PLAN"]["properties"]["timeframe"]["temporal"] == "valid_from"


def test_banking_identities():
    for path in sorted(SCHEMAS.glob("banking*_schema.yaml")):
        schema = _load(path)
        for cls in schema["entity_types"]:
            expected = "occasion" if cls in BANKING_OCCASIONS else "name"
            assert sv.class_identity(schema, cls) == expected, (path.stem, cls)


def test_contracts_amendment_is_a_name():
    schema = _load(SCHEMAS / "contracts_schema.yaml")
    assert {sv.class_identity(schema, c) for c in schema["entity_types"]} == {"name"}


@pytest.mark.parametrize("path", PATHS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_no_identity_warning_or_error_in_shipped_schemas(path):
    schema = _load(path)
    assert sv.identity_warnings(schema, path.stem) == []
    assert [e for e in sv.validate_schema(schema, sv.load_meta(), path.stem) if "identity" in e] == []
