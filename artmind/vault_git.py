"""Git commit per artmind-authored frontmatter change (docs/document-identity.md).

Push is opt-in and never fatal: a laptop offline mid-ingest, or a vault that
isn't a git repo at all, must not fail the ingest that triggered it — writing
the frontmatter is the operation that matters; recording it in git history is
a courtesy on top.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from paths import ARTMIND_VAULT_DIR
from utils.functions import load_env, run_command


def _vault_root() -> Path | None:
    if ARTMIND_VAULT_DIR is None or not ARTMIND_VAULT_DIR.is_dir():
        return None
    if not (ARTMIND_VAULT_DIR / ".git").exists():
        logger.debug("vault_git: {} is not a git repo, skipping commit", ARTMIND_VAULT_DIR)
        return None
    return ARTMIND_VAULT_DIR


def current_commit() -> str | None:
    """The vault's git HEAD sha at this moment — provenance (`_source_commit`),
    never identity. None when there's no vault, no git repo, or no commits yet."""
    vault = _vault_root()
    if vault is None:
        return None
    rc, out, _ = run_command("git rev-parse HEAD", cwd=vault)
    return out.strip() if rc == 0 else None


def commit_paths(paths: list[Path], message: str) -> bool:
    """Stage and commit `paths` (relative to the vault root or absolute
    inside it) in the vault repo. Returns True on an actual commit, False
    when there is nothing to commit or the vault isn't a git repo — never
    raises. Push is separate and opt-in (see `maybe_push`).
    """
    vault = _vault_root()
    if vault is None or not paths:
        return False

    rel_paths = []
    for p in paths:
        p = Path(p)
        try:
            rel_paths.append(str(p.relative_to(vault)) if p.is_absolute() else str(p))
        except ValueError:
            rel_paths.append(str(p))

    add_cmd = "git add -- " + " ".join(f'"{p}"' for p in rel_paths)
    rc, out, err = run_command(add_cmd, cwd=vault)
    if rc != 0:
        logger.warning("vault_git: git add failed ({}): {}", rc, err or out)
        return False

    # Nothing staged (an idempotent write produced byte-identical content) is
    # exactly the "nothing differs -> no-op" case, not an error.
    rc, out, _ = run_command("git diff --cached --quiet", cwd=vault)
    if rc == 0:
        return False

    rc, out, err = run_command(f'git commit -m "{message}"', cwd=vault)
    if rc != 0:
        logger.warning("vault_git: git commit failed ({}): {}", rc, err or out)
        return False
    logger.info("vault_git: committed {} file(s) — {}", len(rel_paths), message)
    return True


def maybe_push() -> None:
    """Push the vault's current branch, only when explicitly opted in via
    ARTMIND_VAULT_GIT_PUSH=1. Failures (no remote, offline, auth) log a
    warning and are otherwise swallowed — push is a courtesy, not a
    precondition for ingest to have succeeded."""
    env = load_env()
    if env.get("ARTMIND_VAULT_GIT_PUSH", "").strip() not in ("1", "true", "yes"):
        return
    vault = _vault_root()
    if vault is None:
        return
    rc, out, err = run_command("git push", cwd=vault)
    if rc != 0:
        logger.warning("vault_git: push failed (non-fatal): {}", err or out)
    else:
        logger.info("vault_git: pushed")
