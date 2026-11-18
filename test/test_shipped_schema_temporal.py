"""Shipped schemas follow the temporal rules (spec 2026-11-15 §4)."""
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "artmind" / "domains" / "schemas"
ALL = [*sorted(SCHEMAS.glob("*_schema.yaml")),
       ROOT / "artmind" / "skills" / "artmind-create-schema" / "assets" / "personal_journal_schema.yaml"]
DEADLINES = [("banking.risk_governance", "ACTION_ITEM", "deadline"), ("banking.cases", "REMEDIATION_ACTION", "due_date"),
             ("project_governance", "ACTION", "due_date"), ("project_governance", "STATE_CHANGE", "due_date")]


def _schema(name):
    return yaml.safe_load((SCHEMAS / f"{name}_schema.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name, cls, prop", DEADLINES)
def test_deadlines_are_due_not_valid_from(name, cls, prop):
    assert _schema(name)["entity_types"][cls]["properties"][prop]["temporal"] == "due"


def test_a_state_change_is_dated_by_when_it_happened():
    props = _schema("project_governance")["entity_types"]["STATE_CHANGE"]["properties"]
    assert props["date_or_time"]["temporal"] == "valid_from"


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_no_temporal_warning_or_exclusive_flag_in_shipped_schemas(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert sv.temporal_warnings(schema, path.stem) == []
    assert "exclusive:" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("banking*_schema.yaml")), ids=lambda p: p.stem)
def test_last_updated_is_revised_not_effective(path):
    doc = (yaml.safe_load(path.read_text(encoding="utf-8")).get("temporal") or {}).get("document") or {}
    assert "Last Updated" not in (doc.get("valid_from") or [])


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("banking*_schema.yaml")), ids=lambda p: p.stem)
def test_banking_happenings_declare_their_date(path):
    types = yaml.safe_load(path.read_text(encoding="utf-8"))["entity_types"]
    assert types["TRANSACTION"]["properties"]["transaction_date"]["temporal"] == "valid_from"
    assert types["SAR"]["properties"]["filed_on"]["temporal"] == "valid_from"
