"""Open an answer's cited source document in Obsidian.

Query answers end with a Sources list linking each document's vault path
(artmind-query skill). Obsidian's Web Viewer -- where the plugin opens the
admin console -- drops a page's navigation to ``obsidian://``, so a plain
click there does nothing. The UI instead posts the path here and the server,
which always runs on the same machine as Obsidian (the plugin starts it),
hands the ``obsidian://open`` URI to the OS, exactly as a browser would.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


class SourceLinkError(ValueError):
    """The path can't be opened in the vault; the message says why."""


def vault_relative(path: str, vault: Path) -> str:
    """`path` (vault-relative, or absolute inside the vault) as a POSIX path
    relative to `vault`. Raises SourceLinkError when it escapes the vault, sits
    in a dot-folder Obsidian doesn't index, or doesn't exist."""
    vault = vault.resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else vault / candidate).resolve()
    try:
        rel = resolved.relative_to(vault)
    except ValueError:
        raise SourceLinkError(f"{path} is not inside the vault") from None
    if any(part.startswith(".") for part in rel.parts):
        raise SourceLinkError(f"{path} is in a hidden folder Obsidian doesn't open")
    if not resolved.is_file():
        raise SourceLinkError(f"{path} does not exist in the vault")
    return rel.as_posix()


def obsidian_uri(vault: Path, rel: str) -> str:
    """`obsidian://open` for vault-relative `rel`. Obsidian names a vault after
    its folder; a note is addressed without its `.md`, any other file with its
    extension."""
    file = rel[:-3] if rel.endswith(".md") else rel
    return f"obsidian://open?vault={quote(vault.name, safe='')}&file={quote(file, safe='')}"


def open_uri(uri: str) -> None:
    """Hand `uri` to the OS URL handler (argv, never a shell)."""
    if sys.platform == "darwin":
        subprocess.run(["open", uri], check=True, timeout=10)
    elif sys.platform == "win32":
        os.startfile(uri)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", uri], check=True, timeout=10)
