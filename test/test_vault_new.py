"""`artmind vault new` (spec docs/superpowers/specs/2026-10-02-vault-new-design.md)."""
from __future__ import annotations

import subprocess
import sys


def test_python_dash_m_artmind_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "artmind", "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "vault" in result.stdout
