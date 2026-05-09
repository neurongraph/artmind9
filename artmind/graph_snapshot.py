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
