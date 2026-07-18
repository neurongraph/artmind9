"""The ACP path must stay import-clean of claude_agent_sdk (see CLAUDE.md /
docs/admin-ui-plan.md Task 5.1) so ACP-only hosts don't need the SDK installed.

Runs in a fresh subprocess — importing claude_agent_sdk anywhere else in the
test session (e.g. test_webui_events.py) would otherwise make this check
depend on collection order.
"""
import subprocess
import sys


def _sdk_absent_after(import_statement: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; {import_statement}; print('claude_agent_sdk' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == "False"


def test_importing_acp_events_does_not_pull_in_claude_sdk():
    assert _sdk_absent_after("import artmind.webui.backends.acp_events")


def test_creating_acp_backend_does_not_pull_in_claude_sdk():
    assert _sdk_absent_after(
        "from artmind.webui.backends import create_backend; create_backend('acp')"
    )
