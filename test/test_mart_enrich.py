from artmind.mart import enrich, records
from artmind.mart.declaration import MartDeclaration, PersonDim, PlaceDim, TypeDim

DECL = MartDeclaration("j", 1, "Hometown", ["EVENT"], ["EVENT"], False,
                       PersonDim("PERSON", "rel", "kinship"), PlaceDim("LOCATION", "location", []),
                       TypeDim(["EVENT"], "type", {"trip": ["outing"]}), {}, {})


def _fake(monkeypatch, written):
    people = [{"key": "a|PERSON|j", "props": {"name": "A", "rel": "uncle (mama)"}},
              {"key": "b|PERSON|j", "props": {"name": "B", "rel": "a puzzling description"}}]
    places = [{"key": "c|LOCATION|j", "props": {"name": "Cafe", "place_type": "restaurant", "location": "Metro"}},
              {"key": "d|LOCATION|j", "props": {"name": "Nowhere", "place_type": "park"}}]
    monkeypatch.setattr(enrich.mart_graph, "members", lambda d, c: people if c == "PERSON" else places)
    monkeypatch.setattr(enrich.mart_graph, "happenings", lambda d, c: [{"type": "outing"}, {"type": "meeting"}])
    monkeypatch.setattr(enrich.records, "load_dimensions", lambda d, **k: {})
    monkeypatch.setattr(enrich.records, "put_dimension", lambda *a, **k: written.append((a, k)) or "id")


def test_dry_run_counts_and_writes_nothing(monkeypatch):
    written = []
    _fake(monkeypatch, written)
    plan = enrich.enrich(DECL, ["j"], dry_run=True, use_llm=False)
    assert plan["decided"] == {"person": {"rule": 1}, "place": {"seed": 1}, "type": {"rule": 1}}
    assert plan["undecided"] == {"person": 1, "place": 1, "type": 1}
    assert written == []


def test_real_run_writes_rule_and_seed_records(monkeypatch):
    written = []
    _fake(monkeypatch, written)
    enrich.enrich(DECL, ["j"], dry_run=False, use_llm=False)
    sources = sorted(k["source"] for _, k in written)
    assert sources == ["rule", "rule", "seed"]


def test_rule_records_are_recomputed_but_llm_records_stay(monkeypatch):
    written = []
    _fake(monkeypatch, written)
    monkeypatch.setattr(enrich.records, "load_dimensions", lambda d, **k: {
        ("person", "a|PERSON|j"): {"source": "rule"}, ("place", "d|LOCATION|j"): {"source": "llm"}})
    plan = enrich.enrich(DECL, ["j"], dry_run=True, use_llm=False)
    assert plan["decided"]["person"] == {"rule": 1} and plan["undecided"].get("place", 0) == 0


def test_llm_step_only_asks_the_chosen_dimension(monkeypatch):
    written = []
    _fake(monkeypatch, written)
    asked = []
    enrich.enrich(DECL, ["j"], dry_run=False, use_llm=True, llm=lambda d, items: asked.extend(items) or {},
                  dimensions=("place",))
    assert [a["dimension"] for a in asked] == ["place"]
