import pytest

from artmind.ingest import _filter_valid_items, _rewrite_entity_ids, _rewrite_ref_ids


def test_filter_valid_items_drops_non_dict_entries(caplog):
    raw = [{"id": "e1", "name": "A"}, "stray string from malformed LLM json", {"id": "e2"}]
    result = _filter_valid_items(raw, seq=21, step_name="entity", required_key="id")
    assert result == [{"id": "e1", "name": "A"}, {"id": "e2"}]


def test_filter_valid_items_drops_dicts_missing_required_key():
    raw = [{"id": "e1"}, {"name": "no id here"}]
    result = _filter_valid_items(raw, seq=1, step_name="entity", required_key="id")
    assert result == [{"id": "e1"}]


def test_rewrite_entity_ids_does_not_crash_on_filtered_input():
    raw = [{"id": "e1", "name": "A"}, {"id": "e2"}]
    entities, id_map = _rewrite_entity_ids(raw, "chunk_1")
    assert id_map == {"e1": "chunk_1_e1", "e2": "chunk_1_e2"}
    assert entities[0]["id"] == "chunk_1_e1"


def test_rewrite_entity_ids_raises_on_unfiltered_string_item():
    with pytest.raises(TypeError):
        _rewrite_entity_ids(["not-a-dict"], "chunk_1")


def test_rewrite_ref_ids_does_not_crash_on_filtered_input():
    raw = [{"id": "e1", "value": "x"}]
    result = _rewrite_ref_ids(raw, {"e1": "chunk_1_e1"}, "id")
    assert result == [{"id": "chunk_1_e1", "value": "x"}]
