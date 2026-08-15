"""A0 packaging split: a core-only install (no `[ingest]` extra) must still
import the query/serve/CLI code, and document-ingest commands must fail with a
friendly hint rather than a bare ModuleNotFoundError.

The extra bundles docling + langchain-text-splitters + openpyxl. These tests
simulate its absence with `sys.modules[name] = None` (which makes `import name`
raise ImportError) in a fresh subprocess, and lock the pyproject boundary so the
three libs can't silently drift back into core `[project.dependencies]`.
"""
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


# ── import safety under a simulated core install ────────────────────────────

def test_core_load_path_imports_without_the_ingest_extra():
    """Blocking the extra's libs, the core import chain still loads."""
    script = (
        "import sys; "
        "sys.modules['langchain_text_splitters'] = None; "
        "sys.modules['openpyxl'] = None; "
        "sys.modules['docling'] = None; "
        "import artmind.cli; "
        "import artmind.ingest; "
        "import artmind.structured.duckdb_adapter; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ── friendly error when a chunking command runs core-only ───────────────────

def test_ingest_sync_prints_friendly_hint_when_extra_missing(monkeypatch, tmp_path):
    import importlib.util as _util
    import artmind.cli as cli

    real_find_spec = _util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "langchain_text_splitters":
            return None
        return real_find_spec(name, *args, **kwargs)

    # _require_ingest_extra does `import importlib.util` then calls find_spec,
    # so patching the stdlib module's attribute is what it actually resolves.
    monkeypatch.setattr(_util, "find_spec", fake_find_spec)

    # The argument is click.Path(exists=True), so the file must exist for the
    # callback (and thus the guard) to run — otherwise Click exits 2 first.
    doc = tmp_path / "x.md"
    doc.write_text("hello", encoding="utf-8")

    result = CliRunner().invoke(
        cli.cli, ["ingest", "sync", str(doc), "--domain", "general"]
    )
    assert result.exit_code != 0
    assert "artmind9[ingest]" in result.output


def test_require_ingest_extra_is_a_noop_when_present():
    """With the dev group installed the guard must not fire."""
    from artmind.cli import _require_ingest_extra

    _require_ingest_extra()  # should not raise


# ── packaging boundary lock ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pyproject():
    with open(_PYPROJECT, "rb") as f:
        return tomllib.load(f)


def _names(specs):
    # "langchain-text-splitters>=1.1.2" -> "langchain-text-splitters"
    out = set()
    for s in specs:
        name = s.split(">=")[0].split("==")[0].split("[")[0].split(">")[0].split("<")[0]
        out.add(name.strip())
    return out


def test_ingest_libs_are_in_the_ingest_extra_not_core(pyproject):
    core = _names(pyproject["project"]["dependencies"])
    extra = _names(pyproject["project"]["optional-dependencies"]["ingest"])

    for lib in ("docling", "langchain-text-splitters", "openpyxl"):
        assert lib in extra, f"{lib} must live in the [ingest] extra"
        assert lib not in core, f"{lib} must NOT be a core dependency"


def test_query_libs_stay_in_core(pyproject):
    core = _names(pyproject["project"]["dependencies"])
    for lib in ("duckdb", "ollama", "neo4j"):
        assert lib in core, f"{lib} must remain a core dependency"
