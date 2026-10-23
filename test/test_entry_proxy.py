import io
import json
from unittest.mock import patch

import pytest

from artmind import _entry


def _response(payload: dict):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode())


@pytest.fixture(autouse=True)
def _outside_any_vault(monkeypatch, tmp_path):
    """The checkout is no vault; pin that, so a stray ARTMIND_VAULT can't decide."""
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.delenv("ARTMIND_NO_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)


def _vault(root):
    (root / ".artmind").mkdir(parents=True)
    (root / ".artmind" / "vault.yaml").write_text("{}\n")
    return root


def _health_only(monkeypatch, health: dict):
    """The daemon answers /health with `health`; a /query call fails the test."""

    def _urlopen(request, *a, **k):
        url = request if isinstance(request, str) else request.full_url
        assert url.endswith("/health"), f"must not proxy to {url}"
        return _response(health)

    monkeypatch.setattr(_entry.urllib.request, "urlopen", _urlopen)


def test_proxies_to_the_serve_of_this_vault(monkeypatch, tmp_path, capsys):
    vault = _vault(tmp_path / "vault")
    monkeypatch.chdir(vault)
    responses = [
        _response({"service": "artmind", "status": "ok", "vault": str(vault), "pid": 1}),
        _response({"exit_code": 0, "output": "{}\n", "stderr": ""}),
    ]
    monkeypatch.setattr(_entry.urllib.request, "urlopen", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "domains-overview"])

    with pytest.raises(SystemExit) as excinfo:
        _entry.main()

    assert excinfo.value.code == 0
    assert not responses


def test_never_proxies_to_another_vaults_serve(monkeypatch, tmp_path, capsys):
    ours = _vault(tmp_path / "ours")
    other = _vault(tmp_path / "other")
    monkeypatch.chdir(ours)
    _health_only(monkeypatch, {"service": "artmind", "status": "ok", "vault": str(other), "pid": 1})
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()
    err = capsys.readouterr().err
    assert str(other) in err and "running in-process" in err


def test_a_vault_never_proxies_to_a_serve_of_no_vault(monkeypatch, tmp_path):
    monkeypatch.chdir(_vault(tmp_path / "ours"))
    _health_only(monkeypatch, {"service": "artmind", "status": "ok", "vault": None, "pid": 1})
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()


def test_never_proxies_to_a_serve_that_cannot_name_its_vault(monkeypatch, capsys):
    _health_only(monkeypatch, {"service": "artmind", "status": "ok"})
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()
    assert capsys.readouterr().err == ""


def test_a_bad_artmind_vault_runs_in_process(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTMIND_VAULT", str(tmp_path / "nowhere"))
    _health_only(monkeypatch, {"service": "artmind", "status": "ok", "vault": None, "pid": 1})
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()


def test_proxies_query_when_daemon_alive(monkeypatch, capsys):
    responses = [
        _response({"service": "artmind", "status": "ok", "vault": None, "pid": 1}),
        _response({"exit_code": 0, "output": '{"rows": []}\n', "stderr": ""}),
    ]
    monkeypatch.setattr(
        _entry.urllib.request, "urlopen", lambda *a, **k: responses.pop(0)
    )
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "domains-overview"])

    with pytest.raises(SystemExit) as excinfo:
        _entry.main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == '{"rows": []}\n'
    assert not responses


def test_proxy_propagates_exit_code_and_stderr(monkeypatch, capsys):
    responses = [
        _response({"service": "artmind", "status": "ok", "vault": None, "pid": 1}),
        _response({"exit_code": 2, "output": "", "stderr": "Usage: bad flag\n"}),
    ]
    monkeypatch.setattr(
        _entry.urllib.request, "urlopen", lambda *a, **k: responses.pop(0)
    )
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--bogus"])

    with pytest.raises(SystemExit) as excinfo:
        _entry.main()

    assert excinfo.value.code == 2
    assert capsys.readouterr().err == "Usage: bad flag\n"


def test_falls_back_to_cli_when_daemon_down(monkeypatch):
    def _refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(_entry.urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()


def test_non_query_commands_never_probe_daemon(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("daemon must not be probed for non-query commands")

    monkeypatch.setattr(_entry.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "domains", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()


def test_no_proxy_env_skips_daemon(monkeypatch):
    monkeypatch.setenv("ARTMIND_NO_PROXY", "1")

    def _boom(*a, **k):
        raise AssertionError("daemon must not be probed when ARTMIND_NO_PROXY is set")

    monkeypatch.setattr(_entry.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(_entry.sys, "argv", ["artmind", "query", "--help"])

    with patch("artmind.cli.cli") as cli_mock:
        _entry.main()

    cli_mock.assert_called_once()
