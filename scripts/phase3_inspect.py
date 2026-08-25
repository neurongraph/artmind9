#!/usr/bin/env python
"""What the Phase 3 run actually produced — scoped to projected entities only.

Everything here filters on `e.key IS NOT NULL`, i.e. entities the Phase 3
projection wrote. A pre-cutover graph also holds entities from the old
accretive upsert; mixing them in makes the output unreadable and invites
exactly the wrong conclusion (an earlier version of this diagnostic did that).

    uv run python scripts/phase3_inspect.py
    uv run python scripts/phase3_inspect.py --domain banking.products
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _rule(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}\n{DIM}{'─' * len(title)}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", default="banking.reference")
    parser.add_argument("--entityClass", dest="entity_class", default="RATE_ENTRY")
    args = parser.parse_args()

    from artmind.graph_query import _connection_settings, read_session
    from artmind.observations import normalize_name

    print(f"Neo4j: {_connection_settings()['uri']}  domain={args.domain}")

    with read_session() as s:
        # ── 1. Did the naming rule land? ──────────────────────────────────
        _rule("Projected entity names (did the schema guidance fix work?)")
        rows = s.run(
            """
            MATCH (e:Entity {domain: $d}) WHERE e.key IS NOT NULL AND e.entity_class = $c
            RETURN e.name AS name, e.rate_value AS rate,
                   e._temporal_props AS temporal, e._observation_count AS obs
            ORDER BY name
            """,
            d=args.domain, c=args.entity_class,
        ).data()
        if not rows:
            print("  (no projected entities — the run wrote nothing)")
        laden = 0
        for r in rows:
            name = r["name"] or ""
            # A name still carrying a measurement or a date is the schema
            # guidance still winning over the recurrent naming rule.
            dirty = any(ch in name for ch in "%£$€") or normalize_name(name) != name.casefold().strip()
            laden += 1 if dirty else 0
            mark = "!" if dirty else " "
            print(f"  {mark} {name}")
            print(f"      rate={r['rate']!r}  temporal={r['temporal']}  observations={r['obs']}")
        if rows:
            print(f"\n  {laden} of {len(rows)} names still carry a measurement or date "
                  f"(want 0 — '!' marks them)")

        # ── 2. Conflicts, with their evidence ─────────────────────────────
        _rule("Conflicts raised by the projection, with evidence")
        conflicts = s.run(
            """
            MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(e:Entity {domain: $d})
            OPTIONAL MATCH (c)-[:EVIDENCE]->(o:Observation)
            RETURN e.name AS entity, c.property AS property, c.values AS values,
                   collect(DISTINCT o.doc_id) AS docs,
                   collect(DISTINCT o._valid_from) AS instants
            ORDER BY entity, property
            """,
            d=args.domain,
        ).data()
        if not conflicts:
            print("  (none)")
        for c in conflicts:
            print(f"  {c['entity']}  ·  {c['property']}")
            print(f"      values  : {c['values']}")
            print(f"      docs    : {c['docs']}")
            print(f"      instants: {sorted(x for x in c['instants'] if x)}")
        if conflicts:
            print("\n  A conflict means two observations disagreed AT THE SAME instant.")
            print("  Check the instants above: if they are all identical, the corpus or the")
            print("  extractor really did contradict itself and the projection is right.")

        # ── 3. The Tier 2 aggregate, in detail ────────────────────────────
        _rule("Observations behind the SmartSaver Tier 2 rate")
        obs = s.run(
            """
            MATCH (e:Entity {domain: $d})-[:AGGREGATES]->(o:Observation)
            WHERE e.key STARTS WITH 'smartsaver account tier 2 rate|'
            RETURN o.name AS raw, o.canonical_name AS canonical, o.doc_id AS doc,
                   o.chunk_id AS chunk, o._valid_from AS valid_from,
                   o._doc_valid_from AS doc_valid_from, o.rate_value AS rate
            ORDER BY doc_valid_from, chunk
            """,
            d=args.domain,
        ).data()
        if not obs:
            print("  (none — the Tier 2 key has no projected entity)")
        for o in obs:
            print(f"  {o['doc_valid_from']}  rate={o['rate']!r}  chunk={o['chunk']}")
            print(f"      raw       : {o['raw']}")
            if o["canonical"] != o["raw"]:
                print(f"      canonical : {o['canonical']}   {DIM}(rewritten){RESET}")
            else:
                print(f"      canonical : {DIM}(unchanged — canonicalization did not rewrite this){RESET}")
        if obs:
            rewritten = sum(1 for o in obs if o["canonical"] != o["raw"])
            docs = len({o["doc"] for o in obs})
            print(f"\n  {len(obs)} observations from {docs} document(s); "
                  f"{rewritten} had their name rewritten by canonicalization")

        # ── 4. The embedding invariant ────────────────────────────────────
        _rule("Embedding health")
        emb = s.run(
            """
            MATCH (e:Entity {domain: $d}) WHERE e.key IS NOT NULL
            RETURN count(e) AS total,
                   count(CASE WHEN e.embedding IS NULL THEN 1 END) AS unembedded,
                   count(CASE WHEN e.embedding_stale THEN 1 END) AS stale
            """,
            d=args.domain,
        ).single()
        print(f"  projected entities: {emb['total']}   un-embedded: {emb['unembedded']}   "
              f"stale: {emb['stale']}")
        print("  (un-embedded should be 0 after the sweep; stale entities are still findable)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
