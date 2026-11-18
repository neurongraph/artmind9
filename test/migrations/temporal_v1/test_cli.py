"""`artmind migrate status` / `migrate temporal` over fake steps (the pack's runner)."""
import json
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from artmind import backfill, data_model
from artmind.cli import cli
from artmind.migrations.temporal_v1 import cli as pack_cli
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import Step


REAL_GIT = pack_cli._git


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


class _Out:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout = rc, out


def _git(repo=True, dirty=False, calls=None):
    def run(root, args):
        if calls is not None:
            calls.append(args)
        if args[0] == "rev-parse":
            return _Out(0 if repo else 128)
        return _Out(0, " M kg/x.json" if dirty else "")
    return run


@pytest.fixture
def env(monkeypatch, tmp_path):
    log = []
    a, b, c = Fake("alpha", 2), Fake("beta", 0), Fake("gamma", 1)
    monkeypatch.setattr(steps_mod, "STEPS", [a.step(log), b.step(log), c.step(log)])
    monkeypatch.setattr(pack_cli, "_open_graph", _graph)
    monkeypatch.setattr(pack_cli, "_vault_root", lambda: tmp_path)
    monkeypatch.setattr(pack_cli, "_probe_graph", _graph)
    monkeypatch.setattr(pack_cli, "_git", _git())
    monkeypatch.setattr("artmind.cli._get_available_domains", lambda: ["a", "b"])
    monkeypatch.setattr(backfill, "worker_is_running", lambda: False)
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
    ("uncommitted", lambda mp: mp.setattr(pack_cli, "_git", _git(dirty=True))),
    ("repository", lambda mp: mp.setattr(pack_cli, "_git", _git(repo=False))),
    ("ingest worker", lambda mp: mp.setattr(backfill, "worker_is_running", lambda: True)),
    ("Neo4j", lambda mp: mp.setattr(pack_cli, "_probe_graph", lambda: (_ for _ in ()).throw(OSError("down")))),
])
def test_a_failed_precondition_writes_nothing_and_exits_1(env, monkeypatch, reason, patch):
    patch(monkeypatch)
    result = _run("migrate", "temporal", ok=False)
    assert result.exit_code == 1 and reason in result.output
    assert not [e for e in env if e[0] == "apply"]


def test_the_dry_run_reports_preconditions_as_blocked(env, monkeypatch):
    monkeypatch.setattr(backfill, "worker_is_running", lambda: True)
    out = json.loads(_run("migrate", "temporal", "--dryRun").stdout)
    assert len(out["blocked"]) == 1 and "worker" in out["blocked"][0]


def test_git_status_runs_once_over_directories(env, monkeypatch):
    calls = []
    monkeypatch.setattr(pack_cli, "_git", _git(calls=calls))
    _run("migrate", "temporal", "--dryRun")
    assert [c[0] for c in calls] == ["rev-parse", "status"]


def _two_domain_steps(monkeypatch, log):
    left = {"a": 1, "b": 1}

    def pending(ctx):
        n = sum(left[d] for d in (ctx.domains or left))
        return {"items": n} if n else {}

    def apply(ctx, dry):
        for d in (ctx.domains or list(left)):
            left[d] = 0
        return {}
    monkeypatch.setattr(steps_mod, "STEPS", [Step("per", pending, apply)])
    monkeypatch.setattr(steps_mod, "stamp_if_done", lambda ctx: log.append(ctx.domains) or {})


def test_a_scoped_run_hands_the_stamp_an_unscoped_context(env, monkeypatch):
    log = []
    _two_domain_steps(monkeypatch, log)
    _run("migrate", "temporal", "--domain", "a")
    assert log == [None]  # None: every node, whatever its domain


def test_no_domain_is_unscoped(env, monkeypatch):
    seen = []
    monkeypatch.setattr(steps_mod, "STEPS", [Step("s", lambda ctx: seen.append(ctx.domains) or {}, lambda c, d: {})])
    _run("migrate", "status")
    assert seen == [None]


def test_a_step_made_pending_by_an_earlier_apply_runs_in_the_same_run(env, monkeypatch):
    state = {"first": 1, "second": 0}

    def apply_first(ctx, dry):
        state["first"], state["second"] = 0, 2  # e.g. a folded date makes an end to restate
        return {}

    def apply_second(ctx, dry):
        state["second"] = 0
        return {"done": 2}
    monkeypatch.setattr(steps_mod, "STEPS", [
        Step("first", lambda c: {"n": state["first"]} if state["first"] else {}, apply_first),
        Step("second", lambda c: {"n": state["second"]} if state["second"] else {}, apply_second)])
    out = json.loads(_run("migrate", "temporal").stdout)
    assert out["done"] is True and out["steps"][1]["applied"] == {"done": 2}
    assert out["steps"][1]["pending"] == {"n": 2}


def test_domain_reaches_the_context(env, monkeypatch):
    seen = []
    monkeypatch.setattr(steps_mod, "STEPS", [Step("s", lambda ctx: seen.append(ctx.domains) or {}, lambda c, d: {})])
    _run("migrate", "status", "--domain", "a")
    assert seen == [["a"]]


def test_a_failing_step_reports_and_stops(env, monkeypatch):
    log, stamps = [], []

    def boom(ctx, dry):
        raise RuntimeError("bad key")
    monkeypatch.setattr(steps_mod, "STEPS", [Fake("one", 1).step(log), Step("two", lambda c: {"n": 1}, boom),
                                              Fake("three", 1).step(log)])
    monkeypatch.setattr(steps_mod, "stamp_if_done", lambda ctx: stamps.append(1) or {"version": 1})
    result = _run("migrate", "temporal", ok=False)
    out = json.loads(result.stdout)
    assert result.exit_code == 1 and stamps == []
    assert out["steps"][1]["error"] == "RuntimeError: bad key" and out["done"] is False
    assert ("apply", "three", False) not in log and ("apply", "one", False) in log
    assert out["steps"][2]["remaining"] == {"items": 1}  # the re-check ran


def test_a_store_failure_in_a_step_exits_3(env, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    def boom(ctx, dry):
        raise ServiceUnavailable("down")
    monkeypatch.setattr(steps_mod, "STEPS", [Step("g", lambda c: {"n": 1}, boom)])
    assert _run("migrate", "temporal", ok=False).exit_code == 3


def test_a_step_still_pending_after_apply_exits_1(env, monkeypatch):
    monkeypatch.setattr(steps_mod, "STEPS", [Step("stuck", lambda c: {"n": 1}, lambda c, d: {})])
    result = _run("migrate", "temporal", ok=False)
    out = json.loads(result.stdout)
    assert result.exit_code == 1 and out["done"] is False and out["steps"][0]["remaining"] == {"n": 1}


def test_an_unreadable_step_is_reported_and_status_still_prints(env, monkeypatch):
    def boom(ctx):
        raise OSError("no graph")
    monkeypatch.setattr(steps_mod, "STEPS", [Step("bad", boom, lambda c, d: {}), Fake("ok", 1).step([])])
    out = json.loads(_run("migrate", "status").stdout)
    assert out["steps"][0]["pending"] is None and out["steps"][0]["done"] is False and "error" in out["steps"][0]
    assert out["steps"][1]["pending"] == {"items": 1}
    assert any("bad" in w for w in out["warnings"])
    assert json.loads(_run("migrate", "temporal", "--dryRun").stdout)["steps"][0]["pending"] is None


def test_status_writes_nothing(env, monkeypatch, tmp_path):
    stamps = []
    monkeypatch.setattr(data_model, "stamp", lambda v: stamps.append(v))
    monkeypatch.setattr(data_model, "stamp_graph", lambda s, v: stamps.append(v))
    monkeypatch.setattr(steps_mod, "stamp_if_done", lambda ctx: stamps.append(1) or {"version": 1})
    before = sorted(tmp_path.rglob("*"))
    _run("migrate", "status")
    assert stamps == [] and sorted(tmp_path.rglob("*")) == before



def test_only_the_declared_paths_are_checked(env, monkeypatch, tmp_path):
    """Real git: a dirty neighbour of data_model.yaml does not block; a dirty declared file is named."""
    import subprocess

    def sh(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    sh("init", "-q")
    (tmp_path / "data_model.yaml").write_text("version: 0\n")
    (tmp_path / "notes.txt").write_text("x")
    sh("add", "-A")
    sh("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    monkeypatch.setattr(pack_cli, "_git", REAL_GIT)
    monkeypatch.setattr(data_model, "MODEL_FILE", tmp_path / "data_model.yaml")
    (tmp_path / "notes.txt").write_text("edited")
    assert _run("migrate", "temporal", "--dryRun") and json.loads(_run("migrate", "temporal", "--dryRun").stdout)["blocked"] == []
    (tmp_path / "data_model.yaml").write_text("version: 5\n")
    blocked = json.loads(_run("migrate", "temporal", "--dryRun").stdout)["blocked"]
    assert len(blocked) == 1 and "data_model.yaml" in blocked[0] and "notes.txt" not in blocked[0]


def test_a_store_failure_reading_pending_exits_3(env, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    def down(ctx):
        raise ServiceUnavailable("down")
    monkeypatch.setattr(steps_mod, "STEPS", [Step("g", down, lambda c, d: {})])
    assert _run("migrate", "temporal", ok=False).exit_code == 3


def test_status_explains_a_pulled_stamp_over_an_unmigrated_graph(env, monkeypatch):
    monkeypatch.setattr(data_model, "recorded", lambda: data_model.CURRENT)
    out = json.loads(_run("migrate", "status").stdout)
    assert out["current"] is False and any("this machine's graph" in w for w in out["warnings"])


def _stamping(env, monkeypatch, tmp_path, graph_error):
    monkeypatch.setattr(steps_mod, "STEPS", [Fake("one", 1).step([])])
    monkeypatch.setattr(data_model, "MODEL_FILE", tmp_path / "data_model.yaml")
    calls = []

    def stamp_graph(session, version):
        calls.append(version)
        if graph_error:
            raise graph_error
    monkeypatch.setattr(data_model, "stamp_graph", stamp_graph)
    return calls


def test_a_failed_graph_stamp_reports_after_the_file_stamp_and_exits_3(env, monkeypatch, tmp_path):
    from neo4j.exceptions import ServiceUnavailable

    _stamping(env, monkeypatch, tmp_path, ServiceUnavailable("down"))
    result = _run("migrate", "temporal", ok=False)
    out = json.loads(result.stdout)
    assert result.exit_code == 3 and out["stamped"] is False
    assert any("graph marker not written" in w and "re-run artmind migrate temporal" in w for w in out["warnings"])
    assert "version: 1" in data_model.MODEL_FILE.read_text()


def test_a_failed_graph_stamp_that_is_not_a_store_failure_exits_1(env, monkeypatch, tmp_path):
    _stamping(env, monkeypatch, tmp_path, RuntimeError("bug"))
    result = _run("migrate", "temporal", ok=False)
    assert result.exit_code == 1 and json.loads(result.stdout)["stamped"] is False


def test_the_rerun_is_not_blocked_by_its_own_stamped_file_and_stamps_the_graph(env, monkeypatch, tmp_path):
    calls = _stamping(env, monkeypatch, tmp_path, None)
    data_model.stamp(1)  # left by the failed run, uncommitted

    def git(root, args):
        if args[0] == "rev-parse":
            return _Out(0)
        return _Out(0, " M data_model.yaml" if any("data_model.yaml" in a for a in args) else "")
    monkeypatch.setattr(pack_cli, "_git", git)
    monkeypatch.setattr(steps_mod, "STEPS", [Fake("one", 0).step([])])
    out = json.loads(_run("migrate", "temporal").stdout)
    assert out["stamped"] is True and calls == [1] and "data_model.yaml" in out["next"]


def test_not_stamped_reports_what_is_pending(env, monkeypatch, tmp_path):
    steps = [Fake("one", 1).step([])]
    monkeypatch.setattr(steps_mod, "STEPS", steps)
    monkeypatch.setattr(steps_mod, "stamp_if_done", lambda ctx: {"pending": [{"step": "one"}]})
    out = json.loads(_run("migrate", "temporal").stdout)
    assert out["stamped"] is False and out["stamp_pending"] == [{"step": "one"}]
