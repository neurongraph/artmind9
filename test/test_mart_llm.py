from artmind.mart import llm


def test_prompt_lists_allowed_levels_and_the_member():
    p = llm.prompt("place", {"key": "x", "props": {"name": "Riverside", "description": "a town", "context": ["went"]}})
    assert "Riverside" in p and "country" in p and "JSON" in p


def test_parse_keeps_declared_levels_only():
    assert llm.parse("place", '{"levels": {"city": "A", "country": "B", "planet": "C"}, "confidence": 0.8}') == \
        ({"city": "A", "country": "B"}, 0.8)
    assert llm.parse("person", '{"levels": {"group": "spaceship"}, "confidence": 0.9}') == (None, 0.0)
    assert llm.parse("place", "not json") == (None, 0.0)


def test_run_applies_or_proposes(monkeypatch):
    written = []
    monkeypatch.setattr(llm, "_call", lambda prompt: '{"levels": {"country": "B"}, "confidence": 0.95}')
    monkeypatch.setattr(llm.records, "put_dimension", lambda *a, **k: written.append(k["status"]))

    class D:  # minimal declaration stand-in
        domain = "j"
    out = llm.run(D(), [{"dimension": "place", "key": "x", "props": {"name": "R"}}], threshold=0.9)
    assert out == {"applied": 1, "proposed": 0, "rejected": 0} and written == ["applied"]


def test_type_family_must_be_declared():
    assert llm.parse("type", '{"levels": {"family": "invented"}, "confidence": 0.9}', ["trip", "work"]) == (None, 0.0)
    assert llm.parse("type", '{"levels": {"family": "trip"}, "confidence": 0.9}', ["trip", "work"]) == ({"family": "trip"}, 0.9)
    assert "trip, work" in llm.prompt("type", {"key": "type:x", "props": {"name": "x"}}, ["trip", "work"])
