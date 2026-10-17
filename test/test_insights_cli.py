import json

import pytest
from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.insights import store
from artmind.insights.model import Finding, Run


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    findings = [
        Finding(key="dormancy:PERSON:p1", detector="dormancy", kind="corpus", score=0.9, title="Quiet: Asha", diff="new"),
        Finding(key="kind_mismatch:ACTIVITY", detector="kind_mismatch", kind="health", score=0.4,
                title="ACTIVITY mismatch", action="schema", diff="same"),
        Finding(key="dormancy:PERSON:p9", detector="dormancy", kind="corpus", score=0.2, title="Quiet: Ben", diff="gone"),
    ]
    run = Run(domain="demo", created_at="2026-10-17T09:00:00Z", as_of="2026-10-01", fingerprint={}, anchors=[],
              profile={}, plan=[], circles=[], diff={"new": 1}, findings=findings)
    store.write_run(run)
    return run


def invoke(*args):
    return CliRunner().invoke(cli, ["insights", *args, "--compact"])


def test_list_ranks_live_findings_and_filters(snap):
    out = json.loads(invoke("list", "--domain", "demo").output)
    assert [f["key"] for f in out["findings"]] == ["dormancy:PERSON:p1", "kind_mismatch:ACTIVITY"]
    assert out["as_of"] == "2026-10-01"
    health = json.loads(invoke("list", "--domain", "demo", "--kind", "health").output)
    assert [f["key"] for f in health["findings"]] == ["kind_mismatch:ACTIVITY"]
    changed = json.loads(invoke("list", "--domain", "demo", "--new").output)
    assert [f["key"] for f in changed["findings"]] == ["dormancy:PERSON:p1", "dormancy:PERSON:p9"]


def test_show_and_missing_key(snap):
    out = json.loads(invoke("show", "dormancy:PERSON:p1", "--domain", "demo").output)
    assert out["finding"]["title"] == "Quiet: Asha" and out["created_at"] == "2026-10-17T09:00:00Z"
    missing = invoke("show", "nope", "--domain", "demo")
    assert missing.exit_code != 0 and "no finding" in missing.output


def test_runs_lists_history(snap):
    out = json.loads(invoke("runs", "--domain", "demo").output)
    assert out["runs"] == [{"run": "2026-10-17T09-00-00Z.json", "created_at": "2026-10-17T09:00:00Z",
                            "as_of": "2026-10-01", "findings": 2, "diff": {"new": 1}}]


def test_no_snapshot_is_a_friendly_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "empty")
    result = invoke("list", "--domain", "demo")
    assert result.exit_code != 0 and "no insights snapshot" in result.output


def test_refresh_passes_options_through(monkeypatch):
    from artmind.insights import runner

    seen = {}
    monkeypatch.setattr(runner, "refresh", lambda domain, **kw: seen.update(domain=domain, **kw) or {"status": "written"})
    result = invoke("refresh", "--domain", "demo", "--force", "--only", "dormancy, brokers")
    assert result.exit_code == 0, result.output
    assert seen == {"domain": "demo", "force": True, "only": ["dormancy", "brokers"]}


def test_refresh_needs_exactly_one_domain():
    result = invoke("refresh", "--domain", "a,b")
    assert result.exit_code != 0 and "exactly one --domain" in result.output
