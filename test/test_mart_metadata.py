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
