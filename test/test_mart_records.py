import paths
from artmind import curation_records
from artmind.mart import records


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path)
    monkeypatch.setattr(curation_records, "curation_dir", lambda: tmp_path)


def test_put_and_load(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_dimension("journal", "person", "ann|PERSON|journal", {"group": "family"}, source="rule", confidence=1.0)
    got = records.load_dimensions("journal")
    assert got[("person", "ann|PERSON|journal")]["levels"] == {"group": "family"}


def test_user_record_wins(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_dimension("journal", "place", "x|LOCATION|journal", {"city": "A"}, source="user", confidence=1.0)
    records.put_dimension("journal", "place", "x|LOCATION|journal", {"city": "B"}, source="llm", confidence=0.9)
    assert records.load_dimensions("journal")[("place", "x|LOCATION|journal")]["levels"] == {"city": "A"}


def test_canonical_follow(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_dimension("journal", "person", "bob|PERSON|journal", {"group": "friend"}, source="rule", confidence=1)
    got = records.load_dimensions("journal", canonical={"bob|PERSON|journal": "robert|PERSON|journal"})
    assert ("person", "robert|PERSON|journal") in got


def test_calendar(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_period("journal", "Festival", "2025-10-01", "2025-10-05", source="user")
    assert records.periods_on("journal", "2025-10-03") == ["Festival"]
    assert records.periods_on("journal", "2025-10-09") == []


def test_drop_seed_record(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_dimension("journal", "place", "x|LOCATION|journal", {"city": "bogus"}, source="seed", confidence=1)
    records.drop_dimension("journal", "place", "x|LOCATION|journal", source="seed")
    assert records.load_dimensions("journal") == {}


def test_user_record_answers_a_proposal(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    records.put_dimension("journal", "place", "x|LOCATION|journal", {"city": "B"}, source="llm", confidence=0.5,
                          status="proposed")
    records.put_dimension("journal", "place", "y|LOCATION|journal", {"city": "C"}, source="llm", confidence=0.4,
                          status="proposed")
    assert len(records.proposed("journal")) == 2
    records.put_dimension("journal", "place", "x|LOCATION|journal", {"city": "A"}, source="user", confidence=1.0)
    assert [r["member_key"] for r in records.proposed("journal")] == ["y|LOCATION|journal"]


def test_filter_proposals():
    rows = [{"id": "1", "dimension": "place", "member_key": "cafe peter|LOCATION|j", "levels": {"city": "Pune"},
             "confidence": 0.8},
            {"id": "2", "dimension": "person", "member_key": "ann|PERSON|j", "levels": {"group": "friend"},
             "confidence": 0.3},
            {"id": "3", "dimension": "place", "member_key": "barclays|LOCATION|j", "levels": {"venue": "Barclays"},
             "confidence": 0.6}]
    assert [r["id"] for r in records.filter_proposals(rows)] == ["2", "3", "1"]
    assert [r["id"] for r in records.filter_proposals(rows, dimension="place", sort="name")] == ["3", "1"]
    assert [r["id"] for r in records.filter_proposals(rows, match="PUNE")] == ["1"]
    assert [r["id"] for r in records.filter_proposals(rows, max_confidence=0.6)] == ["2", "3"]
