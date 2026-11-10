from artmind.mart import status


def test_coverage_counts(monkeypatch):
    monkeypatch.setattr(status.records, "load_dimensions", lambda d, **k: {
        ("place", "a"): {"levels": {"country": "C"}, "source": "llm"},
        ("place", "b"): {"levels": {"city": "X"}, "source": "seed"},
        ("person", "p"): {"levels": {"group": "family"}, "source": "rule"}})
    monkeypatch.setattr(status.records, "proposed", lambda d: [{"id": "r"}])
    monkeypatch.setattr(status, "_counts", lambda decl, domains: {"place": 4, "person": 2})
    out = status.report(type("D", (), {"domain": "j"})(), ["j"])
    assert out["coverage"]["place"] == {"members": 4, "with_country": 1, "with_city": 1, "by_source": {"llm": 1, "seed": 1}}
    assert out["coverage"]["person"]["with_group"] == 1 and out["proposed"] == 1
