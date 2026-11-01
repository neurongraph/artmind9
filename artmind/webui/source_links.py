"""Open an answer's cited source document in Obsidian.

System-owned provenance links document and generated-passage paths. Obsidian's
Web Viewer -- where the plugin opens the admin console -- drops a page's
navigation to ``obsidian://``, so a plain click there does nothing. The UI
instead posts the path here and the server, which always runs on the same
machine as Obsidian (the plugin starts it), hands the ``obsidian://open`` URI
to the OS, exactly as a browser would.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


class SourceLinkError(ValueError):
    """The path can't be opened in the vault; the message says why."""


_GENERATED_CHUNK_ROOT = Path(".artmind/data/documents/markdowns")


def _is_generated_chunk(rel: Path, vault: Path, resolved: Path) -> bool:
    """Whether ``rel`` is the one hidden source shape chat may open.

    This is intentionally a structural allow-list, not permission to open a
    dot-folder generally.  Resolving first prevents a symlink under the cache
    root from escaping into a registry, log, or arbitrary private file.
    """
    try:
        under_cache = resolved.is_relative_to((vault / _GENERATED_CHUNK_ROOT).resolve())
    except OSError:
        return False
    return (
        under_cache
        and len(rel.parts) == len(_GENERATED_CHUNK_ROOT.parts) + 2
        and rel.parts[: len(_GENERATED_CHUNK_ROOT.parts)] == _GENERATED_CHUNK_ROOT.parts
        and rel.parts[-2].endswith("_chunks")
        and rel.parts[-1].startswith("chunk_")
        and rel.parts[-1].endswith(".md")
        and rel.parts[-1][6:-3].isdigit()
    )


def vault_relative(path: str, vault: Path) -> str:
    """`path` (vault-relative, or absolute inside the vault) as a POSIX path
    relative to `vault`. Raises SourceLinkError when it escapes the vault, sits
    in a hidden folder other than an Artmind generated chunk, or doesn't exist."""
    vault = vault.resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else vault / candidate).resolve()
    try:
        rel = resolved.relative_to(vault)
    except ValueError:
        raise SourceLinkError(f"{path} is not inside the vault") from None
    if not resolved.is_file():
        raise SourceLinkError(f"{path} does not exist in the vault")
    if any(part.startswith(".") for part in rel.parts) and not _is_generated_chunk(rel, vault, resolved):
        raise SourceLinkError(f"{path} is in a hidden folder Obsidian doesn't open")
    return rel.as_posix()


def obsidian_uri(vault: Path, rel: str) -> str:
    """`obsidian://open` for vault-relative `rel`. Obsidian names a vault after
    its folder; a note is addressed without its `.md`, any other file with its
    extension. `paneType=tab` opens it in a new tab: the active tab is the
    Web Viewer showing the UI, and replacing it loses the conversation."""
    file = rel[:-3] if rel.endswith(".md") else rel
    return f"obsidian://open?vault={quote(vault.name, safe='')}&file={quote(file, safe='')}&paneType=tab"


def open_uri(uri: str) -> None:
    """Hand `uri` to the OS URL handler (argv, never a shell)."""
    if sys.platform == "darwin":
        subprocess.run(["open", uri], check=True, timeout=10)
    elif sys.platform == "win32":
        os.startfile(uri)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", uri], check=True, timeout=10)
