"""Helpers the tier-1 writes share to describe their `effect` (spec 2026-11-02 §3.4).

`cli_conventions.Effect` is the shape; this module reads what a change would
touch: the topic pages it leaves stale. Read-only, no Neo4j, no LLM.
"""
from __future__ import annotations

from typing import Iterable


def topics_touching(*, entity_keys: Iterable[str] = (), doc_ids: Iterable[str] = (),
                    doc_names: Iterable[str] = ()) -> list[str]:
    """Slugs of topic pages anchored on one of `entity_keys` or citing one of the documents.

    Read from the pages' frontmatter (`topics.store.list_pages`). Outside a vault, or
    with no `_Topics/` folder, there are none.
    """
    import paths
    from artmind.topics import store

    directory = getattr(paths, "TOPICS_DIR", None)
    if directory is None:
        return []
    keys, ids, names = set(entity_keys), set(doc_ids), set(doc_names)
    slugs = []
    for page in store.list_pages(directory):
        anchor = page.meta.get("anchor") or {}
        sources = [s for s in page.meta.get("sources") or [] if isinstance(s, dict)]
        if (anchor.get("kind") == "entity" and anchor.get("key") in keys) or any(
            s.get("doc_id") in ids or s.get("name") in names for s in sources
        ):
            slugs.append(page.slug)
    return sorted(slugs)
