"""Answerability patterns over a normalized subgraph."""

import json

import pytest

from artmind.reviews import answerability as ans

GRAPH = {
    "nodes": [
        {"id": "d", "name": "Diarist", "class": "PERSON", "properties": {}},
        {"id": "m", "name": "Mira", "class": "PERSON", "properties": {"role": "friend"}},
        {"id": "l", "name": "Lakeport", "class": "LOCATION", "properties": {}},
    ],
    "edges": [
        {"source": "d", "target": "l", "rel_type": "WENT_TO", "properties": {"date": "2031-03-04"}},
        {"source": "m", "target": "d", "rel_type": "SPENDS_TIME_WITH", "properties": {}},
    ],
}


def test_match_simple_path_case_insensitive():
    pat = [{"class": "person", "name": "diarist"}, {"rel": "went_to"}, {"class": "LOCATION"}]
    result = ans.match(pat, GRAPH)
    assert result["matched"] is True
    assert result["matches"] == [["Diarist", "Lakeport"]]


def test_no_match_for_missing_companion_edge():
    pat = [{"class": "PERSON", "name": "Mira"}, {"rel": "WENT_TO"}, {"class": "LOCATION", "name": "Lakeport"}]
    assert ans.match(pat, GRAPH) == {"matched": False, "count": 0, "matches": []}


def test_direction():
    assert ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "in"}, {"name": "Mira"}], GRAPH)["matched"]
    assert not ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "out"}, {"name": "Mira"}], GRAPH)["matched"]
    assert ans.match([{"name": "Diarist"}, {"rel": "SPENDS_TIME_WITH", "dir": "any"}, {"name": "Mira"}], GRAPH)["matched"]


def test_require_node_and_edge_properties():
    base = [{"class": "PERSON"}, {"rel": "WENT_TO"}, {"class": "LOCATION"}]
    assert ans.match(base + [{"require": {"edge": 0, "properties": ["date"]}}], GRAPH)["matched"]
    assert not ans.match(base + [{"require": {"edge": 0, "properties": ["purpose"]}}], GRAPH)["matched"]
    assert ans.match(base + [{"require": {"node": 0, "properties": ["name"]}}], GRAPH)["matched"]
    assert not ans.match(base + [{"require": {"node": 0, "properties": ["role"]}}], GRAPH)["matched"]


def test_single_node_pattern():
    assert ans.match([{"class": "LOCATION", "name": "Lakeport"}], GRAPH)["count"] == 1


@pytest.mark.parametrize("bad", [
    [], {"class": "X"}, [{"rel": "R"}], [{"class": "A"}, {"class": "B"}], [{"class": "A"}, {"rel": "R"}],
    [{"class": "A"}, {"rel": "R", "dir": "up"}, {"class": "B"}],
    [{"class": "A"}, {"rel": "R"}, {"class": "B"}, {"require": {"node": 3, "properties": ["x"]}}],
    [{"require": {"node": 0, "properties": ["x"]}}, {"class": "A"}],
])
def test_parse_pattern_rejects(bad):
    with pytest.raises(ans.PatternError):
        ans.parse_pattern(bad)


def test_load_pattern_arg_json_and_file(tmp_path):
    pat = [{"class": "PERSON"}]
    assert ans.load_pattern_arg(json.dumps(pat)) == pat
    f = tmp_path / "p.json"
    f.write_text(json.dumps(pat))
    assert ans.load_pattern_arg(f"@{f}") == pat
    with pytest.raises(ans.PatternError):
        ans.load_pattern_arg("not json")


def test_from_staging_merges_by_name_and_class_and_skips_extracted_from():
    obs = [
        {"name": "Mira", "entity_class": "PERSON", "chunk_id": "c", "role": "friend", "_kind": "recurrent", "id": "o1"},
        {"name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "c", "id": "o2"},
    ]
    rels = [
        {"source_name": "Mira", "target_name": "Lakeport", "rel_type": "went_to", "chunk_id": "c"},
        {"source_name": "Mira", "target_name": "c", "rel_type": "EXTRACTED_FROM", "chunk_id": "c"},
    ]
    g = ans.from_staging(obs, rels)
    assert {(n["name"], n["class"]) for n in g["nodes"]} == {("Mira", "PERSON"), ("Lakeport", "LOCATION")}
    assert next(n for n in g["nodes"] if n["name"] == "Mira")["properties"] == {"role": "friend"}
    assert [e["rel_type"] for e in g["edges"]] == ["went_to"]
    assert ans.match([{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}], g)["matched"]


def test_from_extraction():
    ents = [{"id": "p1", "name": "Mira", "entity_class": "PERSON", "properties": {"role": "friend"}},
            {"id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "properties": {}}]
    rels = [{"source_id": "p1", "target_id": "l1", "rel_type": "went_to", "properties": {}},
            {"source_id": "p1", "target_id": "zz", "rel_type": "knows"}]  # dangling id: dropped
    g = ans.from_extraction(ents, rels)
    assert len(g["edges"]) == 1
    assert ans.match([{"name": "Mira"}, {"rel": "went_to"}, {"name": "Lakeport"}], g)["matched"]
