"""Tests for the `admin-ui` / `chat-ui` CLI commands.

These commands start a real uvicorn server when invoked, so tests only
introspect the registered Click command objects (name, options, defaults)
rather than calling `runner.invoke(...)`, which would block.
"""

from artmind.cli import cli


class TestAdminUi:
    def test_registered(self):
        assert "admin-ui" in cli.commands

    def test_options(self):
        params = {p.name: p for p in cli.commands["admin-ui"].params}
        assert set(params) == {"host", "port", "acp_cmd"}
        assert params["host"].default == "127.0.0.1"
        assert params["port"].default == 8379
        assert params["port"].type.name == "integer"
        assert params["acp_cmd"].default is None


class TestChatUi:
    def test_registered(self):
        assert "chat-ui" in cli.commands

    def test_options(self):
        params = {p.name: p for p in cli.commands["chat-ui"].params}
        assert set(params) == {"host", "port", "acp_cmd"}
        assert params["host"].default == "127.0.0.1"
        assert params["port"].default == 8378
        assert params["port"].type.name == "integer"
        assert params["acp_cmd"].default is None
