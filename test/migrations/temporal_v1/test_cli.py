"""`artmind migrate status` / `migrate temporal` over fake steps (the pack's runner)."""
import json
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from artmind import backfill, data_model, setup
from artmind.cli import cli
from artmind.migrations.temporal_v1 import cli as pack_cli
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import Step


class FakeSession:
    def run(self, *_a, **_k):
        class R:
            def consume(self):
                return None
        return R()


@contextmanager
def _graph():
    yield FakeSession()


class Fake:
    """A step whose state is a counter of pending items; records what was called."""

    def __init__(self, name, n):
        self.name, self.n, self.calls = name, n, []

    def step(self, log):
        def pending(ctx):
            log.append(("pending", self.name))
            return {"items": self.n} if self.n else {}

        def apply(ctx, dry_run):
            log.append(("apply", self.name, dry_run))
            done, self.n = self.n, 0
            return {"converted": done}
        return Step(self.name, pending, apply)


@pytest.fixture
def env(monkeypatch, tmp_path):
    log = []
    a, b, c = Fake("alpha", 2), Fake("beta", 0), Fake("gamma", 1)
    monkeypatch.setattr(steps_mod, "STEPS", [a.step(log), b.step(log), c.step(log)])
    monkeypatch.setattr(pack_cli, "_open_graph", _graph)
    monkeypatch.setattr(pack_cli, "_vault_root", lambda: tmp_path)
    monkeypatch.setattr(setup, "_uncommitted", lambda root, path: False)
    monkeypatch.setattr(backfill, "_worker_running", lambda: False)
    monkeypatch.setattr(data_model, "recorded", lambda: 0)
    monkeypatch.setattr(data_model, "read_graph_version", lambda *a, **k: 0)
    return log


def _run(*args, ok=True):
    result = CliRunner().invoke(cli, [*args, "--compact"])
    assert (result.exit_code == 0) == ok, result.output
    return result


def test_status_lists_every_step_with_done(env):
    out = json.loads(_run("migrate", "status").stdout)
    assert [(s["name"], s["done"]) for s in out["steps"]] == [("alpha", False), ("beta", True), ("gamma", False)]
    assert out["steps"][0]["pending"] == {"items": 2}
    assert (out["version"], out["graph_version"], out["current"]) == (0, 0, False)
    assert out["blocked"] == [] and out["next"] == "artmind migrate temporal --dryRun"
    assert all(e[0] == "pending" for e in env)


def test_status_is_not_gated_and_prints_no_nag(env):
    result = _run("migrate", "status")
    assert "Run: artmind migrate status" not in result.output


def test_dry_run_calls_only_pending(env):
    out = json.loads(_run("migrate", "temporal", "--dryRun").stdout)
    assert out["dry_run"] is True and all(e[0] == "pending" for e in env)
    assert out["effect"]["changes"] == [{"kind": "data_migration", "count": 2, "sample": ["alpha", "gamma"]}]


def test_real_run_applies_only_pending_steps_in_order_then_rechecks(env):
    out = json.loads(_run("migrate", "temporal").stdout)
    assert [e[:2] for e in env if e[0] == "apply"] == [("apply", "alpha"), ("apply", "gamma")]
    assert all(e[2] is False for e in env if e[0] == "apply")
    assert env[-3:] == [("pending", "alpha"), ("pending", "beta"), ("pending", "gamma")]  # the re-check
    assert out["done"] is True and [s["done"] for s in out["steps"]] == [True, True, True]
    assert out["steps"][1]["applied"] == {}
    env.clear()
    _run("migrate", "temporal")
    assert not [e for e in env if e[0] == "apply"]  # a second run is a no-op


@pytest.mark.parametrize("reason, patch", [
    ("uncommitted", lambda mp: mp.setattr(setup, "_uncommitted", lambda root, path: True)),
    ("ingest worker", lambda mp: mp.setattr(backfill, "_worker_running", lambda: True)),
    ("Neo4j", lambda mp: mp.setattr(pack_cli, "_open_graph", lambda: (_ for _ in ()).throw(OSError("down")))),
])
def test_a_failed_precondition_writes_nothing_and_exits_1(env, monkeypatch, reason, patch):
    patch(monkeypatch)
    result = _run("migrate", "temporal", ok=False)
    assert result.exit_code == 1 and reason in result.output
    assert not [e for e in env if e[0] == "apply"]


def test_the_dry_run_reports_preconditions_as_blocked(env, monkeypatch):
    monkeypatch.setattr(backfill, "_worker_running", lambda: True)
    out = json.loads(_run("migrate", "temporal", "--dryRun").stdout)
    assert len(out["blocked"]) == 1 and "worker" in out["blocked"][0]
