#!/usr/bin/env python3
"""One-off migration: rename entity_class values and Neo4j labels to POOLE+ standard.

Renames:
  fiction domain:          CHARACTER → PERSON
  technical_paper domain:  AUTHOR    → PERSON
  personal_journal domain: PLACE     → LOCATION

Run once: uv run python scripts/migrate_poole.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from artmind.graph_query import neo4j_session

RENAMES = [
    ("fiction",          "CHARACTER", "PERSON"),
    ("technical_paper",  "AUTHOR",    "PERSON"),
    ("personal_journal", "PLACE",     "LOCATION"),
]


def migrate() -> None:
    with neo4j_session() as session:
        for domain, old_class, new_class in RENAMES:
            result = session.run(
                """
                MATCH (e:Entity {domain: $domain, entity_class: $old_class})
                CALL apoc.create.addLabels(e, [$new_class]) YIELD node
                WITH node
                CALL apoc.create.removeLabels(node, [$old_class]) YIELD node AS n
                SET n.entity_class = $new_class
                RETURN count(n) AS updated
                """,
                domain=domain,
                old_class=old_class,
                new_class=new_class,
            ).single()
            count = result["updated"] if result else 0
            print(f"  {domain}: {old_class} → {new_class}: {count} node(s) updated")


if __name__ == "__main__":
    print("Running POOLE+ entity class migration...")
    migrate()
    print("Done.")
