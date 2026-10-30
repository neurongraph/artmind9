"""`graph_extraction: false` marks a chunks-only domain (spec 2026-10-30)."""
import artmind.schema_validate as sv

META = {"kinds": {"entity": {}}, "reserved_prefix": "_"}


def test_enabled_unless_literally_false():
    assert sv.graph_extraction_enabled({}) is True
    assert sv.graph_extraction_enabled(None) is True
    assert sv.graph_extraction_enabled({"graph_extraction": True}) is True
    assert sv.graph_extraction_enabled({"graph_extraction": False}) is False


def test_a_non_boolean_value_is_a_validation_error():
    errors = sv.validate_schema({"name": "n", "graph_extraction": "no"}, META)
    assert any("graph_extraction" in e and "true or false" in e for e in errors)
    assert sv.validate_schema({"name": "n", "graph_extraction": False}, META) == []


def test_ignored_keys_warn_only_when_off():
    schema = {"name": "n", "graph_extraction": False, "entity_types": {"A": {}}, "property_aliases": {"x": "y"}}
    warnings = sv.graph_extraction_warnings(schema, "n")
    assert len(warnings) == 2 and all("ignored" in w for w in warnings)
    assert sv.graph_extraction_warnings({"name": "n", "entity_types": {"A": {}}}, "n") == []


def test_domain_lookup_reads_that_domains_own_file_and_never_inherits(tmp_path, monkeypatch):
    (tmp_path / "docs_schema.yaml").write_text("name: docs\ngraph_extraction: false\n")
    (tmp_path / "docs.child_schema.yaml").write_text("name: docs.child\n")
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", tmp_path)
    assert sv.domain_graph_extraction("docs") is False
    assert sv.domain_graph_extraction("docs.child") is True      # not inherited
    assert sv.domain_graph_extraction("missing") is True         # no schema: default
    (tmp_path / "bad_schema.yaml").write_text("name: [unclosed")
    assert sv.domain_graph_extraction("bad") is True             # unreadable: default
