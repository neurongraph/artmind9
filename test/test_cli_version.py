"""`artmind --version` (spec 2026-09-30 §6 X1): the Obsidian plugin reads it
to refuse an artmind older than the one it was built against."""
import functools
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from click.testing import CliRunner

from artmind.cli import cli

REPO = Path(__file__).resolve().parent.parent


@functools.cache
def _pyproject_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_version_prints_the_package_version():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output == f"artmind {_pyproject_version()}\n"


def test_version_is_one_parseable_line():
    """The plugin matches `^artmind (\\d+)\\.(\\d+)\\.(\\d+)$` on stdout."""
    result = CliRunner().invoke(cli, ["--version"])

    assert re.fullmatch(r"artmind \d+\.\d+\.\d+\n", result.output)


def test_version_through_the_console_entry_point():
    """What the plugin actually runs: the `artmind` console script, whose
    `_entry.main` proxies only `query` calls to the serve daemon."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv = ['artmind', '--version']; "
         "from artmind._entry import main; main()"],
        capture_output=True, text=True, cwd=REPO,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"artmind {_pyproject_version()}\n"
