# Session Graph Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `artmind session close` and `artmind session initiate` CLI commands to export/import the full Neo4j graph as compressed JSON snapshots.

**Architecture:** New `artmind/graph_snapshot.py` module handles all export/import logic. Export queries Neo4j for all nodes and relationships, writes a single JSON, and compresses to `.tar.gz`. Import wipes Neo4j (schema + data), recreates schema via existing `_setup_neo4j()`, then restores nodes and relationships from the snapshot. CLI commands are added to `artmind/cli.py` under a new `session` group.

**Tech Stack:** Python, Click (CLI), neo4j driver, tarfile (compression), existing artmind modules (graph_query, setup, ingest)

**Spec:** `docs/superpowers/specs/2026-05-09-session-graph-snapshot-design.md`

---

## File Structure

- **Create:** `artmind/graph_snapshot.py` — export/import logic (export_graph, import_graph, helpers)
- **Create:** `test/test_graph_snapshot.py` — unit tests for snapshot logic + CLI tests
- **Modify:** `paths.py:21` — add `GRAPH_SNAPSHOT_DIR`
- **Modify:** `artmind/cli.py:1-41` — add imports and `session` CLI group with `close` and `initiate` commands
- **Modify:** `justfile` — add `session-close` and `session-initiate` recipes

---

### Task 1: Add GRAPH_SNAPSHOT_DIR path constant

**Files:**
- Modify: `paths.py:19-21`

- [ ] **Step 1: Add GRAPH_SNAPSHOT_DIR to paths.py**

In `paths.py`, add after `REFINE_DIR` (line 19):

```python
GRAPH_SNAPSHOT_DIR = DATA_DIR / "graph_snapshot"
```

- [ ] **Step 2: Commit**

```bash
git add paths.py
git commit -m "feat: add GRAPH_SNAPSHOT_DIR path constant

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Create graph_snapshot module — export logic

**Files:**
- Create: `artmind/graph_snapshot.py`
- Create: `test/test_graph_snapshot.py`

- [ ] **Step 1: Write failing tests for export helper functions**

Create `test/test_graph_snapshot.py`:

```python
import json
import tarfile
from pathlib import Path

import pytest

from artmind.graph_snapshot import (
    _match_keys_for_node,
    _find_latest_snapshot,
)


class TestMatchKeysForNode:
    def test_entity_uses_name_class_domain(self):
        labels = ["CHARACTER", "Entity"]
        props = {"name": "Elara", "entity_class": "CHARACTER", "domain": "fiction", "id": "abc"}
        assert _match_keys_for_node(labels, props) == {
            "name": "Elara",
            "entity_class": "CHARACTER",
            "domain": "fiction",
        }

    def test_document_uses_id(self):
        labels = ["Document"]
        props = {"id": "doc1", "name": "test.pdf", "domain": "fiction"}
        assert _match_keys_for_node(labels, props) == {"id": "doc1"}

    def test_docchunk_uses_id(self):
        labels = ["DocChunk"]
        props = {"id": "chunk1", "doc_id": "doc1", "text": "hello"}
        assert _match_keys_for_node(labels, props) == {"id": "chunk1"}

    def test_userchat_uses_id(self):
        labels = ["UserChat"]
        props = {"id": "chat1", "raw_text": "hello"}
        assert _match_keys_for_node(labels, props) == {"id": "chat1"}

    def test_unknown_label_falls_back_to_id(self):
        labels = ["SomeNewLabel"]
        props = {"id": "x1", "foo": "bar"}
        assert _match_keys_for_node(labels, props) == {"id": "x1"}


class TestFindLatestSnapshot:
    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path / "nonexistent")
        assert _find_latest_snapshot() is None

    def test_returns_none_when_dir_empty(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        assert _find_latest_snapshot() is None

    def test_returns_latest_by_name(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        (tmp_path / "snapshot_2026-05-01_100000.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-09_140000.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-05_120000.tar.gz").write_text("")
        result = _find_latest_snapshot()
        assert result.name == "snapshot_2026-05-09_140000.tar.gz"

    def test_ignores_non_snapshot_files(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        (tmp_path / "random_file.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-01_100000.tar.gz").write_text("")
        result = _find_latest_snapshot()
        assert result.name == "snapshot_2026-05-01_100000.tar.gz"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --group dev pytest test/test_graph_snapshot.py -v
```

Expected: FAIL with import errors (module doesn't exist yet).

- [ ] **Step 3: Create artmind/graph_snapshot.py with export logic**

Create `artmind/graph_snapshot.py`:

```python
import json
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from neo4j.graph import Node, Relationship

from artmind.graph_query import neo4j_session
from artmind.ingest import _sanitize_label, _flatten_props
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --group dev pytest test/test_graph_snapshot.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_snapshot.py test/test_graph_snapshot.py
git commit -m "feat: add graph_snapshot module with export logic and tests

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Add import logic to graph_snapshot module

**Files:**
- Modify: `artmind/graph_snapshot.py`
- Modify: `test/test_graph_snapshot.py`

- [ ] **Step 1: Write failing tests for tar extraction and snapshot reading**

Append to `test/test_graph_snapshot.py`:

```python
from artmind.graph_snapshot import _read_snapshot


class TestReadSnapshot:
    def test_reads_valid_tar_gz(self, tmp_path):
        snapshot_data = {
            "meta": {"exported_at": "2026-05-09T14:00:00", "node_counts": {}, "relationship_count": 0},
            "schema": {},
            "nodes": {"Document": [], "DocChunk": [], "Entity": [], "UserChat": []},
            "relationships": [],
        }
        tar_path = tmp_path / "snapshot_2026-05-09_140000.tar.gz"
        json_path = tmp_path / "snapshot.json"
        json_path.write_text(json.dumps(snapshot_data), encoding="utf-8")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(json_path, arcname="snapshot.json")
        json_path.unlink()

        result = _read_snapshot(tar_path)
        assert result["meta"]["exported_at"] == "2026-05-09T14:00:00"
        assert result["nodes"]["Document"] == []

    def test_raises_on_missing_snapshot_json(self, tmp_path):
        tar_path = tmp_path / "bad.tar.gz"
        json_path = tmp_path / "other.json"
        json_path.write_text("{}")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(json_path, arcname="other.json")
        json_path.unlink()

        with pytest.raises(ValueError, match="snapshot.json"):
            _read_snapshot(tar_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --group dev pytest test/test_graph_snapshot.py::TestReadSnapshot -v
```

Expected: FAIL with import error (`_read_snapshot` not found).

- [ ] **Step 3: Add import functions to graph_snapshot.py**

Add these functions to `artmind/graph_snapshot.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --group dev pytest test/test_graph_snapshot.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_snapshot.py test/test_graph_snapshot.py
git commit -m "feat: add import logic (wipe, restore nodes/relationships)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Add CLI commands

**Files:**
- Modify: `artmind/cli.py`
- Modify: `test/test_graph_snapshot.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `test/test_graph_snapshot.py`:

```python
from unittest.mock import patch
from click.testing import CliRunner
from artmind.cli import cli


class TestSessionCloseCli:
    def test_exports_and_shows_summary(self):
        runner = CliRunner()
        fake_path = Path("/tmp/snapshot_2026-05-09_140000.tar.gz")
        with patch("artmind.cli.export_graph", return_value=fake_path) as mock_export, \
             patch.object(fake_path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 1_500_000
            result = runner.invoke(cli, ["session", "close"])
        assert result.exit_code == 0, result.output
        mock_export.assert_called_once()
        assert "snapshot_2026-05-09_140000.tar.gz" in result.output


class TestSessionInitiateCli:
    def test_prompts_and_imports(self):
        runner = CliRunner()
        summary = {
            "snapshot": "snapshot_2026-05-09_140000.tar.gz",
            "node_counts": {"Document": 2, "DocChunk": 10, "Entity": 50, "UserChat": 1},
            "relationship_count": 100,
            "elapsed_seconds": 3.5,
        }
        with patch("artmind.cli.import_graph", return_value=summary):
            result = runner.invoke(cli, ["session", "initiate", "--yes"])
        assert result.exit_code == 0, result.output
        assert "Document" in result.output
        assert "100" in result.output

    def test_aborts_without_confirmation(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["session", "initiate"], input="n\n")
        assert result.exit_code != 0 or "Aborted" in result.output

    def test_uses_explicit_snapshot_path(self):
        runner = CliRunner()
        summary = {
            "snapshot": "custom.tar.gz",
            "node_counts": {"Document": 1},
            "relationship_count": 5,
            "elapsed_seconds": 1.0,
        }
        with patch("artmind.cli.import_graph", return_value=summary) as mock_import:
            result = runner.invoke(
                cli, ["session", "initiate", "--yes", "--snapshot", "/tmp/custom.tar.gz"]
            )
        assert result.exit_code == 0, result.output
        mock_import.assert_called_once()
        call_args = mock_import.call_args
        assert str(call_args[1].get("snapshot_path") or call_args[0][0]) == "/tmp/custom.tar.gz"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --group dev pytest test/test_graph_snapshot.py::TestSessionCloseCli -v
```

Expected: FAIL (no `session` command in cli).

- [ ] **Step 3: Add session CLI group and commands to cli.py**

Add import at the top of `artmind/cli.py` (near the other imports from artmind modules):

```python
from artmind.graph_snapshot import export_graph, import_graph
```

Add the CLI group and commands at the end of `cli.py` (before the `setup` command, after the `update` group):

```python
# ── artmind session ────────────────────────────────────────────────────────────


@cli.group()
def session():
    """Save and restore the Neo4j graph between sessions."""
    pass


@session.command("close")
def session_close():
    """Export the full Neo4j graph to a compressed snapshot (end of session)."""
    _setup_logger()
    try:
        snapshot_path = export_graph()
        size_mb = snapshot_path.stat().st_size / (1024 * 1024)
        click.echo(f"Snapshot saved: {snapshot_path}")
        click.echo(f"  Size: {size_mb:.2f} MB")
    except Exception as e:
        raise click.ClickException(str(e))


@session.command("initiate")
@click.option("--snapshot", "snapshot_file", default=None, type=click.Path(exists=True),
              help="Path to a specific snapshot .tar.gz (default: latest in data/graph_snapshot/)")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def session_initiate(snapshot_file: str | None, yes: bool):
    """Wipe Neo4j and restore from a snapshot (start of session)."""
    _setup_logger()
    if not yes:
        env = load_env()
        db_name = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
        if not click.confirm(
            f"This will delete all data in Neo4j database '{db_name}'. Continue?"
        ):
            raise click.Abort()

    snapshot_path = Path(snapshot_file) if snapshot_file else None
    try:
        summary = import_graph(snapshot_path)
        click.echo(f"Restored from: {summary['snapshot']}")
        node_counts = summary.get("node_counts", {})
        parts = [f"{label}: {count}" for label, count in node_counts.items()]
        click.echo(f"  Nodes: {' | '.join(parts)}")
        click.echo(f"  Relationships: {summary['relationship_count']}")
        click.echo(f"  Elapsed: {summary['elapsed_seconds']}s")
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
uv run --group dev pytest test/test_graph_snapshot.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_graph_snapshot.py
git commit -m "feat: add 'artmind session close' and 'artmind session initiate' CLI commands

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Add justfile recipes and final verification

**Files:**
- Modify: `justfile`

- [ ] **Step 1: Add justfile recipes**

Add at the end of `justfile`:

```just
# ── artmind session ───────────────────────────────────────────────────────────

# export Neo4j graph to a snapshot (end of session)
session-close:
    uv run artmind session close

# wipe Neo4j and restore from latest snapshot (start of session)
session-initiate:
    uv run artmind session initiate --yes
```

- [ ] **Step 2: Verify CLI help renders correctly**

```bash
uv run artmind session --help
uv run artmind session close --help
uv run artmind session initiate --help
```

Expected: Help text displays for all three commands showing the descriptions and options.

- [ ] **Step 3: Run full test suite**

```bash
uv run --group dev pytest test/ -v
```

Expected: All existing tests still pass, plus new snapshot tests pass.

- [ ] **Step 4: Commit**

```bash
git add justfile
git commit -m "feat: add session-close and session-initiate justfile recipes

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
