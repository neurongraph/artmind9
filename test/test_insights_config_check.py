import pytest

import paths
from _insights_helpers import FakeProjector
from artmind.insights import config_check

TABLES = {
    "entity_evidence": [
        {"_id": "e1", "name": "E1", "cls": "EMOTION", "type": "joy", "days": ["2024-01-01"], "docs": 1},
        {"_id": "p1", "name": "P1", "cls": "PLAN", "type": None, "days": ["2024-01-01"], "docs": 1},
    ],
    "documents": [{"id": "d", "valid_from": "2024-01-01", "time_source": "document_label"}],
    "property_stats": [{"cls": "PLAN", "property": "status", "filled": 1, "distinct_values": 1, "sample": ["open"]}],
    "entity_property": [{"_id": "p1", "value": "open"}],
}


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", tmp_path)
    monkeypatch.setattr(config_check, "load_schema", lambda d: {})
    return tmp_path


def test_valid_config_is_ok_with_warnings(cfg_dir):
    (cfg_dir / "demo.yaml").write_text(
        "outcome: {class: EMOTION, positive: [joy], negative: [sadness]}\n"
        "open_loops: {class: PLAN, open: [open], closed: [done]}\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is True and r["errors"] == []
    assert any("sadness" in w for w in r["warnings"]) and any("'done'" in w for w in r["warnings"])


def test_missing_classes_and_properties_are_errors(cfg_dir):
    (cfg_dir / "demo.yaml").write_text(
        "outcome: {class: MOOD, positive: [up], negative: [down]}\n"
        "open_loops: {class: PLAN, property: state, open: [open]}\n"
        "detectors: {communities: {classes: [TEAM]}}\nanchors: [Nobody]\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is False
    text = " ".join(r["errors"])
    for needle in ("MOOD", "PLAN.state", "TEAM", "Nobody"):
        assert needle in text


def test_invalid_config_is_reported_not_raised(cfg_dir):
    (cfg_dir / "demo.yaml").write_text("period: week\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is False and "period" in r["errors"][0]
