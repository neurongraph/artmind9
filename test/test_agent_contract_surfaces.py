"""The admin console takes its contract from ARTMIND_AGENT_CONTRACT; a benchmark run from its own field."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from artmind.webui import profiles


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")


def _install_bundle_skill(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_AGENT_CWD", tmp_path / "vault")
    skill = tmp_path / "vault" / ".claude" / "skills" / "artmind-query-bundle"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("invented\n")


def test_admin_profile_follows_the_env(monkeypatch):
    from artmind.webui import app as app_mod

    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "bundle")
    assert app_mod.admin_profile().contract == "bundle"
    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "classic")
    assert app_mod.admin_profile() is profiles.ADMIN_PROFILE


def test_chat_ui_ignores_the_env(monkeypatch):
    from artmind.webui import app as app_mod

    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "bundle")
    assert app_mod.chat_profile() is profiles.QA_PROFILE


def test_a_bundle_benchmark_run_uses_the_bundle_profile_and_records_it(tmp_path, monkeypatch):
    from artmind import benchmark
    from artmind.traces import store

    seen = {}

    class FakeBackend:
        trace_context_path = None

        async def connect(self):
            pass

        async def query(self, prompt):
            pass

        async def receive_events(self):
            yield {"type": "text_delta", "text": "an invented answer"}
            yield {"type": "turn_done", "turns": 1, "duration_s": 0.1, "cost": 0.0}

        async def disconnect(self):
            pass

    def fake_create(name, profile, **kw):
        seen["skills"] = profile.skills
        return FakeBackend()

    monkeypatch.setattr("artmind.webui.backends.create_backend", fake_create)
    q = [{"seq": 1, "qid": "Q1", "title": "t", "question": "invented?", "eval_comment": None}]
    run_id = benchmark._create_benchmark_run(str(tmp_path / "q.md"), q, contract="bundle")
    asyncio.run(benchmark._run_benchmark(run_id))
    assert "artmind-query-bundle" in seen["skills"] and "artmind-query" not in seen["skills"]
    turns = [t for t in store.list_turns() if t.get("benchmark_run_id") == run_id]
    assert len(turns) == 1 and turns[0]["contract"] == "bundle"


def test_the_run_route_passes_the_contract_through(tmp_path, monkeypatch):
    from artmind.webui import benchmark_routes
    from artmind.webui.app import create_app

    _install_bundle_skill(tmp_path, monkeypatch)
    qfile = tmp_path / "q.md"
    qfile.write_text("x", encoding="utf-8")
    monkeypatch.setattr(benchmark_routes, "_parse_questions_file",
                        lambda path: [{"seq": 1, "qid": "Q1", "title": "t", "question": "q?", "eval_comment": None}])
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "run-1"

    async def fake_run(run_id):
        pass

    monkeypatch.setattr(benchmark_routes, "_create_benchmark_run", fake_create)
    monkeypatch.setattr(benchmark_routes, "_run_benchmark", fake_run)
    r = TestClient(create_app(admin_routes=True)).post(
        "/api/benchmark/runs", json={"path": str(qfile), "contract": "bundle"})
    assert r.status_code == 200
    assert captured["contract"] == "bundle"


def test_the_run_route_refuses_a_bundle_run_without_the_skill(tmp_path, monkeypatch):
    import paths

    from artmind.webui import benchmark_routes
    from artmind.webui.app import create_app

    monkeypatch.setattr(paths, "ARTMIND_AGENT_CWD", tmp_path / "empty")
    qfile = tmp_path / "q.md"
    qfile.write_text("x", encoding="utf-8")
    monkeypatch.setattr(benchmark_routes, "_parse_questions_file",
                        lambda path: [{"seq": 1, "qid": "Q1", "title": "t", "question": "q?", "eval_comment": None}])
    r = TestClient(create_app(admin_routes=True)).post(
        "/api/benchmark/runs", json={"path": str(qfile), "contract": "bundle"})
    assert r.status_code == 400 and "artmind init" in r.json()["detail"]


def test_the_run_route_refuses_an_unknown_contract(tmp_path, monkeypatch):
    from artmind.webui.app import create_app

    qfile = tmp_path / "q.md"
    qfile.write_text("x", encoding="utf-8")
    r = TestClient(create_app(admin_routes=True)).post(
        "/api/benchmark/runs", json={"path": str(qfile), "contract": "fast"})
    assert r.status_code == 400


def test_a_streamed_admin_turn_records_the_app_contract(tmp_path):
    from artmind.traces import store
    from artmind.webui.app import create_app
    from artmind.webui.sessions import SessionRegistry

    class Fake:
        def __init__(self, ctx):
            self.trace_context_path = ctx

        async def connect(self): pass
        async def disconnect(self): pass
        async def query(self, prompt): pass
        async def interrupt(self): pass

        async def receive_events(self):
            yield {"type": "text_delta", "text": "Hi"}
            yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01, "_trace": {"usage": {}, "model": "m"}}

    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    registry = SessionRegistry(client_factory=lambda name: Fake(ctx))
    client = TestClient(create_app(registry=registry, admin_routes=True, trace_turns=True, contract="bundle"))
    client.post("/api/chat", json={"conversation_id": "s1", "prompt": "Who?"})
    (turn,) = store.list_turns()
    assert turn["contract"] == "bundle"


def test_run_admin_ui_exits_when_the_bundle_skill_is_missing(tmp_path, monkeypatch, capsys):
    import paths

    from artmind.catalogue import refresh
    from artmind.webui import app as app_mod

    monkeypatch.setattr(paths, "ARTMIND_AGENT_CWD", tmp_path / "empty")
    monkeypatch.setattr(refresh, "enable_background", lambda: None)
    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "bundle")
    with pytest.raises(SystemExit) as e:
        app_mod.run_admin_ui()
    assert e.value.code == 1 and "artmind init" in capsys.readouterr().err
