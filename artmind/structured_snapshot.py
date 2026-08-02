"""Snapshot/restore for the structured store: parquet files + the five
SQLite registry tables (datasources/tables/columns/column_mappings/column_roles).

Mirrors artmind/graph_snapshot.py's export/import shape, but the payload is
registry rows + parquet bytes rather than Neo4j nodes/relationships, and
restoring must reinsert registry rows with their original ids so
columns.table_id / column_mappings.table_id foreign keys stay valid — see
registry.restore_all().

Uses ``import paths`` + attribute access (matching duckdb_adapter.py), not
``from paths import ...`` (graph_snapshot.py's style) — this module's tests,
and the shared CLI test fixture, monkeypatch ``paths.STRUCTURED_DIR`` /
``paths.STRUCTURED_SNAPSHOT_DIR`` directly, which only a dynamic lookup sees.
"""

import json
import shutil
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

import paths
from artmind.structured import registry
from artmind.structured.duckdb_adapter import DuckDBDatasource


def _find_latest_structured_snapshot() -> Path | None:
    """Return the newest structured snapshot .tar.gz, or None."""
    if not paths.STRUCTURED_SNAPSHOT_DIR.exists():
        return None
    snapshots = sorted(paths.STRUCTURED_SNAPSHOT_DIR.glob("structured_snapshot_*.tar.gz"))
    return snapshots[-1] if snapshots else None


def export_structured() -> Path:
    """Tar up every registered parquet file plus a JSON dump of the four
    registry tables. Returns the path to the created .tar.gz."""
    t0 = time.monotonic()
    dump = registry.dump_all()

    paths.STRUCTURED_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = paths.STRUCTURED_SNAPSHOT_DIR / f"structured_snapshot_{timestamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        registry_json = tmp_path / "registry.json"
        registry_json.write_text(
            json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(registry_json, arcname="registry.json")
            for table_row in dump["tables"]:
                parquet_path = Path(table_row["parquet_path"])
                if parquet_path.exists():
                    arcname = f"parquet/{table_row['domain']}/{table_row['table_name']}.parquet"
                    tar.add(parquet_path, arcname=arcname)

    elapsed = time.monotonic() - t0
    logger.info(
        "Structured snapshot exported in {:.1f}s: {} table(s), {:.2f} MB",
        elapsed, len(dump["tables"]), dest.stat().st_size / (1024 * 1024),
    )
    return dest


def import_structured(snapshot_path: Path | None = None) -> dict:
    """Wipe the structured store and restore from a snapshot.

    If snapshot_path is None, uses the latest snapshot in
    paths.STRUCTURED_SNAPSHOT_DIR. Returns a summary dict.
    """
    if snapshot_path is None:
        snapshot_path = _find_latest_structured_snapshot()
    if snapshot_path is None:
        raise FileNotFoundError(
            "No structured snapshots found in " + str(paths.STRUCTURED_SNAPSHOT_DIR)
        )

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with tarfile.open(snapshot_path, "r:gz") as tar:
            tar.extractall(tmp_path, filter="data")
        dump = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))

        if paths.STRUCTURED_DIR.exists():
            shutil.rmtree(paths.STRUCTURED_DIR)
        paths.STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)

        parquet_src = tmp_path / "parquet"
        if parquet_src.exists():
            for domain_dir in parquet_src.iterdir():
                shutil.copytree(
                    domain_dir, paths.STRUCTURED_DIR / domain_dir.name, dirs_exist_ok=True
                )

        registry.restore_all(dump)

        ds = DuckDBDatasource()
        ds.ensure_views(registry.list_tables())

    elapsed = time.monotonic() - t0
    logger.info(
        "Structured snapshot restored in {:.1f}s: {} table(s)",
        elapsed, len(dump["tables"]),
    )
    return {
        "snapshot": snapshot_path.name,
        "table_count": len(dump["tables"]),
        "elapsed_seconds": round(elapsed, 1),
    }
