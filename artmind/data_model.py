"""The data-model gate (spec 2026-11-15 §8.1).

The run folder's ``data_model.yaml`` records the version the vault's data is at.
Every write path calls :func:`require_current`; read paths call
:func:`warn_if_behind` once per process. The gate is not temporal-specific: a
later migration pack bumps ``CURRENT`` and reuses it.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import click
import yaml

from paths import ARTMIND_HOME, DB_PATH, LOGS_DIR

CURRENT = 1
MODEL_FILE: Path = ARTMIND_HOME / "data_model.yaml"
_warned = False


class DataModelBehind(click.ClickException):
    """A write was attempted on data older than the code expects."""


def stamp(version: int) -> None:
    """Record `version` as the data's model, atomically."""
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=LOGS_DIR, prefix="data_model.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": int(version)}, fh)
        os.replace(tmp, MODEL_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def recorded(write: bool = True) -> int:
    """The model version the data is at.

    An absent file means 0 when the data dir already holds a registry DB, and
    ``CURRENT`` otherwise (written at once unless ``write`` is False): a fresh
    vault starts current.
    """
    if MODEL_FILE.exists():
        try:
            data = yaml.safe_load(MODEL_FILE.read_text(encoding="utf-8")) or {}
            return int(data["version"])
        except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
            return 0
    if Path(DB_PATH).exists():
        return 0
    if write:
        stamp(CURRENT)
    return CURRENT


def _message(command: str, have: int) -> str:
    return (f"{command}: this vault's data is at model {have}, the code needs {CURRENT}. "
            "Run: artmind migrate status")


def require_current(command: str) -> None:
    """Refuse a write on data that predates the code."""
    have = recorded()
    if have != CURRENT:
        raise DataModelBehind(_message(command, have))


def warn_if_behind() -> None:
    """Print the gate's text once per process on stderr; never fails, never writes."""
    global _warned
    if _warned:
        return
    try:
        have = recorded(write=False)
    except Exception:  # noqa: BLE001 - a warning must not break a read
        return
    if have != CURRENT:
        _warned = True
        print(f"artmind: {_message('this command', have)}", file=sys.stderr)


#: Commands exempt from the gate: the migration pack is how a vault gets current.
EXEMPT_PREFIX = "migrate"


def gate_commands(root: click.Group, write_paths) -> None:
    """Wrap every leaf command of `root`: a write path (`write_paths`, space-separated
    Click paths) calls :func:`require_current`; any other command except `migrate ...`
    calls :func:`warn_if_behind`. Group callbacks and `--help` never reach a leaf."""
    import functools

    write_paths = set(write_paths)

    def walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            path = f"{prefix} {name}".strip()
            if isinstance(cmd, click.Group):
                walk(cmd, path)
                if not cmd.invoke_without_command:
                    continue
            if path == "migrate" or path.startswith("migrate ") or cmd.callback is None:
                continue
            if getattr(cmd.callback, "_data_model_gated", False):
                continue
            inner = cmd.callback
            if path in write_paths:
                def run(*a, _inner=inner, _path=path, **kw):
                    require_current(_path)
                    return _inner(*a, **kw)
            else:
                def run(*a, _inner=inner, **kw):
                    warn_if_behind()
                    return _inner(*a, **kw)
            run = functools.wraps(inner)(run)
            run._data_model_gated = True  # type: ignore[attr-defined]
            cmd.callback = run

    walk(root, "")
