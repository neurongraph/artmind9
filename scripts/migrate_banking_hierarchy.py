#!/usr/bin/env python3
"""Optional migration: rename flat banking_* domains to hierarchical banking.* so
the existing STARTS WITH rollup lets `--domain banking` span all siblings.

Renames the .domain property on Document/DocChunk/UserChat/Entity/Conflict nodes
and moves schema files. Idempotent; dry-run by default.

  uv run python scripts/migrate_banking_hierarchy.py --apply
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from artmind.graph_query import neo4j_session

RENAMES = {
    "banking_policy": "banking.policy",
    "banking_reference": "banking.reference",
    "banking_sop_guides": "banking.sop_guides",
    "banking_products": "banking.products",
    "banking_organization": "banking.organization",
    "banking_communications": "banking.communications",
    "banking_risk_governance": "banking.risk_governance",
}


def migrate(apply: bool) -> None:
    with neo4j_session() as session:
        for old, new in RENAMES.items():
            count = session.run(
                "MATCH (n) WHERE n.domain = $old RETURN count(n) AS c", old=old
            ).single()["c"]
            print(f"  {old} -> {new}: {count} node(s)")
            if apply and count:
                session.run(
                    "MATCH (n) WHERE n.domain = $old SET n.domain = $new",
                    old=old, new=new,
                )
                # Conflict.domains is a list property — update in place.
                session.run(
                    "MATCH (co:Conflict) WHERE $old IN co.domains "
                    "SET co.domains = [d IN co.domains | CASE WHEN d=$old THEN $new ELSE d END]",
                    old=old, new=new,
                )
    print("Applied." if apply else "Dry-run only. Re-run with --apply to write.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    migrate(ap.parse_args().apply)
