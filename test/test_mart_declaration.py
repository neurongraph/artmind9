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


def test_families_by_name_and_away_place_types(tmp_path, monkeypatch):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["dimensions"]["type"]["families_by_name"] = {"ritual": ["shraddha"], "nope": ["x"]}
    data["home"]["away_place_types"] = ["Resort"]
    _write(tmp_path, monkeypatch, data)
    d = dec.load_declaration("journal")
    assert d.type.family_by_name("Father's Shraddha function") == "ritual" and d.away_place_types == ["resort"]
    errors, _ = dec.validate_declaration(d, SCHEMA)
    assert any("'nope'" in e for e in errors)


# --- dimension links: from_properties and same_chunk (spec 2026-11-19) ---------------------------------------


def _links_decl():
    d = yaml.safe_load(yaml.safe_dump(GOOD))
    d["dimensions"]["person"]["from_properties"] = {"EVENT": ["participants"], "ACTIVITY": ["who_involved"]}
    d["dimensions"]["place"]["from_properties"] = {"EVENT": ["location"]}
    d["dimensions"]["place"]["same_chunk"] = True
    return d


LINK_SCHEMA = {"entity_types": {
    "EVENT": {"kind": "occurrent", "properties": {"type": {}, "participants": {}, "location": {}}},
    "ACTIVITY": {"kind": "occurrent", "properties": {"type": {}, "who_involved": {}}},
    "PERSON": {"kind": "recurrent", "properties": {"relationship_to_narrator": {}}},
    "LOCATION": {"kind": "recurrent", "properties": {"location": {}}}}}


def test_load_reads_dimension_link_sources(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, _links_decl())
    d = dec.load_declaration("journal")
    assert d.person.from_properties == {"EVENT": ["participants"], "ACTIVITY": ["who_involved"]}
    assert d.place.from_properties == {"EVENT": ["location"]}
    assert d.place.same_chunk is True and d.person.same_chunk is False


def test_link_sources_default_to_none(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, GOOD)
    d = dec.load_declaration("journal")
    assert d.person.from_properties == {} and d.place.from_properties == {}
    assert d.person.same_chunk is False and d.place.same_chunk is False


def test_validate_checks_link_classes_and_properties(tmp_path, monkeypatch):
    bad = _links_decl()
    bad["dimensions"]["place"]["from_properties"] = {"EVENT": ["where_it_was"], "NOPE": ["location"]}
    _write(tmp_path, monkeypatch, bad)
    errors, _ = dec.validate_declaration(dec.load_declaration("journal"), LINK_SCHEMA)
    assert any("where_it_was" in e and "EVENT" in e for e in errors)
    assert any("NOPE" in e and "from_properties" in e for e in errors)


def test_validate_accepts_declared_link_properties_and_warns_on_same_chunk(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, _links_decl())
    errors, warnings = dec.validate_declaration(dec.load_declaration("journal"), LINK_SCHEMA)
    assert errors == []
    assert any("same_chunk" in w and "place" in w for w in warnings)
