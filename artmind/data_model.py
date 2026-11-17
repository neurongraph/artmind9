"""The data-model gate (spec 2026-11-15 §8.1).

Two records say which model a vault's data is at, because the data lives in two
places that are migrated separately:

* the run folder's ``data_model.yaml`` -- committed with the vault, it describes
  the committed files (staging, curation, ``same_as.yaml``);
* a ``(:DataModel {id: 'artmind', version})`` node in this machine's Neo4j graph.

Every write path calls :func:`require_current`; read paths call
:func:`warn_if_behind`. The gate is not temporal-specific: a later migration pack
bumps ``CURRENT`` and reuses it. A read never writes either record.
"""
from __future__ import annotations

import functools
import os
import sys
import tempfile
from pathlib import Path

import click
import yaml

from paths import ARTMIND_HOME, CURATION_DIR, DB_PATH, KG_DIR

CURRENT = 1
MODEL_FILE: Path = ARTMIND_HOME / "data_model.yaml"
SAME_AS_FILE: Path = ARTMIND_HOME / "same_as.yaml"
#: Key on the root click context's meta: the behind-message a read carries into its JSON `warnings`.
WARNING_META = "artmind.data_model_warning"
GRAPH_TIMEOUT_S = 2.0

_warned = False
_graph_cache: dict = {}


class DataModelBehind(click.ClickException):
    """A write was attempted on data the code cannot read."""


# ── the committed file ───────────────────────────────────────────────────────

def stamp_to(model_file: Path, version: int) -> None:
    """Write `version` to `model_file` atomically (temp file beside it, `os.replace`)."""
    model_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=model_file.parent, prefix=".data_model.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"version": int(version)}, fh)
        os.replace(tmp, model_file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def stamp(version: int) -> None:
    """Record `version` as the committed data's model."""
    stamp_to(MODEL_FILE, version)


def is_fresh(model_file: Path | None = None, db_path: Path | None = None, kg_dir: Path | None = None,
             same_as: Path | None = None, curation_dir: Path | None = None) -> bool:
    """True when the vault holds nothing a model could apply to: no record, no registry,
    no staged folder (`kg/<domain>/<doc>`), no `same_as.yaml`, no curation record. A vault
    cloned to a second machine has the committed ones of these but not the (gitignored)
    registry, so it is not fresh."""
    model_file = MODEL_FILE if model_file is None else model_file
    db_path = DB_PATH if db_path is None else db_path
    kg_dir = KG_DIR if kg_dir is None else kg_dir
    same_as = SAME_AS_FILE if same_as is None else same_as
    curation_dir = CURATION_DIR if curation_dir is None else curation_dir
    if Path(model_file).exists() or Path(db_path).exists() or Path(same_as).exists():
        return False
    if Path(kg_dir).is_dir() and any(Path(kg_dir).glob("*/*")):
        return False
    if Path(curation_dir).is_dir() and any(Path(curation_dir).iterdir()):
        return False
    return True


def _file_state() -> tuple[str, int]:
    """`("ok", n)`, `("malformed", 0)` or `("absent", 0)`; never writes."""
    if not MODEL_FILE.exists():
        return "absent", 0
    try:
        data = yaml.safe_load(MODEL_FILE.read_text(encoding="utf-8")) or {}
        return "ok", int(data["version"])
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        return "malformed", 0


def recorded() -> int:
    """The model version the committed data is at: `CURRENT` for a fresh vault, else the
    file's version (0 when absent or unreadable). Never writes."""
    state, version = _file_state()
    if state == "absent":
        return CURRENT if is_fresh() else 0
    return version


def stamp_if_fresh(model_file: Path, db_path: Path, **paths: Path) -> None:
    """Stamp `model_file` current when the vault is fresh (see :func:`is_fresh`)."""
    if is_fresh(model_file, db_path, **paths):
        stamp_to(model_file, CURRENT)


# ── the graph's marker ───────────────────────────────────────────────────────

_READ_MARKER = "MATCH (m:DataModel {id: 'artmind'}) RETURN m.version AS version"
_POPULATED = ("RETURN EXISTS { MATCH (n) WHERE n:Document OR n:DocumentHistory OR n:Entity } "
              "AS populated")
_WRITE_MARKER = "MERGE (m:DataModel {id: 'artmind'}) SET m.version = $version"


def stamp_graph(session, version: int) -> None:
    """Record `version` as this graph's model (the pack's stamp step calls this)."""
    session.run(_WRITE_MARKER, version=int(version)).consume()
    _graph_cache["version"] = int(version)


def _open_session():
    from artmind.graph_query import neo4j_session

    return neo4j_session(timeout=GRAPH_TIMEOUT_S)


def read_graph_version(stamp_fresh: bool = False) -> int | None:
    """The graph's model: the marker's version; `CURRENT` for a graph with no
    Document/DocumentHistory/Entity nodes (marked at once when `stamp_fresh`); 0 for a
    populated graph without a marker; None when the graph cannot be reached (the command's
    own store check reports that). Cached for the process."""
    if "version" in _graph_cache:
        return _graph_cache["version"]
    try:
        with _open_session() as session:
            row = session.run(_READ_MARKER).single()
            if row is not None and row["version"] is not None:
                version = int(row["version"])
            elif session.run(_POPULATED).single()["populated"]:
                version = 0
            else:
                version = CURRENT
                if stamp_fresh:
                    stamp_graph(session, CURRENT)
    except Exception:  # noqa: BLE001 - unreachable store: unknown, not behind
        _graph_cache["version"] = None
        return None
    if version != CURRENT or stamp_fresh:
        _graph_cache["version"] = version
    return version


def _file_problem(command: str) -> str | None:
    state, version = _file_state()
    if state == "malformed":
        return (f"{command}: data_model.yaml is unreadable. Restore it from git, "
                "or run: artmind migrate status")
    if state == "absent":
        version = CURRENT if is_fresh() else 0
    if version > CURRENT:
        return (f"{command}: this vault's data is at model {version}, newer than this artmind "
                f"(model {CURRENT}). Upgrade artmind.")
    if version < CURRENT:
        return (f"{command}: this vault's data is at model {version}, the code needs {CURRENT}. "
                "Run: artmind migrate status")
    return None


def _graph_problem(command: str, version: int | None) -> str | None:
    if version is None or version == CURRENT:
        return None
    if version > CURRENT:
        return (f"{command}: this graph's data is at model {version}, newer than this artmind "
                f"(model {CURRENT}). Upgrade artmind.")
    return (f"{command}: this graph's data is at model {version}, the code needs {CURRENT}. "
            "Run: artmind migrate status")


def _dry_run() -> bool:
    ctx = click.get_current_context(silent=True)
    return bool(ctx is not None and ctx.params.get("dry_run"))


def require_current(command: str) -> None:
    """Refuse a write when the committed data or this graph predates the code. A first
    write on a truly fresh vault (or graph) records `CURRENT`; a dry run stamps nothing."""
    problem = _file_problem(command)
    if problem:
        raise DataModelBehind(problem)
    dry = _dry_run()
    if not dry and _file_state()[0] == "absent":
        stamp(CURRENT)  # is_fresh held above
    problem = _graph_problem(command, read_graph_version(stamp_fresh=not dry))
    if problem:
        raise DataModelBehind(problem)


def warn_if_behind() -> None:
    """A read: say once on stderr (and in the command's JSON `warnings`) that a record is
    behind. Never fails, never writes."""
    try:
        text = _file_problem("this command") or _graph_problem("this command", read_graph_version())
    except Exception:  # noqa: BLE001 - a warning must not break a read
        return
    if not text:
        return
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        ctx.find_root().meta[WARNING_META] = text
    global _warned
    if not _warned:
        _warned = True
        print(f"artmind: {text}", file=sys.stderr)


#: Commands exempt from the gate: the migration pack is how a vault gets current.
EXEMPT_PREFIX = "migrate"


def gate_commands(root: click.Group, write_paths) -> None:
    """Wrap every leaf command of `root`: a write path (`write_paths`, space-separated
    Click paths) calls :func:`require_current`; any other command except `migrate ...`
    calls :func:`warn_if_behind`. Group callbacks warn only when they run on their own."""
    write_paths = set(write_paths)

    def walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            path = f"{prefix} {name}".strip()
            is_group = isinstance(cmd, click.Group)
            if is_group:
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
                def run(*a, _inner=inner, _group=is_group, **kw):
                    ctx = click.get_current_context(silent=True)
                    if not (_group and ctx is not None and ctx.invoked_subcommand):
                        warn_if_behind()
                    return _inner(*a, **kw)
            run = functools.wraps(inner)(run)
            run._data_model_gated = True  # type: ignore[attr-defined]
            cmd.callback = run

    walk(root, "")
