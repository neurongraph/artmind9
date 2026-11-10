from artmind.mart import hooks


def test_build_after_rebuild_is_best_effort(monkeypatch):
    calls = []
    monkeypatch.setattr(hooks, "_declared", lambda domains: ["j"])
    monkeypatch.setattr(hooks, "_build", lambda d: calls.append(d) or (_ for _ in ()).throw(RuntimeError("x")))
    out = hooks.build_after(["j", "k"])
    assert calls == ["j"] and out == {"j": "failed: x"}
