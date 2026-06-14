from artmind.wizard import apply_jq_filter


def test_apply_jq_filter_extracts_field():
    data = '{"name": "Holmes", "entity_class": "CHARACTER"}'
    result = apply_jq_filter(data, ".name")
    assert result == '"Holmes"'


def test_apply_jq_filter_array():
    data = '[{"name": "Holmes"}, {"name": "Watson"}]'
    result = apply_jq_filter(data, "[.[] | .name]")
    assert "Holmes" in result
    assert "Watson" in result


def test_apply_jq_filter_invalid_expression_returns_error():
    data = '{"name": "Holmes"}'
    result = apply_jq_filter(data, "INVALID{{{{")
    assert "error" in result.lower()


def test_apply_jq_filter_non_json_returns_error():
    result = apply_jq_filter("not json", ".foo")
    assert "error" in result.lower()
