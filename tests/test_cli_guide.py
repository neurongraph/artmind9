import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_cli_guide_is_up_to_date() -> None:
    """Fail if docs/artmind-cli-guide.html is out of sync with artmind/cli.py."""
    result = subprocess.run(
        [sys.executable, "scripts/generate_cli_guide.py", "--check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"docs/artmind-cli-guide.html is stale.\n"
            f"Run: uv run python scripts/generate_cli_guide.py\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
