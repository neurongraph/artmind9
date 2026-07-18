"""Pull KG JSON sub-folders from an external git repo into the local KG directory."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from paths import KG_DIR

# Transport schemes git is permitted to use for clone/fetch operations here.
# Deliberately excludes `ext` (arbitrary local command execution via
# `ext::sh -c ...`), `file`, `fd`, and anything else not in this list. See
# `git help clone` / `git help -c protocol.allow` for the GIT_ALLOW_PROTOCOL
# semantics this enforces. Plain `http` is also excluded — nothing in this
# codebase has a legitimate use for an unencrypted, trivially MITM-able git
# remote; every real example uses `https://` or `git@...` (ssh). If an
# internal http-only git server ever becomes a real requirement, add `http`
# back here with a comment documenting that need.
_GIT_ALLOWED_PROTOCOLS = "https:ssh:git"


def _reject_leading_dash(value: str, label: str) -> None:
    """Reject a value that could be misread as a CLI option by git/ssh."""
    if value.startswith("-"):
        raise RuntimeError(f"Invalid {label} '{value}': must not start with '-'")


def _rewrite_url_with_token(repo_url: str) -> str:
    """If GITHUB_TOKEN is set and the URL is HTTPS, inject the token for auth."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return repo_url
    if repo_url.startswith("https://"):
        # https://github.com/... → https://<token>@github.com/...
        return repo_url.replace("https://", f"https://{token}@", 1)
    return repo_url


def _detect_conflicts(incoming_names: list[str], target_dir: Path) -> list[str]:
    """Return names from incoming_names that already exist as sub-dirs in target_dir."""
    if not target_dir.exists():
        return []
    existing = {d.name for d in target_dir.iterdir() if d.is_dir()}
    return sorted(name for name in incoming_names if name in existing)


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a git command, raising RuntimeError on failure.

    Restricts git to a safe allowlist of URL transport protocols via
    GIT_ALLOW_PROTOCOL so a caller-supplied URL can't invoke the `ext::`
    transport (or similar) to run arbitrary local commands.
    """
    env = os.environ.copy()
    env["GIT_ALLOW_PROTOCOL"] = _GIT_ALLOWED_PROTOCOLS
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git {' '.join(args)} failed: {e.stderr.strip()}")


def _sparse_clone(repo_url: str, repo_path: str) -> tuple[Path, Path]:
    """Sparse-checkout a single sub-path from a repo into a temp directory.

    Validates repo_url and repo_path before cloning: rejects values that
    could be misread as CLI options (see _reject_leading_dash), and rejects
    a repo_path that resolves outside the cloned repo (see the containment
    check below).

    Returns (content_dir, tmp_dir) where content_dir is the materialized
    repo_path and tmp_dir is the root temp directory for cleanup.
    """
    _reject_leading_dash(repo_url, "repo URL")
    _reject_leading_dash(repo_path, "repo path")

    url = _rewrite_url_with_token(repo_url)
    tmp_dir = Path(tempfile.mkdtemp(prefix="artmind_pull_"))
    clone_dir = tmp_dir / "repo"

    logger.info("Cloning {} (sparse) into {}", repo_url, tmp_dir)
    _run_git(["clone", "--no-checkout", "--depth=1", url, str(clone_dir)])
    _run_git(["sparse-checkout", "set", repo_path], cwd=clone_dir)
    _run_git(["checkout"], cwd=clone_dir)

    # Unlike _validate_artifact_segment (artmind/webui/dashboard_routes.py),
    # which rejects any '/' in a domain/doc value outright, repo_path is a
    # legitimate multi-segment sparse-checkout path (e.g. "data/kg/sales"),
    # so segment-rejection isn't an option here. Instead, resolve the
    # symlink-free absolute path and check it's still contained within the
    # clone dir, which catches traversal via "../.." or an absolute path.
    content_dir = clone_dir / repo_path
    resolved_content_dir = content_dir.resolve()
    if not resolved_content_dir.is_relative_to(clone_dir.resolve()):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Path '{repo_path}' escapes the cloned repository")

    if not content_dir.is_dir():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Path '{repo_path}' not found in repository")

    return content_dir, tmp_dir


def pull_kg(repo_url: str, repo_path: str, domain: str) -> dict:
    """Pull KG JSON sub-folders from an external git repo into local data/kg/<domain>/.

    Returns a summary dict with keys: pulled_count, domain, repo_url, conflicts.
    Raises RuntimeError on git failures or conflicts.
    """
    content_dir, tmp_root = _sparse_clone(repo_url, repo_path)

    try:
        # Find document sub-folders (contain document.json)
        doc_dirs = sorted(
            d for d in content_dir.iterdir()
            if d.is_dir() and (d / "document.json").exists()
        )
        if not doc_dirs:
            raise RuntimeError(
                f"No document sub-folders with document.json found at '{repo_path}' in the repository"
            )

        incoming_names = [d.name for d in doc_dirs]
        target_dir = KG_DIR / domain

        # Conflict check
        conflicts = _detect_conflicts(incoming_names, target_dir)
        if conflicts:
            raise RuntimeError(
                f"Pull aborted — {len(conflicts)} conflict(s) with existing local folders: "
                + ", ".join(conflicts)
            )

        # Copy
        target_dir.mkdir(parents=True, exist_ok=True)
        for doc_dir in doc_dirs:
            dest = target_dir / doc_dir.name
            shutil.copytree(doc_dir, dest)
            logger.info("  Copied {}", doc_dir.name)

        logger.info("Pulled {} document(s) into {}", len(doc_dirs), target_dir)
        return {
            "pulled_count": len(doc_dirs),
            "domain": domain,
            "repo_url": repo_url,
            "conflicts": [],
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
