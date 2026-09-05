import json
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.setup import _setup_neo4j
from paths import GRAPH_SNAPSHOT_DIR
from utils.functions import load_env


# ── constants ─────────────────────────────────────────────────────────────────

# Phase 5 (docs/redesign-phase-plan.md, "B") called these SOURCES ONLY and
# excluded :Entity/:Conflict/every projection-owned edge: they're derived from
# what's below, and a snapshot carrying a derived layer could carry a STALE
# one with no way for import to know it's stale.
#
# The three History labels (Phase 4) ARE the retired half of
# Document/DocChunk/Observation, not a separate zone — omitting them would
# silently drop every retired document from a snapshot. `Synthesis` (Phase 6)
# is listed pre-emptively: an empty MATCH costs nothing, and adding it later
# would be one more thing to remember.
BASE_LABELS = (
    "Document", "DocumentHistory",
    "DocChunk", "DocChunkHistory",
    "UserChat",
    "Observation", "ObservationHistory",
    "Synthesis",
)

# Phase 9: the "no way to know it's stale" objection above is no longer true.
# `projection.py`'s `:ProjectionState` singleton already hashes `same_as.yaml`
# and the domain schema set at the moment of the last full rebuild — exactly
# the drift signal import needs. So the derived layer is exported too, and
# `import_graph` decides at restore time whether the restored copy is still
# trustworthy (hashes match what's current) or must be thrown away and
# rebuilt (drift, or an older snapshot that carries none of this).
#
# Entity ids are deterministic (`sha256(canonical_name|entity_class|domain)`,
# see `observations.entity_id`), so a restored Entity round-trips byte-
# identical to a freshly rebuilt one when there's no drift — nothing is lost
# by trusting it. `ProjectionState` travels alongside Entity/Conflict because
# it's the only record of the hashes a restored projection was built against.
PROJECTED_LABELS = ("Entity", "Conflict", "ProjectionState")

_ID_MATCH_KEYS = ("id",)


# ── helpers ───────────────────────────────────────────────────────────────────


def _match_keys_for_node(labels: list[str], props: dict) -> dict:
    """Extract the match keys used to uniquely identify a node during import.

    Every source label carries a unique `id`. `:Entity` is the one exception:
    it's MERGEd in the live graph on `_id` (see `projection.rebuild_key`), not
    `id`, so relationships pointing at a restored Entity must be re-matched
    the same way or every AGGREGATES/RELATES_TO/CONFLICT_OF edge onto it would
    silently fail to attach.
    """
    if "Entity" in labels:
        return {"_id": props["_id"]} if "_id" in props else {}
    return {k: props[k] for k in _ID_MATCH_KEYS if k in props}


def _find_latest_snapshot() -> Path | None:
    """Return the newest snapshot .tar.gz in GRAPH_SNAPSHOT_DIR, or None."""
    if not GRAPH_SNAPSHOT_DIR.exists():
        return None
    snapshots = sorted(GRAPH_SNAPSHOT_DIR.glob("snapshot_*.tar.gz"))
    return snapshots[-1] if snapshots else None


# ── export ────────────────────────────────────────────────────────────────────


def _export_nodes(session, labels: tuple[str, ...] = BASE_LABELS) -> dict[str, list[dict]]:
    """Query all nodes grouped by label, from the given set. Each node gets
    its full label set — needed to round-trip a multi-labeled node like
    `:Entity:CHARACTER` (see `_restore_nodes`)."""
    nodes: dict[str, list[dict]] = {}
    for label in labels:
        result = session.run(
            f"MATCH (n:{label}) RETURN properties(n) AS props, labels(n) AS labels"
        )
        label_nodes = []
        for record in result:
            node = dict(record["props"])
            node["labels"] = list(record["labels"])
            label_nodes.append(node)
        nodes[label] = label_nodes
        logger.debug("Exported {} {} node(s)", len(label_nodes), label)
    return nodes


def _export_relationships(session, labels: tuple[str, ...] = BASE_LABELS + PROJECTED_LABELS) -> list[dict]:
    """Query relationships between KG nodes, with start/end match keys.

    Scoped to endpoints that carry one of `labels`. Derived projections like
    the structured-store catalogue (:Table/:TableColumn, see
    artmind/structured/catalogue.py) are excluded on purpose: _restore_nodes
    never recreates those node types, so a relationship to one could never be
    matched back up on restore anyway. The catalogue is non-authoritative and
    gets rebuilt separately (unified_snapshot.py calls project_catalogue()
    after a structured-store restore).

    Defaulting to BASE_LABELS + PROJECTED_LABELS (rather than BASE_LABELS
    alone) is what lets RELATES_TO/AGGREGATES/SAME_AS/CONFLICT_OF/EVIDENCE —
    every projection-owned edge — travel with the snapshot too.
    """
    result = session.run(
        "MATCH (s)-[r]->(e) "
        "WHERE any(l IN labels(s) WHERE l IN $base_labels) "
        "  AND any(l IN labels(e) WHERE l IN $base_labels) "
        "RETURN labels(s) AS start_labels, properties(s) AS start_props, "
        "       type(r) AS rel_type, properties(r) AS rel_props, "
        "       labels(e) AS end_labels, properties(e) AS end_props",
        base_labels=list(labels),
    )
    relationships = []
    for record in result:
        start_labels = list(record["start_labels"])
        end_labels = list(record["end_labels"])
        start_props = dict(record["start_props"])
        end_props = dict(record["end_props"])
        rel_props = dict(record["rel_props"])

        # Strip embeddings from relationship properties (shouldn't have any, but be safe)
        rel_props.pop("embedding", None)

        relationships.append({
            "type": record["rel_type"],
            "start_labels": start_labels,
            "start_match": _match_keys_for_node(start_labels, start_props),
            "end_labels": end_labels,
            "end_match": _match_keys_for_node(end_labels, end_props),
            "properties": rel_props,
        })
    logger.debug("Exported {} relationship(s)", len(relationships))
    return relationships


def _compress_snapshot(json_data: dict, dest_path: Path) -> None:
    """Write snapshot JSON to a tar.gz file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = Path(tmp_dir) / "snapshot.json"
        json_path.write_text(
            json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        with tarfile.open(dest_path, "w:gz") as tar:
            tar.add(json_path, arcname="snapshot.json")


def export_graph() -> Path:
    """Export the full Neo4j graph to a compressed snapshot file.

    Returns the path to the created .tar.gz file.
    """
    env = load_env()
    database = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
    t0 = time.monotonic()

    with neo4j_session() as session:
        nodes = _export_nodes(session, labels=BASE_LABELS)
        nodes.update(_export_nodes(session, labels=PROJECTED_LABELS))
        relationships = _export_relationships(session)

    node_counts = {label: len(items) for label, items in nodes.items()}
    snapshot = {
        "meta": {
            "exported_at": datetime.now().isoformat(),
            "neo4j_database": database,
            "node_counts": node_counts,
            "relationship_count": len(relationships),
        },
        "schema": {
            "constraints": ["document_id", "chunk_id", "user_chat_id"],
            "indexes": ["entity_lookup"],
            "vector_indexes": ["chunk_embedding", "user_chat_embedding"],
            "fulltext_indexes": ["chunk_text_ft", "user_chat_text_ft"],
        },
        "nodes": nodes,
        "relationships": relationships,
    }

    GRAPH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = GRAPH_SNAPSHOT_DIR / f"snapshot_{timestamp}.tar.gz"
    _compress_snapshot(snapshot, dest)

    elapsed = time.monotonic() - t0
    size_mb = dest.stat().st_size / (1024 * 1024)
    total_nodes = sum(node_counts.values())
    logger.info(
        "Snapshot exported in {:.1f}s: {} nodes, {} relationships, {:.2f} MB",
        elapsed, total_nodes, len(relationships), size_mb,
    )
    return dest


# ── import ────────────────────────────────────────────────────────────────────


def _read_snapshot(tar_path: Path) -> dict:
    """Extract and parse snapshot.json from a .tar.gz file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with tarfile.open(tar_path, "r:gz") as tar:
            members = tar.getnames()
            if "snapshot.json" not in members:
                raise ValueError(
                    f"Archive does not contain snapshot.json (found: {members})"
                )
            tar.extract("snapshot.json", path=tmp_dir)
        json_path = Path(tmp_dir) / "snapshot.json"
        return json.loads(json_path.read_text(encoding="utf-8"))


def _wipe_database(session) -> None:
    """Drop all constraints, indexes, and delete all nodes/relationships."""
    # Drop constraints
    constraints = session.run("SHOW CONSTRAINTS").data()
    for c in constraints:
        name = c.get("name")
        if name:
            try:
                session.run(f"DROP CONSTRAINT {name}")
                logger.debug("Dropped constraint: {}", name)
            except Exception as e:
                logger.warning("Failed to drop constraint {}: {}", name, e)

    # Drop indexes
    indexes = session.run("SHOW INDEXES").data()
    for idx in indexes:
        name = idx.get("name")
        idx_type = idx.get("type", "")
        # Skip lookup indexes (auto-managed by Neo4j, cannot be dropped)
        if name and idx_type != "LOOKUP":
            try:
                session.run(f"DROP INDEX {name}")
                logger.debug("Dropped index: {}", name)
            except Exception as e:
                logger.warning("Failed to drop index {}: {}", name, e)

    # Batch delete all nodes
    batch_size = 10_000
    while True:
        result = session.run(
            f"MATCH (n) WITH n LIMIT {batch_size} DETACH DELETE n RETURN count(*) AS deleted"
        ).single()
        deleted = result["deleted"] if result else 0
        if deleted == 0:
            break
        logger.debug("Deleted {} nodes", deleted)


def _restore_nodes(session, nodes: dict[str, list[dict]]) -> dict[str, int]:
    """CREATE all nodes from snapshot data. Returns counts per bucket label.

    Recreates each node under its own exported `labels` list, not just the
    bucket key it was grouped under — the bucket key is `"Entity"`, but an
    entity node itself carries `:Entity:<CLASS>` (e.g. `:Entity:CHARACTER`;
    see `projection.rebuild_key`'s dynamic `SET e:$(...)`). Falls back to
    `[base_label]` for a node with no stored `labels` (a pre-Phase-9 snapshot,
    or any bucket where the label set never varies from the bucket key).
    """
    counts: dict[str, int] = {}
    for base_label, node_list in nodes.items():
        for node in node_list:
            labels = node.get("labels") or [base_label]
            props = {k: v for k, v in node.items() if k != "labels"}
            session.run(f"CREATE (n:{':'.join(labels)}) SET n = $props", props=props)
        counts[base_label] = len(node_list)
        logger.debug("Restored {} {} node(s)", len(node_list), base_label)
    return counts


def _restore_relationships(session, relationships: list[dict]) -> int:
    """MATCH start/end nodes and CREATE relationships. Returns count."""
    count = 0
    for rel in relationships:
        rel_type = rel["type"]
        start_match = rel["start_match"]
        end_match = rel["end_match"]
        rel_props = rel.get("properties", {})

        if not start_match or not end_match:
            # No match keys means an unrestorable endpoint (e.g. a catalogue
            # node from a snapshot taken before _export_relationships started
            # excluding those). An empty WHERE clause would be a Cypher
            # syntax error, not a harmless no-op, so skip explicitly.
            logger.warning(
                "Skipped relationship {} -> {}: unmatched endpoint(s) for {}",
                start_match, end_match, rel_type,
            )
            continue

        # Build WHERE clauses from match keys
        start_conditions = " AND ".join(f"s.{k} = $start_{k}" for k in start_match)
        end_conditions = " AND ".join(f"e.{k} = $end_{k}" for k in end_match)

        params = {}
        for k, v in start_match.items():
            params[f"start_{k}"] = v
        for k, v in end_match.items():
            params[f"end_{k}"] = v
        params["rel_props"] = rel_props

        cypher = (
            f"MATCH (s) WHERE {start_conditions} "
            f"MATCH (e) WHERE {end_conditions} "
            f"CREATE (s)-[r:{rel_type}]->(e) SET r = $rel_props"
        )
        try:
            session.run(cypher, **params)
            count += 1
        except Exception as exc:
            logger.warning(
                "Skipped relationship {} -> {}: {}",
                start_match, end_match, exc,
            )
    logger.debug("Restored {} relationship(s)", count)
    return count


def _sweep_stale_embeddings(keys: list[tuple[str, str, str]]) -> tuple[int, int]:
    """Run the embed sweep over `keys`, grouped by top-level domain family
    (`_sweep_embeddings` itself scopes by exact/prefix domain match, so a
    family-level call is required to reach e.g. both `banking.reference` and
    `banking.products`). Returns (embedded_total, stale_remaining) — the
    latter a direct post-sweep count, reported loudly rather than assumed
    zero (CLAUDE.md: never null an embedding, so a down embed service leaves
    entities `embedding_stale` rather than failing the restore).

    Shared by both projection-rebuild paths in `import_graph`: the full
    rebuild sweeps every key (`projection.all_keys`), the fast/no-rebuild
    path sweeps only the keys the restored snapshot already flagged stale.
    """
    from artmind.ingest import _sweep_embeddings

    embedded_total = 0
    domains_swept = sorted({key[2].split(".", 1)[0] for key in keys if key[2]})
    for dom in domains_swept:
        dom_keys = [k for k in keys if k[2] == dom or k[2].startswith(dom + ".")]
        embedded_total += _sweep_embeddings(dom, dom_keys)

    with neo4j_session() as session:
        stale_remaining = session.run(
            "MATCH (e:Entity) WHERE e.embedding IS NULL OR e.embedding_stale RETURN count(e) AS n"
        ).single()["n"]
    if stale_remaining:
        logger.warning(
            "Post-import embed sweep: {} entit{} still have no usable embedding "
            "(embed service unavailable?) -- invisible to entity-resolve's "
            "vector leg until the sweep is re-run (`projection rebuild` or "
            "re-running this import)", stale_remaining, "y" if stale_remaining == 1 else "ies",
        )
    return embedded_total, stale_remaining


def _restored_stale_entity_keys(session) -> list[tuple[str, str, str]]:
    """Keys for every restored `:Entity` still flagged `embedding_stale` (or
    missing an embedding outright) — the fast-restore path's sweep scope.
    Parsed from each entity's own `key` property (`name|CLASS|domain`) rather
    than importing `projection`'s private key parser, mirroring this
    module's existing style of a small local duplicate over a cross-module
    private import (see `_sanitize_label` elsewhere in this codebase)."""
    rows = session.run(
        "MATCH (e:Entity) WHERE e.embedding IS NULL OR e.embedding_stale "
        "RETURN e.key AS key"
    ).data()
    keys = []
    for row in rows:
        parts = (row.get("key") or "").split("|")
        if len(parts) == 3:
            keys.append(tuple(parts))
    return keys


def _report_progress(progress_cb, phase: str, detail: str | None = None) -> None:
    """Best-effort progress callback. `import_graph` can run for hours on a
    remote database, entirely inside one blocking call (a CLI process, or a
    single `asyncio.to_thread` from the admin-ui) — a caller that wants to
    show the operator something better than a hung request/prompt provides
    this and reads it back from wherever it stashed it (e.g. an in-memory
    dict a polling endpoint serves). A broken callback must never take down
    the restore it's just narrating."""
    if progress_cb is None:
        return
    try:
        progress_cb(phase, detail)
    except Exception as exc:
        logger.debug("Progress callback raised (ignored): {}", exc)


def import_graph(
    snapshot_path: Path | None = None,
    *,
    force_rebuild: bool | None = None,
    progress_cb=None,
) -> dict:
    """Wipe Neo4j and restore from a snapshot.

    If snapshot_path is None, uses the latest snapshot in GRAPH_SNAPSHOT_DIR.

    `force_rebuild` controls the projection rebuild described below:
      - `None` (default, "auto") — rebuild only if the restored `:Entity`
        layer is missing (an older snapshot, or `--only` excluded it) or
        provably stale (`same_as.yaml`/domain schemas changed since the
        snapshot's own last rebuild, per its restored `:ProjectionState`).
        Otherwise the restored copy is trusted as-is and the rebuild is
        skipped.
      - `True` — always rebuild, even with no detected drift.
      - `False` — never rebuild, even with detected drift. The restored
        copy is used as-is; a loud warning is logged, since this can leave
        the projection genuinely stale on purpose.

    `progress_cb`, if given, is called `progress_cb(phase: str, detail: str
    | None)` at each major phase boundary (see `_report_progress`) — purely
    additive, on top of the existing `logger.info` calls, not a replacement
    for them.

    Returns a summary dict.
    """
    if snapshot_path is None:
        snapshot_path = _find_latest_snapshot()
    if snapshot_path is None:
        raise FileNotFoundError("No snapshots found in " + str(GRAPH_SNAPSHOT_DIR))

    env = load_env()
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))
    t0 = time.monotonic()

    logger.info("Importing from: {}", snapshot_path.name)
    _report_progress(progress_cb, "reading_snapshot", snapshot_path.name)
    data = _read_snapshot(snapshot_path)

    with neo4j_session() as session:
        logger.info("Wiping Neo4j database...")
        _report_progress(progress_cb, "wiping")
        _wipe_database(session)

        logger.info("Recreating schema...")
        _report_progress(progress_cb, "recreating_schema")
        _setup_neo4j(session, embedding_dim)

        logger.info("Restoring nodes...")
        _report_progress(progress_cb, "restoring_nodes")
        node_counts = _restore_nodes(session, data.get("nodes", {}))

        logger.info("Restoring relationships...")
        _report_progress(progress_cb, "restoring_relationships", str(len(data.get("relationships", []))))
        rel_count = _restore_relationships(session, data.get("relationships", []))

    # Phase 5 (docs/redesign-phase-plan.md, "B"): rebuild everything derived
    # from sources, automatically, as this restore's final phase, in order:
    # docs reindex -> projection (rebuild-or-trust) -> embed sweep. A restore
    # that left the graph unqueryable until an operator remembered a second
    # command would get reported as broken, not as "restored".
    logger.info("Rebuilding the registry from vault frontmatter (docs reindex)...")
    _report_progress(progress_cb, "reindexing")
    reindex_result: dict | None = None
    reindex_error: str | None = None
    try:
        from artmind.reindex import reindex

        reindex_result = reindex()
    except Exception as exc:
        reindex_error = str(exc)
        logger.warning("Post-import docs reindex skipped: {}", exc)

    from artmind import projection, same_as

    # Phase 9: the restored :Entity layer (see PROJECTED_LABELS) is trusted
    # as-is when it's provably still in sync with same_as.yaml and the
    # domain schemas -- entity ids are deterministic, so a byte-identical
    # rebuild would produce nothing new anyway. Any uncertainty (no restored
    # Entity data, no restored :ProjectionState, or an actual hash mismatch)
    # falls back to today's unconditional full rebuild -- never silently
    # trusts a copy it can't vouch for.
    entity_count = node_counts.get("Entity", 0)
    rebuild_needed = True
    drift_reason: str | None = None
    if force_rebuild is True:
        drift_reason = "rebuild forced"
    elif entity_count == 0:
        drift_reason = "snapshot carries no restored :Entity nodes"
    else:
        with neo4j_session() as session:
            state = session.execute_read(lambda tx: projection.read_state(tx))
        if not state:
            drift_reason = "restored snapshot carries no :ProjectionState (older export format)"
        else:
            same_as_drift = state.get("same_as_hash") != same_as.content_hash()
            schema_drift = state.get("schema_hash") != projection.schema_set_hash()
            if same_as_drift or schema_drift:
                drift_reason = (
                    "same_as.yaml changed since the snapshot's last rebuild" if same_as_drift
                    else "domain schemas changed since the snapshot's last rebuild"
                )
            else:
                rebuild_needed = False

    skip_reason: str | None = None
    if force_rebuild is False and rebuild_needed:
        skip_reason = drift_reason
        rebuild_needed = False
        logger.warning(
            "Projection rebuild skipped by request (--no-rebuild-projection) despite: {}. "
            "The restored :Entity layer may be stale until the next `projection rebuild`.",
            drift_reason,
        )

    if rebuild_needed:
        logger.info("Rebuilding the projection (full) -- {}...", drift_reason)
        _report_progress(progress_cb, "rebuilding_projection", drift_reason)
        with neo4j_session() as session:
            all_keys = sorted(session.execute_read(lambda tx: projection.all_keys(tx, None)))
            rebuild_summary = session.execute_write(
                lambda tx: projection.full_rebuild(
                    tx, None, synthesis_loader=lambda k: projection.load_synthesis(tx, k)
                )
            )
        logger.info("Running the embed sweep across {} key(s)...", len(all_keys))
        _report_progress(progress_cb, "embed_sweep", str(len(all_keys)))
        embedded_total, stale_remaining = _sweep_stale_embeddings(all_keys)
    else:
        logger.info(
            "Restored projection ({} entit{}) matches current same_as/schema state -- "
            "skipping the full rebuild.", entity_count, "y" if entity_count == 1 else "ies",
        )
        _report_progress(progress_cb, "projection_trusted", str(entity_count))
        rebuild_summary = {"skipped": True, "reason": skip_reason or "no drift detected"}
        with neo4j_session() as session:
            stale_keys = session.execute_read(lambda tx: _restored_stale_entity_keys(tx))
        _report_progress(progress_cb, "embed_sweep", str(len(stale_keys)))
        embedded_total, stale_remaining = _sweep_stale_embeddings(stale_keys)

    elapsed = time.monotonic() - t0
    total_nodes = sum(node_counts.values())
    logger.info(
        "Import complete in {:.1f}s: {} nodes, {} relationships, {} entit{} embedded, "
        "{} still stale",
        elapsed, total_nodes, rel_count, embedded_total,
        "y" if embedded_total == 1 else "ies", stale_remaining,
    )
    _report_progress(progress_cb, "done", f"{elapsed:.1f}s")
    return {
        "snapshot": snapshot_path.name,
        "node_counts": node_counts,
        "relationship_count": rel_count,
        "elapsed_seconds": round(elapsed, 1),
        "reindex": reindex_result,
        "reindex_error": reindex_error,
        "projection_rebuild": rebuild_summary,
        "projection_rebuild_skipped": not rebuild_needed,
        "embedded": embedded_total,
        "embedding_stale_remaining": stale_remaining,
    }
