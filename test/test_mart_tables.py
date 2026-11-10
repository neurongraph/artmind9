from artmind.mart import tables
from artmind.mart.declaration import MartDeclaration, PersonDim, PlaceDim, TypeDim

DECL = MartDeclaration("j", 1, "Hometown", ["EVENT"], ["EVENT"], True, PersonDim(), PlaceDim(),
                       TypeDim(["EVENT"], "type", {"trip": ["outing"]}), {}, {})
HAPPEN = [{"key": "e1", "name": "Outing", "class": "EVENT", "date": "2025-03-02", "type": "outing", "outcome": None,
           "participants": ["A"], "obs": ["o1"], "chunks": ["c1"], "docs": ["d1"], "first": "2025-03-02",
           "last": "2025-03-02"}]
MENTION = [{"id": "o1", "entity_key": "e1", "class": "EVENT", "date": "2025-03-02", "type": "outing",
            "doc_id": "d1", "chunk_id": "c1", "sentence": "We went out."}]
BRIDGE = [{"fact_key": "e1", "member_key": "p1", "member_class": "PERSON", "role": "PARTICIPATES_IN"},
          {"fact_key": "e1", "member_key": "l1", "member_class": "LOCATION", "role": "OCCURS_AT"}]
RECORDS = {("place", "l1"): {"levels": {"venue": "Park", "city": "Elsewhere", "country": "C"}},
           ("person", "p1"): {"levels": {"group": "family", "relation": "child"}},
           ("type", "type:outing"): {"levels": {"family": "trip"}}}
PERIODS = [{"name": "Spring fest", "start": "2025-03-01", "end": "2025-03-03"}]


def test_assemble_all_tables():
    t = tables.assemble(DECL, HAPPEN, MENTION, BRIDGE, RECORDS, PERIODS,
                        members={"PERSON": [{"key": "p1", "props": {"name": "A"}}],
                                 "LOCATION": [{"key": "l1", "props": {"name": "Park"}}]})
    fh = t["fact_happening"][0]
    assert fh["type_family"] == "trip" and fh["place_key"] == "l1" and fh["home_away"] == "away"
    assert fh["observation_ids"] == ["o1"] and fh["chunk_ids"] == ["c1"]
    assert t["fact_mention"][0]["entity_key"] == "e1" and t["fact_mention"][0]["home_away"] == "away"
    assert t["bridge_person"] == [{"entity_key": "e1", "person_key": "p1", "role": "participates_in"}]
    assert t["dim_place"][0]["country"] == "C" and t["dim_person"][0]["group"] == "family"
    assert t["dim_time"][0] == {"date": "2025-03-02", "week": "2025-W09", "month": "2025-03", "quarter": "2025-Q1",
                                "year": "2025", "period": "Spring fest"}
    assert t["dim_type"] == [{"type": "outing", "family": "trip"}]
