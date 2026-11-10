"""One-off cleanup: drop the `:TopicPage` graph copy of topic pages (spec 2026-11-10 §5.2).

REMOVE THIS MODULE, its call in `setup.setup_all` and `test/test_migration_topic_pages.py`
once every graph in use prints `topic page graph copy: none` from `artmind setup`. A fresh
graph never needs it: setup no longer creates the constraint or the indexes.

Agents read topic plans, never pages, and the admin console reads the page files, so
nothing reads the nodes any more. Deletes every `:TopicPage` node with its `CITES`,
`CITES_PASSAGE` and `ABOUT` edges (in batches), then drops the `topic_embedding` and
`topic_text_ft` indexes and the `topic_page_slug` constraint. `run(session, dry_run=True)`
reports the same counts and changes nothing.
"""
from __future__ import annotations

INDEXES = ("topic_embedding", "topic_text_ft")
CONSTRAINT = "topic_page_slug"
BATCH = 1000
NONE = "topic page graph copy: none"

_PRESENT = "SHOW INDEXES YIELD name WHERE name IN $names RETURN name"
_CONSTRAINTS = "SHOW CONSTRAINTS YIELD name WHERE name = $name RETURN name"
_COUNT = """
MATCH (t:TopicPage)
OPTIONAL MATCH (t)-[r:CITES|CITES_PASSAGE|ABOUT]->()
RETURN count(DISTINCT t) AS nodes, count(r) AS edges
"""
_DELETE = f"""
MATCH (t:TopicPage)
WITH t LIMIT {BATCH}
DETACH DELETE t
RETURN count(*) AS n
"""


def run(session, dry_run: bool = False) -> dict:
    indexes = [r["name"] for r in session.run(_PRESENT, names=list(INDEXES)).data()]
    indexes = [n for n in INDEXES if n in indexes]
    constraint = bool(session.run(_CONSTRAINTS, name=CONSTRAINT).data())
    counts = session.run(_COUNT).single()
    nodes, edges = int(counts["nodes"] or 0), int(counts["edges"] or 0)
    if not indexes and not constraint and not nodes:
        return {"status": "none", "report": NONE}
    found = {"nodes": nodes, "edges": edges, "indexes": indexes, "constraint": CONSTRAINT if constraint else None}
    if dry_run:
        return {"status": "would_drop", **found,
                "report": (f"topic page graph copy: would delete {nodes} node(s) ({edges} edge(s)) and drop "
                           f"{len(indexes) + int(constraint)} index/constraint(s)")}
    deleted = 0
    while True:
        n = int(session.run(_DELETE).single()["n"] or 0)
        deleted += n
        if n < BATCH:
            break
    for name in indexes:
        session.run(f"DROP INDEX {name} IF EXISTS")
    if constraint:
        session.run(f"DROP CONSTRAINT {CONSTRAINT} IF EXISTS")
    return {"status": "dropped", **found, "deleted": deleted, "report": NONE}
