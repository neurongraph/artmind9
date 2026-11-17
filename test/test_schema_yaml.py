"""artmind.schema_yaml: libyaml-backed, mtime-keyed schema reads (query-path latency)."""
import os

import yaml

from artmind import schema_yaml, temporal


def test_load_matches_safe_load_and_returns_a_copy(tmp_path):
    p = tmp_path / "x_schema.yaml"
    p.write_text("name: x\nentity_types:\n  PERSON:\n    properties: {age: {type: int}}\n", encoding="utf-8")
    first = schema_yaml.load(p)
    assert first == yaml.safe_load(p.read_text(encoding="utf-8"))
    first["name"] = "mutated"
    first["entity_types"]["PERSON"]["properties"].clear()
    again = schema_yaml.load(p)
    assert again["name"] == "x" and again["entity_types"]["PERSON"]["properties"] == {"age": {"type": "int"}}


def test_load_rereads_an_edited_file(tmp_path):
    p = tmp_path / "x_schema.yaml"
    p.write_text("name: x\n", encoding="utf-8")
    assert schema_yaml.load(p) == {"name": "x"}
    p.write_text("name: yy\n", encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert schema_yaml.load(p) == {"name": "yy"}


def test_load_schema_parses_each_file_once(tmp_path, monkeypatch):
    (tmp_path / "fam_schema.yaml").write_text("name: fam\ntemporal: {defaults: {kind: event}}\n", encoding="utf-8")
    (tmp_path / "fam.leaf_schema.yaml").write_text("name: fam.leaf\n", encoding="utf-8")
    calls = []
    real = schema_yaml.safe_load
    monkeypatch.setattr(schema_yaml, "safe_load", lambda text: calls.append(1) or real(text))
    schema_yaml._load_cached.cache_clear()
    for _ in range(3):
        schema = temporal.load_schema("fam.leaf", schemas_dir=tmp_path)
        schema["temporal"]["mutated"] = True  # callers may mutate the result
    assert len(calls) == 2
    assert "mutated" not in temporal.load_schema("fam.leaf", schemas_dir=tmp_path)["temporal"]
