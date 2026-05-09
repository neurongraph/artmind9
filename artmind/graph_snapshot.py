import json
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _sanitize_label
from artmind.setup import _setup_neo4j
from paths import GRAPH_SNAPSHOT_DIR
from utils.functions import load_env


# ── constants ─────────────────────────────────────────────────────────────────

BASE_LABELS = ("Document", "DocChunk", "Entity", "UserChat")

_ENTITY_MATCH_KEYS = ("name", "entity_class", "domain")
_ID_MATCH_KEYS = ("id",)


# ── helpers ───────────────────────────────────────────────────────────────────


def _match_keys_for_node(labels: list[str], props: dict) -> dict:
    """Extract the match keys used to uniquely identify a node during import."""
    if "Entity" in labels:
        return {k: props[k] for k in _ENTITY_MATCH_KEYS if k in props}
    return {k: props[k] for k in _ID_MATCH_KEYS if k in props}


def _find_latest_snapshot() -> Path | None:
    """Return the newest snapshot .tar.gz in GRAPH_SNAPSHOT_DIR, or None."""
    if not GRAPH_SNAPSHOT_DIR.exists():
        return None
    snapshots = sorted(GRAPH_SNAPSHOT_DIR.glob("snapshot_*.tar.gz"))
    return snapshots[-1] if snapshots else None


# ── export ────────────────────────────────────────────────────────────────────


def _export_nodes(session) -> dict[str, list[dict]]:
    """Query all nodes grouped by base label. Each node gets its full label set."""
    nodes: dict[str, list[dict]] = {}
    for base_label in BASE_LABELS:
        result = session.run(
            f"MATCH (n:{base_label}) RETURN properties(n) AS props, labels(n) AS labels"
        )
        label_nodes = []
        for record in result:
            node = dict(record["props"])
            node["labels"] = list(record["labels"])
            label_nodes.append(node)
        nodes[base_label] = label_nodes
        logger.debug("Exported {} {} node(s)", len(label_nodes), base_label)
    return nodes


def _export_relationships(session) -> list[dict]:
    """Query all relationships with start/end match keys."""
    result = session.run(
        "MATCH (s)-[r]->(e) "
        "RETURN labels(s) AS start_labels, properties(s) AS start_props, "
        "       type(r) AS rel_type, properties(r) AS rel_props, "
        "       labels(e) AS end_labels, properties(e) AS end_props"
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
        nodes = _export_nodes(session)
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
    """CREATE all nodes from snapshot data. Returns counts per label."""
    counts: dict[str, int] = {}
    for base_label, node_list in nodes.items():
        for node in node_list:
            props = {k: v for k, v in node.items() if k != "labels"}
            labels = node.get("labels", [base_label])

            if base_label == "Entity":
                # Build label string from stored labels (e.g. "CHARACTER:Entity")
                label_parts = [_sanitize_label(l) for l in labels if l != "Entity"]
                label_str = ":".join(label_parts + ["Entity"]) if label_parts else "Entity"
            else:
                label_str = base_label

            session.run(f"CREATE (n:{label_str}) SET n = $props", props=props)
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


def import_graph(snapshot_path: Path | None = None) -> dict:
    """Wipe Neo4j and restore from a snapshot.

    If snapshot_path is None, uses the latest snapshot in GRAPH_SNAPSHOT_DIR.
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
    data = _read_snapshot(snapshot_path)

    with neo4j_session() as session:
        logger.info("Wiping Neo4j database...")
        _wipe_database(session)

        logger.info("Recreating schema...")
        _setup_neo4j(session, embedding_dim)

        logger.info("Restoring nodes...")
        node_counts = _restore_nodes(session, data.get("nodes", {}))

        logger.info("Restoring relationships...")
        rel_count = _restore_relationships(session, data.get("relationships", []))

    elapsed = time.monotonic() - t0
    total_nodes = sum(node_counts.values())
    logger.info(
        "Import complete in {:.1f}s: {} nodes, {} relationships",
        elapsed, total_nodes, rel_count,
    )
    return {
        "snapshot": snapshot_path.name,
        "node_counts": node_counts,
        "relationship_count": rel_count,
        "elapsed_seconds": round(elapsed, 1),
    }
