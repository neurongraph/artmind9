"""Vault root resolution + safe read/write (ADR 0002, ADR 0015).

The Vault is the authoritative markdown the user works in (ADR 0002), a
first-class configured root distinct from ARTMIND_HOME and ARTMIND_DATA_DIR.
Resolved from ``ARTMIND_VAULT_DIR``; falls back to a shipped ``dev_vault/`` so
the dev skeleton has something real to render.

Phase 1c adds writing (ADR 0015): the editor pane writes markdown back with a
**conditional write** — the caller sends the version it loaded, and the write
is refused (409) if the file changed underneath it. "Version" is the sha256 of
the on-disk bytes, which is robust across filesystems (unlike mtime). Writing is
restricted to markdown (ADR 0005: the editable surface is markdown only).
"""

import hashlib
import os
from pathlib import Path

_MARKDOWN_SUFFIXES = {".md", ".markdown"}


class VaultConflict(Exception):
    """The on-disk file changed since it was loaded (or create-vs-exists clash).

    Carries the current on-disk version so the client can offer reload/overwrite/diff.
    ``current_version`` is None when the caller expected an existing file that is now gone.
    """

    def __init__(self, current_version: str | None) -> None:
        super().__init__("vault file changed on disk")
        self.current_version = current_version


def vault_root() -> Path:
    env = os.environ.get("ARTMIND_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # Dev fallback: the sample vault shipped under the backend dir
    # (.../backend/src/artmind_canvas_backend/vault.py → parents[2] == .../backend).
    return (Path(__file__).resolve().parents[2] / "dev_vault").resolve()


def _resolve(rel_path: str) -> Path:
    """Resolve a root-relative path, guarding against traversal outside the Vault."""
    root = vault_root()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)  # must stay within the vault root
    except ValueError as exc:
        raise ValueError("path escapes vault root") from exc
    return target


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_vault_file(rel_path: str) -> tuple[str, str]:
    """Read a Vault file → (content, version). Guards against traversal."""
    target = _resolve(rel_path)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    data = target.read_bytes()
    return data.decode("utf-8"), _version(data)


def write_vault_file(rel_path: str, content: str, base_version: str | None) -> str:
    """Conditionally write a markdown file → the new version.

    - Existing file: ``base_version`` must match the on-disk version, else ``VaultConflict``.
    - New file: ``base_version`` must be None; a non-None value means the caller thought
      the file existed but it is gone → ``VaultConflict``.
    Returns the new version (sha256 of the written bytes).
    """
    target = _resolve(rel_path)
    if target.suffix.lower() not in _MARKDOWN_SUFFIXES:
        raise ValueError("only markdown files are editable")

    if target.exists():
        if not target.is_file():
            raise ValueError("path is not a file")
        current = _version(target.read_bytes())
        if base_version != current:
            raise VaultConflict(current)
    else:
        if base_version is not None:
            raise VaultConflict(None)  # expected an existing file; it's gone
        target.parent.mkdir(parents=True, exist_ok=True)

    data = content.encode("utf-8")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)  # atomic on the same filesystem
    return _version(data)


def list_vault_dir(rel_dir: str = "") -> dict[str, list[str]]:
    """List a Vault directory → ``{dirs, files}`` of root-relative paths.

    Only markdown files are surfaced (the editable surface, ADR 0005). Powers the
    "save to…" placement picker and file browsing. Traversal-guarded; hidden entries
    (dotfiles) are skipped.
    """
    root = vault_root()
    target = _resolve(rel_dir) if rel_dir else root
    if not target.is_dir():
        raise FileNotFoundError(rel_dir)
    dirs: list[str] = []
    files: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        rel = entry.relative_to(root).as_posix()
        if entry.is_dir():
            dirs.append(rel)
        elif entry.is_file() and entry.suffix.lower() in _MARKDOWN_SUFFIXES:
            files.append(rel)
    return {"dirs": dirs, "files": files}
