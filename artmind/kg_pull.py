"""Pull KG JSON sub-folders from an external git repo into the local KG directory."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from paths import KG_DIR


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
    """Run a git command, raising RuntimeError on failure."""
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git {' '.join(args)} failed: {e.stderr.strip()}")


def _sparse_clone(repo_url: str, repo_path: str) -> tuple[Path, Path]:
    """Sparse-checkout a single sub-path from a repo into a temp directory.

    Returns (content_dir, tmp_dir) where content_dir is the materialized
    repo_path and tmp_dir is the root temp directory for cleanup.
    """
    url = _rewrite_url_with_token(repo_url)
    tmp_dir = Path(tempfile.mkdtemp(prefix="artmind_pull_"))
    clone_dir = tmp_dir / "repo"

    logger.info("Cloning {} (sparse) into {}", repo_url, tmp_dir)
    _run_git(["clone", "--no-checkout", "--depth=1", url, str(clone_dir)])
    _run_git(["sparse-checkout", "set", repo_path], cwd=clone_dir)
    _run_git(["checkout"], cwd=clone_dir)

    content_dir = clone_dir / repo_path
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
