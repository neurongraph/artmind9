import pytest
import yaml

import paths
from artmind.mart import declaration as dec

GOOD = {
    "format": 1, "domain": "journal", "version": 1,
    "narrator": "from_domain_config", "home": {"city": "Hometown"},
    "facts": {"happening": ["EVENT"], "mention": ["EVENT", "ACTIVITY"]},
    "dimensions": {
        "time": {"calendar": True},
        "person": {"class": "PERSON", "seed": {"property": "relationship_to_narrator"}, "words": "kinship"},
        "place": {"class": "LOCATION", "seed": {"property": "location", "edges": ["LOCATED_IN"]}},
        "type": {"classes": ["EVENT", "ACTIVITY"], "property": "type",
                 "families": {"trip": ["visit", "outing"], "health": ["health_incident"], "ritual": ["milestone"]}},
    },
    "wholes": {
        "trip": {"from": "happening", "where": {"type_family": ["trip"], "place": "away"},
                 "join": {"consecutive_days": 1, "share": ["participant", "place"]},
                 "alone_never_joins": ["narrator", "household"]},
        "life_event": {"from": "happening", "anchor": "person", "match": ["edge", "name", "participants"],
                       "where": {"type_family": ["health", "ritual"]}, "window": {"grows_while_gap_days": 21}},
    },
}
SCHEMA = {"entity_types": {c: {"kind": "occurrent", "properties": {"type": {}}} for c in ("EVENT", "ACTIVITY")}
          | {"PERSON": {"kind": "recurrent", "properties": {"relationship_to_narrator": {}}},
             "LOCATION": {"kind": "recurrent", "properties": {"location": {}}}}}


def _write(tmp_path, monkeypatch, data, name="journal"):
    monkeypatch.setattr(paths, "DOMAIN_MART_DIR", tmp_path)
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data))


def test_load_reads_every_part(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, GOOD)
    d = dec.load_declaration("journal")
    assert d.version == 1 and d.home_city == "Hometown"
    assert d.happening_classes == ["EVENT"] and d.mention_classes == ["EVENT", "ACTIVITY"]
    assert d.person.entity_class == "PERSON" and d.person.seed_property == "relationship_to_narrator"
    assert d.place.seed_edges == ["LOCATED_IN"]
    assert d.type.family_of("outing") == "trip" and d.type.family_of("unknown") is None
    assert set(d.wholes) == {"trip", "life_event"}
    assert d.wholes["life_event"].window_gap_days == 21


def test_missing_declaration_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_MART_DIR", tmp_path)
    assert dec.load_declaration("nothing") is None


def test_validate_flags_unknown_classes_and_families(tmp_path, monkeypatch):
    bad = yaml.safe_load(yaml.safe_dump(GOOD))
    bad["facts"]["happening"] = ["EVENT", "NOPE"]
    bad["wholes"]["trip"]["where"]["type_family"] = ["missing_family"]
    _write(tmp_path, monkeypatch, bad)
    errors, warnings = dec.validate_declaration(dec.load_declaration("journal"), SCHEMA)
    assert any("NOPE" in e for e in errors)
    assert any("missing_family" in e for e in errors)


def test_validate_clean(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, GOOD)
    errors, warnings = dec.validate_declaration(dec.load_declaration("journal"), SCHEMA)
    assert errors == []


def test_bad_yaml_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_MART_DIR", tmp_path)
    (tmp_path / "journal.yaml").write_text("format: [")
    with pytest.raises(dec.MartDeclarationError):
        dec.load_declaration("journal")
