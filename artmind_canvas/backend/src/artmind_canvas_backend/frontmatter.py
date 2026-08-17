"""Client-owned YAML frontmatter shaping for the doc-first placement path.

Editing a markdown file's YAML header is a client *write* concern (the doc-first
path, ADR 0002/0012): the Placement Card, once the user confirms, folds the
accepted filing facets into the document's frontmatter and writes it back through
the ordinary conditional Vault write. That is not ingestion logic — ingestion
(and the graph) stay entirely behind artmind. We keep our own tiny parser/merger
here rather than reaching into artmind's private ``ingest._parse_md_frontmatter``.

Only the filing keys ingestion actually reads from frontmatter (ADR 0010,
``artmind/ingest.py``) are written: ``project``, ``area``, ``tags`` (a list), and
``title``. ``domain`` is deliberately NOT written — ingestion sources the domain
from its ``--domain`` flag / registry, never from frontmatter, so writing it here
would be dead metadata.
"""

from typing import Any

import yaml


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(metadata, body)``. Metadata is empty if there is no frontmatter.

    Mirrors ``artmind.ingest._parse_md_frontmatter`` so the round-trip matches what
    ingestion will later parse.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[end + 4 :].lstrip("\n")


def merge_frontmatter(
    text: str,
    *,
    area: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    title: str | None = None,
) -> str:
    """Fold the accepted filing facets into ``text``'s frontmatter → new markdown.

    Existing frontmatter keys and the document body are preserved; only the
    supplied facets are set (``None`` leaves a facet untouched; an empty list
    clears ``tags``). A document with no frontmatter gains one.
    """
    meta, body = split_frontmatter(text)
    meta = dict(meta)  # don't mutate the parsed mapping in place

    if area is not None:
        meta["area"] = area
    if project is not None:
        meta["project"] = project
    if tags is not None:
        meta["tags"] = list(tags)
    if title is not None:
        meta["title"] = title

    if not meta:
        # Nothing to write and no prior frontmatter — return the body unchanged.
        return body

    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}" if body else f"---\n{front}\n---\n"
