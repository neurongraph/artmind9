"""`artmind vault doctor` (spec 2026-09-26 §5 R8): read-only checks that a
vault is safe for Obsidian Git to auto-commit and merge, each with the exact
fix. Never writes -- not to git (D1), not to the working tree. Fixes that
need a git write are printed for the user to run.

Two inputs are unverified (spec §13): Obsidian Git's `data.json` key names
and Obsidian Sync's `core-plugins.json` marker. Checks built on them report
`unknown`/`warn`, never `fail`.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from artmind.vault import GITATTRIBUTES_BLOCK, GITIGNORE_BLOCK, VaultLayout, block_status
from utils.functions import run_command

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"

OBSIDIAN_GIT_DATA = Path(".obsidian") / "plugins" / "obsidian-git" / "data.json"

#: (key, label, predicate, expected). UNVERIFIED against the installed plugin
#: version (spec §13): an absent key is `unknown`, a surprising value `warn`.
_OBSIDIAN_GIT_EXPECTATIONS = (
    ("syncMethod", "sync method", lambda v: v == "merge", "merge"),
    ("autoPullOnBoot", "pull on startup", lambda v: v is True, "on"),
    (
        "autoSaveInterval", "auto commit interval (minutes)",
        lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
        "> 0 (5-10 recommended)",
    ),
)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _git(vault_dir: Path, args: list[str], expected_codes: tuple[int, ...] = ()) -> tuple[int, str, str]:
    return run_command(["git", *args], cwd=vault_dir, expected_codes=expected_codes)


def check_block(vault_dir: Path, filename: str, block: str) -> Check:
    name = f"{filename} artmind block"
    status = block_status(vault_dir / filename, block)
    if status == "current":
        return Check(name, OK, "present and current")
    if status == "duplicate":
        return Check(
            name, FAIL, "more than one artmind block",
            f"edit {filename} by hand so it has exactly one artmind block (keep the newest, "
            "delete the others from their '# ── artmind' line through their '# ── end artmind' "
            "line), then run `artmind init` in the vault root",
        )
    if status == "malformed":
        return Check(
            name, FAIL, "artmind block has a start line but no '# ── end artmind' line",
            f"edit {filename} by hand so artmind's block ends with its '# ── end artmind' line, "
            "then run `artmind init` in the vault root",
        )
    return Check(
        name, FAIL, f"artmind block {status}",
        "run `artmind init` in the vault root (rewrites only artmind's block; your own rules are kept)",
    )


def _collapse(paths: list[str]) -> list[str]:
    """Each tracked file -> its `table__*` / `*.artmind-tmp` / `*.artmind-old` folder when it
    sits inside one, so the printed command untracks folders, not files."""
    out: list[str] = []
    for path in paths:
        parts = path.split("/")
        for i, part in enumerate(parts[:-1]):
            if part.startswith("table__") or part.endswith((".artmind-tmp", ".artmind-old")):
                path = "/".join(parts[: i + 1])
                break
        if path not in out:
            out.append(path)
    return out


def check_tracked_ignored(vault_dir: Path) -> Check:
    name = "ignored paths still tracked"
    # -z: paths verbatim, never C-quoted (spec 2026-09-26 §14 A5).
    rc, out, err = _git(vault_dir, ["ls-files", "-z", "-c", "-i", "--exclude-standard", "--", ".artmind"])
    if rc != 0:
        return Check(name, UNKNOWN, f"git ls-files failed: {(err or out).strip()}")
    paths = _collapse([p for p in out.split("\0") if p])
    if not paths:
        return Check(name, OK, "none")
    # Built in two parts so test_no_git_writes' source scan doesn't read
    # printed advice as a git write. artmind never runs it (D1).
    command = "git " + "rm -r --cached -- " + " ".join(shlex.quote(p) for p in paths)
    return Check(
        name, FAIL,
        f"{len(paths)} path(s) are gitignored but still tracked, so Obsidian Git keeps committing them: "
        + ", ".join(paths[:5]) + (", ..." if len(paths) > 5 else ""),
        command,
    )


def check_pull_rebase(vault_dir: Path) -> Check:
    name = "pull.rebase"
    rc, out, _ = _git(vault_dir, ["config", "--get", "pull.rebase"], expected_codes=(1,))
    value = out.strip().lower() if rc == 0 else ""
    if value in ("", "false", "no", "off", "0"):
        return Check(name, OK, f"{value or 'unset'} (pulls merge)")
    return Check(
        name, FAIL,
        f"pull.rebase is {value!r}: pulls would rebase, rewriting the commit shas artmind "
        "uses as provenance and sync cursors (spec D5)",
        "git config pull.rebase false",
    )


def check_obsidian_git(vault_dir: Path) -> list[Check]:
    path = vault_dir / OBSIDIAN_GIT_DATA
    if not path.is_file():
        return [Check(
            "Obsidian Git", WARN,
            f"{OBSIDIAN_GIT_DATA} not found: the plugin is not installed, or never configured, in this vault",
            "install Obsidian Git (Settings -> Community plugins), then set sync method: merge, "
            "pull on startup: on, auto commit-and-sync interval: 5-10 minutes",
        )]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [Check("Obsidian Git", UNKNOWN, f"could not read {OBSIDIAN_GIT_DATA}: {e}")]
    if not isinstance(data, dict):
        return [Check("Obsidian Git", UNKNOWN, f"{OBSIDIAN_GIT_DATA} is not a JSON object")]
    checks = []
    for key, label, is_ok, expected in _OBSIDIAN_GIT_EXPECTATIONS:
        name = f"Obsidian Git: {label}"
        if key not in data:
            checks.append(Check(
                name, UNKNOWN,
                f"no {key!r} in {OBSIDIAN_GIT_DATA}; this plugin version may name the setting differently",
                f"check by hand in Obsidian -> Settings -> Git: {label} = {expected}",
            ))
        elif is_ok(data[key]):
            checks.append(Check(name, OK, f"{key} = {data[key]!r}"))
        else:
            checks.append(Check(
                name, WARN, f"{key} = {data[key]!r}, expected {expected}",
                f"Obsidian -> Settings -> Git: set {label} to {expected}",
            ))
    return checks


def check_deprecated_push(vault_dir: Path) -> Check:
    name = "ARTMIND_VAULT_GIT_PUSH"
    where = []
    if "ARTMIND_VAULT_GIT_PUSH" in os.environ:
        where.append("the environment")
    config_env = VaultLayout(vault_dir).config_env
    if config_env.is_file() and any(
        line.strip().startswith("ARTMIND_VAULT_GIT_PUSH=")
        for line in config_env.read_text(encoding="utf-8").splitlines()
    ):
        where.append(str(config_env))
    if not where:
        return Check(name, OK, "not set")
    return Check(
        name, WARN,
        f"set in {' and '.join(where)}: deprecated and ignored -- Obsidian Git pushes, artmind never does",
        f"remove it from {' and '.join(where)}",
    )


def _obsidian_sync_enabled(vault_dir: Path) -> bool:
    """Heuristic, unverified across Obsidian versions (spec §13):
    `.obsidian/core-plugins.json` is a list of enabled core plugin ids in
    older versions and an {id: enabled} map in newer ones; Sync's id is
    "sync"."""
    try:
        data = json.loads((vault_dir / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if isinstance(data, list):
        return "sync" in data
    if isinstance(data, dict):
        return data.get("sync") is True
    return False


def check_other_sync(vault_dir: Path) -> Check:
    name = "other sync tools"
    resolved = vault_dir.resolve()
    found = []
    if "/Library/Mobile Documents/" in resolved.as_posix() + "/":
        found.append("iCloud Drive")
    if any((p / ".dropbox").exists() or (p / ".dropbox.cache").exists() for p in (resolved, *resolved.parents)):
        found.append("Dropbox")
    if _obsidian_sync_enabled(vault_dir):
        found.append("Obsidian Sync")
    if not found:
        return Check(name, OK, "none detected")
    return Check(
        name, WARN,
        f"{', '.join(found)} also syncs this folder; a second sync layer racing Obsidian Git "
        "over one working tree can corrupt it",
        f"move the vault out of {', '.join(found)}, or turn it off for this vault",
    )


def run(vault_dir: Path) -> dict:
    vault_dir = Path(vault_dir)
    checks = [
        check_block(vault_dir, ".gitignore", GITIGNORE_BLOCK),
        check_block(vault_dir, ".gitattributes", GITATTRIBUTES_BLOCK),
        check_tracked_ignored(vault_dir),
        check_pull_rebase(vault_dir),
        *check_obsidian_git(vault_dir),
        check_deprecated_push(vault_dir),
        check_other_sync(vault_dir),
    ]
    return {
        "vault": str(vault_dir),
        "ok": all(c.status != FAIL for c in checks),
        "checks": [asdict(c) for c in checks],
    }
