"""Citation markers to Obsidian footnotes (spec 2026-11-01 §3.2 step 6). Pure."""
from __future__ import annotations

import re

from artmind.topics.check import MARKER


def link(info: dict) -> str:
    """`[[source|title]]`: the original document (a note without its `.md`, a converted
    binary with its extension)."""
    target = str(info.get("source_path") or info.get("path") or info.get("name") or "")
    title = str(info.get("title") or info.get("name") or target).replace("|", "-")
    if target.endswith(".md"):
        target = target[:-3]
    return f"[[{target}|{title}]]"


def chunk_link(info: dict, cid: str) -> str | None:
    """The indented second line of a footnote: the exact chunk's derived markdown
    (`.artmind/data/...`), opened from Obsidian by its path. None when unresolved."""
    path = info.get("chunk_file")
    if not path:
        return None
    seq = str(info.get("chunk_id") or "").rsplit("_", 1)[-1].lstrip("0") or "0"
    heading = str(info.get("header_path") or "").strip()
    label = f"Chunk {seq}" + (f" — {heading}" if heading else "")
    return f"[{label.replace(']', ')')}](<{path}>)"


def _passage_base(cid: str) -> str:
    """Strip a ``_pNN`` passage suffix, returning the bare chunk id.

    >>> _passage_base("c1_007_p01")
    'c1_007'
    >>> _passage_base("c1_007")
    'c1_007'
    """
    return re.sub(r"_p\d+$", "", cid)


def render_body(body: str, links: dict[str, dict]) -> tuple[str, list[str]]:
    """The body with ``[#id]`` replaced by ``[^n]`` (numbered by first use) and
    the footnotes appended; also the cited ids in footnote order.

    When two ids share the same base chunk (one is ``<cid>_pNN``, the other is
    bare ``<cid>``), the second occurrence reuses the first footnote number so
    the document is cited only once.  The footnote label is ``passage`` when the
    id has a ``_pNN`` suffix, and ``chunk`` otherwise.
    """
    order: list[str] = []           # ids in first-use order
    base_to_idx: dict[str, int] = {}  # bare-chunk-id → 0-based index in order

    def sub(match) -> str:
        cid = match.group(1)
        base = _passage_base(cid)
        if base in base_to_idx:
            # Reuse the existing footnote for this chunk
            return f"[^{base_to_idx[base] + 1}]"
        if cid not in order:
            order.append(cid)
        idx = order.index(cid)
        base_to_idx[base] = idx
        return f"[^{idx + 1}]"

    text = MARKER.sub(sub, body).rstrip()
    notes = []
    for n, cid in enumerate(order, start=1):
        info = links.get(cid, {})
        label = "passage" if _passage_base(cid) != cid else "chunk"
        line = f"[^{n}]: {link(info)} — {label} `{cid}`"
        chunk = chunk_link(info, cid)
        notes.append(line + (f"\n    {chunk}" if chunk else ""))
    return (text + "\n\n" + "\n".join(notes)) if notes else text, order
