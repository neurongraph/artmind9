#!/usr/bin/env python3
"""Before/after check for the batched full projection rebuild (plan 2026-10-03, B1).

Starts a DISPOSABLE Neo4j container with a small transaction-memory pool,
seeds ~4,300 synthetic aggregate keys through plain Cypher, gives the
projected entities 768-float embeddings (what made the real table commit
overflow), then shows:

  1. the OLD path -- `projection.full_rebuild` in ONE transaction -- fails
     with Neo4j's transaction-memory error;
  2. the NEW path -- `projection.full_rebuild_batched` -- passes, and
     `projection.status` reports 0 unprojected keys and no drift.

Safety: it only ever connects to 127.0.0.1:<ARTMIND_MEMCHECK_PORT> (default
17687), refuses if its container name or that port is already in use,
refuses to share a Docker VM under 4 GiB with another running Neo4j
container, and removes its container on exit (`docker run --rm` + stop).

    just dev-rebuild-memcheck

Knobs: ARTMIND_MEMCHECK_PORT, ARTMIND_MEMCHECK_IMAGE (default neo4j:latest;
needs Cypher 25, i.e. Neo4j 2025.06+), ARTMIND_MEMCHECK_TX_MAX (default 128m),
ARTMIND_MEMCHECK_KEYS (default 4300), ARTMIND_MEMCHECK_SHARE_VM=1 to skip
the VM-size refusal.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

CONTAINER = "artmind-rebuild-memcheck"
PORT = int(os.environ.get("ARTMIND_MEMCHECK_PORT", "17687"))
PASSWORD = "memcheck-disposable-only"
IMAGE = os.environ.get("ARTMIND_MEMCHECK_IMAGE", "neo4j:latest")
TX_TOTAL_MAX = os.environ.get("ARTMIND_MEMCHECK_TX_MAX", "128m")
KEYS = int(os.environ.get("ARTMIND_MEMCHECK_KEYS", "4300"))
OBS_PER_KEY = 3
DOMAIN = "memcheck"
MIN_SHARED_VM_BYTES = 4 * 1024**3

# Point artmind at the disposable container BEFORE importing it. `paths`
# loads every .env with override=False, so these real env vars win.
os.environ.update(
    ARTMIND_KG_NEO4J_URI=f"bolt://127.0.0.1:{PORT}",
    ARTMIND_KG_NEO4J_USERNAME="neo4j",
    ARTMIND_KG_NEO4J_PASSWORD=PASSWORD,
    ARTMIND_KG_NEO4J_DATABASE="neo4j",
)


def say(message: str) -> None:
    print(f"memcheck: {message}", flush=True)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _preflight() -> None:
    names = _docker("ps", "-a", "--format", "{{.Names}}").stdout.split()
    if CONTAINER in names:
        sys.exit(f"refusing: a container named {CONTAINER} already exists (docker rm -f {CONTAINER})")
    if _port_in_use(PORT):
        sys.exit(f"refusing: 127.0.0.1:{PORT} is already in use -- set ARTMIND_MEMCHECK_PORT")
    running = [
        line.split(" ", 1)[0]
        for line in _docker("ps", "--format", "{{.Names}} {{.Image}}").stdout.splitlines()
        if "neo4j" in line.split(" ", 1)[-1]
    ]
    mem_total = int(_docker("info", "--format", "{{.MemTotal}}").stdout.strip() or 0)
    if running and mem_total < MIN_SHARED_VM_BYTES and os.environ.get("ARTMIND_MEMCHECK_SHARE_VM") != "1":
        sys.exit(
            f"refusing: the Docker VM has {mem_total / 1024**3:.1f} GiB and Neo4j is already running in "
            f"{', '.join(running)}; a second JVM could OOM the VM and kill it. Stop those first "
            "(e.g. `neo4j-manager stop <name>`), or give colima more memory "
            "(`colima stop && colima start --memory 4`), or set ARTMIND_MEMCHECK_SHARE_VM=1."
        )


def _start() -> None:
    _docker(
        "run", "-d", "--rm", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:7687",
        "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}",
        "-e", f"NEO4J_db_memory_transaction_total_max={TX_TOTAL_MAX}",
        "-e", "NEO4J_server_memory_heap_max__size=512m",
        "-e", "NEO4J_server_memory_pagecache_size=128m",
        IMAGE,
    )
    say(f"started {CONTAINER} ({IMAGE}) on 127.0.0.1:{PORT}, db.memory.transaction.total.max={TX_TOTAL_MAX}")


def _wait_ready(timeout_s: int = 120) -> None:
    from artmind.graph_query import neo4j_session

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with neo4j_session() as s:
                s.run("RETURN 1").consume()
            return
        except Exception:
            time.sleep(2)
    sys.exit("the disposable Neo4j did not come up in time")


def _seed() -> None:
    from artmind.graph_query import neo4j_session
    from artmind.observations import aggregate_key, key_string
    from artmind.setup import _setup_neo4j

    filler = "lorem ipsum dolor sit amet " * 70  # ~1.9 KB, like a real extracted description
    rows, pairs = [], []
    for i in range(KEYS):
        name = f"Synthetic entity {i:05d}"
        key = key_string(aggregate_key(name, "CONCEPT", DOMAIN))
        for j in range(OBS_PER_KEY):
            rows.append({
                "id": f"memcheck-obs-{i}-{j}", "key": key, "canonical_name": name, "name": name,
                "entity_class": "CONCEPT", "_domain": DOMAIN, "_kind": "recurrent",
                "doc_id": f"memcheck-doc-{j}", "doc_version": 1, "chunk_id": f"memcheck-doc-{j}_{i:05d}",
                "_doc_valid_from": f"2026-0{j + 1}-01", "_valid_from": f"2026-0{j + 1}-01",
                "description": f"{name} (source {j}): {filler}",
            })
        if i + 1 < KEYS:
            pairs.append({"id": f"memcheck-rel-{i}", "src": f"memcheck-obs-{i}-0", "tgt": f"memcheck-obs-{i + 1}-0",
                          "chunk": f"memcheck-doc-0_{i:05d}"})
    with neo4j_session() as s:
        _setup_neo4j(s, 768)
        for start in range(0, len(rows), 1000):
            s.run("UNWIND $rows AS row CREATE (o:Observation) SET o = row", rows=rows[start:start + 1000]).consume()
        for start in range(0, len(pairs), 1000):
            s.run(
                """
                UNWIND $pairs AS p
                MATCH (s:Observation {id: p.src}), (t:Observation {id: p.tgt})
                CREATE (s)-[:ASSERTS_RELATION {id: p.id, rel_type: 'related_to',
                                               doc_id: 'memcheck-doc-0', chunk_id: p.chunk}]->(t)
                """,
                pairs=pairs[start:start + 1000],
            ).consume()
    say(f"seeded {len(rows)} observations and {len(pairs)} relations over {KEYS} keys (domain {DOMAIN})")


def _embed_entities() -> None:
    from artmind.graph_query import neo4j_session

    with neo4j_session() as s:
        s.run(
            """
            MATCH (e:Entity {_domain: $d})
            CALL (e) {
              SET e.embedding = [x IN range(1, 768) | rand()], e.embedding_stale = false
            } IN TRANSACTIONS OF 500 ROWS
            """,
            d=DOMAIN,
        ).consume()
        n = s.run("MATCH (e:Entity {_domain: $d}) WHERE e.embedding IS NOT NULL RETURN count(e) AS n", d=DOMAIN).single()["n"]
    say(f"gave {n} entities a 768-float embedding")


def _old_path_fails() -> bool:
    from artmind import projection
    from artmind.graph_query import neo4j_session

    try:
        with neo4j_session() as s:
            s.execute_write(
                lambda tx: projection.full_rebuild(
                    tx, None, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks)
                )
            )
    except Exception as e:  # the driver retries a TransientError for ~30 s first
        text = f"{getattr(e, 'code', '')} {e}"
        if "memory" in text.lower():
            say(f"OLD single-transaction full_rebuild failed as expected: {text[:200]}")
            return True
        raise
    return False


def main() -> int:
    _preflight()
    _start()
    try:
        _wait_ready()
        from artmind import projection
        from artmind.graph_query import neo4j_session

        _seed()
        first = projection.full_rebuild_batched()
        say(f"first batched build -- {first['batches']} batches, {first['rebuilt']} rebuilt")
        _embed_entities()
        if not _old_path_fails():
            say(f"OLD PATH DID NOT FAIL -- lower ARTMIND_MEMCHECK_TX_MAX (now {TX_TOTAL_MAX}) "
                f"or raise ARTMIND_MEMCHECK_KEYS (now {KEYS}) and re-run")
            return 2
        second = projection.full_rebuild_batched()
        say(f"NEW batched full rebuild passed -- {second['batches']} batches, "
            f"{second['rebuilt']} rebuilt, recorded={second['recorded']}")
        with neo4j_session() as s:
            st = s.execute_read(projection.status)
        say(f"projection status -- unprojected_keys={st['unprojected_keys']} "
            f"unembedded_entities={st['unembedded_entities']} drift={st['drift']}")
        if st["unprojected_keys"] or st["drift"]:
            say("FAIL: the batched rebuild left keys unprojected or drift set")
            return 1
        say("PASS")
        return 0
    finally:
        _docker("stop", CONTAINER, check=False)
        say(f"stopped and removed {CONTAINER}")


if __name__ == "__main__":
    sys.exit(main())
