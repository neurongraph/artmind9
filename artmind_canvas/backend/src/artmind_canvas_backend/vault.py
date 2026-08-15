"""Vault root resolution + safe read (Phase 0 = read-only).

The Vault is the authoritative markdown the user works in (ADR 0002), a
first-class configured root distinct from ARTMIND_HOME and ARTMIND_DATA_DIR.
Resolved from ``ARTMIND_VAULT_DIR``; falls back to a shipped ``dev_vault/`` so
the Phase-0 skeleton has something real to render.
"""

import os
from pathlib import Path


def vault_root() -> Path:
    env = os.environ.get("ARTMIND_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # Dev fallback: the sample vault shipped under the backend dir
    # (.../backend/src/artmind_canvas_backend/vault.py → parents[2] == .../backend).
    return (Path(__file__).resolve().parents[2] / "dev_vault").resolve()


def read_vault_file(rel_path: str) -> str:
    """Read a Vault file by root-relative path, guarding against traversal."""
    root = vault_root()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)  # target must stay within the vault root
    except ValueError as exc:
        raise ValueError("path escapes vault root") from exc
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target.read_text(encoding="utf-8")
