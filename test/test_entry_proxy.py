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


def test_proxies_query_when_daemon_alive(monkeypatch, capsys):
    responses = [
        _response({"service": "artmind", "status": "ok"}),
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
        _response({"service": "artmind", "status": "ok"}),
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
