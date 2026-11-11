import yaml

import paths
from artmind.mart import metadata


def test_generated_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    tables = {"dim_place": [{"place_key": "l1", "country": "C"}], "dim_person": [{"person_key": "p", "group": "family"}],
              "dim_type": [{"type": "outing", "family": "trip"}], "fact_happening": [], "bridge_person": [],
              "bridge_place": [], "whole_trip": []}
    path = metadata.write("j", tables)
    data = yaml.safe_load(path.read_text())
    assert data["generated"] == "mart" and path.name == "j.mart.meta.yaml"
    rels = {(r["from"], r["to"]) for r in data["relationships"]}
    assert ("j__mart_bridge_place.place_key", "j__mart_dim_place.place_key") in rels
    assert "C" in data["tables"]["j__mart_dim_place"]["columns"]["country"]["values"]
    assert any("evidence" in r for r in data["rules"])


def test_wholes_columns_and_rule_are_described(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    tables = {"dim_person": [], "dim_place": [], "fact_happening": [], "fact_whole": [],
              "whole_trip": [], "whole_life_event": []}
    data = yaml.safe_load(metadata.write("j", tables).read_text())
    trip = data["tables"]["j__mart_whole_trip"]
    assert trip["grain"] and trip["description"]
    cols = trip["columns"]
    assert "j__mart_dim_person.person_key" in cols["participant_keys"]["description"]
    assert "list_contains" in cols["participant_keys"]["description"]
    assert "j__mart_dim_place.place_key" in cols["place_keys"]["description"]
    assert cols["anchor_key"]["description"] and "ILIKE" in cols["part_names"]["description"]
    assert data["tables"]["j__mart_whole_life_event"]["columns"]["anchor_key"]["description"]
    assert data["tables"]["j__mart_fact_happening"]["grain"]
    rule = next(r for r in data["rules"] if "j__mart_whole_trip" in r)
    assert "already" in rule and "city" in rule
    assert "never filter" in cols["cities"]["description"] and "never filter" in trip["description"]


def test_person_aliases_are_described(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    data = yaml.safe_load(metadata.write("j", {"dim_person": [{"person_key": "p", "group": "family"}]}).read_text())
    cols = data["tables"]["j__mart_dim_person"]["columns"]
    assert "aliases" in cols["aliases"]["description"] and "group" in cols
