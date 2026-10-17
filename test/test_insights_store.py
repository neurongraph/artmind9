import json

import pytest

import paths
from artmind.insights import store
from artmind.insights.model import Finding, InsightsError, Run


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    return tmp_path


def make_run(created_at="2026-10-17T09:30:12Z", domain="demo"):
    return Run(domain=domain, created_at=created_at, as_of="2026-10-01", fingerprint={}, anchors=[],
               profile={}, plan=[], circles=[], diff={},
               findings=[Finding(key="k", detector="dormancy", kind="corpus", score=0.5, title="t")])


def test_write_creates_run_and_latest_without_temp_files(roots):
    path = store.write_run(make_run())
    assert path == roots / "insights" / "demo" / "runs" / "2026-10-17T09-30-12Z.json"
    assert json.loads(path.read_text())["format"] == 1
    assert (roots / "insights" / "demo" / "latest.json").read_text() == path.read_text()
    assert not list((roots / "insights").rglob("*.tmp"))
    assert store.load_latest("demo") == make_run()


def test_latest_missing_and_unreadable(roots):
    assert store.load_latest("demo") is None
    (roots / "insights" / "demo").mkdir(parents=True)
    (roots / "insights" / "demo" / "latest.json").write_text("{not json")
    with pytest.raises(InsightsError, match="unreadable"):
        store.load_latest("demo")
    # Valid JSON, wrong shape: a hand-edited or truncated snapshot.
    for bad in ('[]', '{"format": 1}', '{"format": 1, "domain": "demo", "created_at": "x", "findings": [{"key": "k"}]}'):
        (roots / "insights" / "demo" / "latest.json").write_text(bad)
        with pytest.raises(InsightsError, match="unreadable"):
            store.load_latest("demo")


def test_list_and_prune_keep_the_newest(roots):
    for minute in range(5):
        store.write_run(make_run(created_at=f"2026-10-17T09:0{minute}:00Z"))
    assert len(store.list_runs("demo")) == 5
    removed = store.prune("demo", keep=2)
    assert removed == [f"2026-10-17T09-0{m}-00Z.json" for m in range(3)]
    assert [p.name for p in store.list_runs("demo")] == ["2026-10-17T09-03-00Z.json", "2026-10-17T09-04-00Z.json"]


def test_domain_names_cannot_escape(roots):
    with pytest.raises(InsightsError):
        store.domain_dir("../etc")
    assert store.domain_dir("finance.retail") == roots / "insights" / "finance.retail"
