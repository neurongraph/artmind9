"""artmind.worker_pid.live_pid: one liveness check for the ingest worker's
pid file, shared by `vault sync`, the worker and the CLI (phase 1 review
finding 7, and its follow-up: liveness is the pid file's flock, not the pid
number -- a recycled pid must not read as alive, and a bad pid value must
never reach `os.kill`)."""
import fcntl
import os

import pytest

from artmind.worker_pid import live_pid


def _forbid_bad_pid(pid, sig):
    if pid <= 0:
        raise AssertionError(f"os.kill must never be called with pid={pid}")
    raise PermissionError(1, "Operation not permitted")


def test_no_pid_file_means_no_worker(tmp_path):
    assert live_pid(tmp_path / "worker.pid") is None


def test_an_unparseable_unlocked_pid_file_means_no_worker(tmp_path):
    (tmp_path / "worker.pid").write_text("not a pid")
    assert live_pid(tmp_path / "worker.pid") is None


@pytest.mark.parametrize("bad_pid", ["0", "-1", "2147483648"])
def test_a_bad_pid_value_in_an_unlocked_file_means_no_worker(tmp_path, monkeypatch, bad_pid):
    """0, -1 and an overflowing pid used to report "alive" under the old
    `kill(pid, 0)` check (own process group, "any process", and an uncaught
    OverflowError respectively). None of them are ever live workers here:
    nobody holds the file's lock, so `live_pid` never even inspects the
    pid, and `os.kill` is never called."""
    (tmp_path / "worker.pid").write_text(bad_pid)
    monkeypatch.setattr(os, "kill", _forbid_bad_pid)
    assert live_pid(tmp_path / "worker.pid") is None


def test_a_live_pid_is_returned(tmp_path):
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text(str(os.getpid()))
    # flock is per open-file-description, so a second handle in the same
    # process can hold the lock the way a real worker's own handle would.
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        assert live_pid(pid_file) == os.getpid()
    finally:
        holder.close()


def test_a_locked_file_with_a_bad_pid_returns_a_positive_sentinel(tmp_path):
    """The lock is what proves liveness; a nonsense pid in the file must not
    turn into a crash or a false "not alive"."""
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("-1")
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        result = live_pid(pid_file)
        assert result is not None and result > 0
    finally:
        holder.close()


def test_a_recycled_pid_in_an_unlocked_file_is_not_alive(tmp_path):
    """The pid file names our own live pid, but nothing holds its lock --
    e.g. after a reboot, the number just happens to have been recycled.
    That must read as no worker, not as "alive"."""
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text(str(os.getpid()))
    assert live_pid(pid_file) is None


def test_the_worker_does_not_start_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(worker, "_pid_file_handle", None)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)  # skip the real retry delay
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        assert worker._acquire_pid_file() is False
        assert pid_file.read_text() == "4242"
    finally:
        holder.close()


def test_the_worker_overwrites_a_stale_pid_file(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("999999999")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(worker, "_pid_file_handle", None)

    assert worker._acquire_pid_file() is True
    try:
        assert pid_file.read_text() == str(os.getpid())
        # The worker now holds the lock itself, so a second liveness check
        # sees it as alive.
        assert live_pid(pid_file) == os.getpid()
    finally:
        worker._release_pid_file()


def test_worker_cleanup_does_not_unlink_the_pid_file(tmp_path, monkeypatch):
    """A worker exiting must not unlink its pid file: the lock lives on the
    inode, but `live_pid` finds the file by path, so unlinking would let the
    next process open a fresh inode, take an uncontended lock on it, and
    register as "no worker" while the old inode's lock is still held --
    two workers end up running at once."""
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(worker, "_pid_file_handle", None)

    assert worker._acquire_pid_file() is True
    worker._release_pid_file()

    assert pid_file.exists()
    # The handle is closed, so the lock is gone too -- the file is simply
    # stale, and the next worker's `_acquire_pid_file` overwrites it in place.
    assert live_pid(pid_file) is None


def test_acquire_retries_the_lock_before_giving_up(tmp_path, monkeypatch):
    """`live_pid` briefly takes the lock to probe liveness. If a starting
    worker's `LOCK_NB` attempt lands inside that window it must retry, not
    immediately conclude another worker is running."""
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(worker, "_pid_file_handle", None)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    real_flock = fcntl.flock
    calls = {"n": 0}

    def _flaky_flock(fd, op):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise BlockingIOError()
        return real_flock(fd, op)

    monkeypatch.setattr(worker.fcntl, "flock", _flaky_flock)

    try:
        assert worker._acquire_pid_file() is True
        assert calls["n"] == 3
    finally:
        worker._release_pid_file()


def test_the_cli_does_not_spawn_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.cli as cli

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(cli, "WORKER_PID_FILE", pid_file)
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    spawned = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    try:
        cli._ensure_worker_running()
    finally:
        holder.close()

    assert spawned == []
