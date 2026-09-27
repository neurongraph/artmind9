"""artmind.worker_pid.live_pid: one liveness check for the ingest worker's
pid file, shared by `vault sync`, the worker and the CLI (phase 1 review
finding 7)."""
import os

from artmind.worker_pid import live_pid


def _refuse(pid, sig):
    raise PermissionError(1, "Operation not permitted")


def test_no_pid_file_means_no_worker(tmp_path):
    assert live_pid(tmp_path / "worker.pid") is None


def test_an_unparseable_pid_file_means_no_worker(tmp_path):
    (tmp_path / "worker.pid").write_text("not a pid")
    assert live_pid(tmp_path / "worker.pid") is None


def test_a_live_pid_is_returned(tmp_path):
    (tmp_path / "worker.pid").write_text(f"{os.getpid()}\n")
    assert live_pid(tmp_path / "worker.pid") == os.getpid()


def test_a_dead_pid_means_no_worker(tmp_path):
    (tmp_path / "worker.pid").write_text("999999999")
    assert live_pid(tmp_path / "worker.pid") is None


def test_a_pid_owned_by_another_user_counts_as_alive(tmp_path, monkeypatch):
    (tmp_path / "worker.pid").write_text("4242")
    monkeypatch.setattr(os, "kill", _refuse)
    assert live_pid(tmp_path / "worker.pid") == 4242


def test_the_worker_does_not_start_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(os, "kill", _refuse)

    assert worker._acquire_pid_file() is False
    assert pid_file.read_text() == "4242"


def test_the_worker_overwrites_a_stale_pid_file(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("999999999")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)

    assert worker._acquire_pid_file() is True
    assert pid_file.read_text() == str(os.getpid())


def test_the_cli_does_not_spawn_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.cli as cli

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(cli, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(os, "kill", _refuse)
    spawned = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: spawned.append(a))

    cli._ensure_worker_running()

    assert spawned == []
