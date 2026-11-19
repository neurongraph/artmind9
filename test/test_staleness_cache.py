"""The staleness line is cached briefly so a behind vault does not cost ~1.5 s
of git subprocesses per short-lived `artmind query` process."""
from pathlib import Path

import pytest

import artmind.vault_sync as vs


@pytest.fixture()
def cached(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(vs, "STALENESS_CACHE_TTL_S", 60.0)
    return tmp_path


def _setup(monkeypatch, tmp_path, marks, computed):
    import artmind.manifest as manifest
    import artmind.vault as vault

    monkeypatch.setattr(vault, "read_state", lambda layout: {vs.STRUCTURED_BOOKMARK_KEY: "x"})
    monkeypatch.setattr(manifest, "read_vault_id", lambda d: "vid")
    monkeypatch.setattr(vs, "run_command", lambda *a, **k: (0, "HEAD1\n", ""))
    monkeypatch.setattr(vs, "read_bookmarks", lambda *a, **k: marks[0])
    calls = []

    def _pending(vault_dir, head, m, **kw):
        calls.append(head)
        return computed[0]

    monkeypatch.setattr(vs, "pending_work", _pending)
    monkeypatch.setattr(vs, "staleness_message", lambda pending: pending)
    return calls


def test_second_call_is_served_from_cache(cached, monkeypatch):
    marks = [vs.Bookmarks(graph="A", structured="A")]
    calls = _setup(monkeypatch, cached, marks, ["behind 1"])
    assert vs.query_staleness_warning(cached) == "behind 1"
    assert vs.query_staleness_warning(cached) == "behind 1"
    assert len(calls) == 1


def test_changed_bookmark_recomputes(cached, monkeypatch):
    marks = [vs.Bookmarks(graph="A", structured="A")]
    computed = ["one"]
    calls = _setup(monkeypatch, cached, marks, computed)
    vs.query_staleness_warning(cached)
    marks[0] = vs.Bookmarks(graph="B", structured="A")
    computed[0] = "two"
    assert vs.query_staleness_warning(cached) == "two"
    assert len(calls) == 2


def test_expired_entry_recomputes(cached, monkeypatch):
    marks = [vs.Bookmarks(graph="A", structured="A")]
    calls = _setup(monkeypatch, cached, marks, ["one"])
    vs.query_staleness_warning(cached)
    monkeypatch.setattr(vs, "STALENESS_CACHE_TTL_S", 0.0)
    vs.query_staleness_warning(cached)
    assert len(calls) == 2


def test_none_is_cached_and_errors_are_not(cached, monkeypatch):
    marks = [vs.Bookmarks(graph="A", structured="A")]
    calls = _setup(monkeypatch, cached, marks, [None])
    assert vs.query_staleness_warning(cached) is None
    assert vs.query_staleness_warning(cached) is None
    assert len(calls) == 1

    def boom(*a, **k):
        raise RuntimeError("x")

    marks[0] = vs.Bookmarks(graph="C", structured="A")
    monkeypatch.setattr(vs, "pending_work", boom)
    with pytest.raises(RuntimeError):
        vs.query_staleness_warning(cached)


def test_corrupt_or_unwritable_cache_never_breaks(cached, monkeypatch):
    marks = [vs.Bookmarks(graph="A", structured="A")]
    calls = _setup(monkeypatch, cached, marks, ["m"])
    log = cached / "logs"
    log.mkdir()
    (log / "staleness_cache.json").write_text("{not json")
    assert vs.query_staleness_warning(cached) == "m"
    monkeypatch.setattr(vs, "_staleness_cache_path", lambda: Path("/proc/nope/x.json"))
    assert vs.query_staleness_warning(cached) == "m"
