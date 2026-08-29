import json
import shutil
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from loguru import logger

from artmind.graph_snapshot import (
    export_graph as _export_graph_inner,
    _read_snapshot as _read_graph_snapshot,
    import_graph as _import_graph_inner,
)
from artmind.structured_snapshot import (
    export_structured as _export_structured_inner,
    import_structured as _import_structured_inner,
)
from artmind import vault_git
from paths import (
    ARTMIND_HOME,
    DOMAIN_SCHEMAS_DIR,
    GRAPH_SNAPSHOT_DIR,
    KG_DIR,
    ORIGINALS_DIR,
    STRUCTURED_SNAPSHOT_DIR,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _archive_kgs(temp_dir: Path) -> tuple[Path, dict]:
    """Archive all KG JSONs from KG_DIR. Returns (archive_path, metadata)."""
    archive_path = temp_dir / "kg_staging.tar.gz"

    if not KG_DIR.exists():
        # Create empty archive
        with tarfile.open(archive_path, "w:gz") as tar:
            pass
        return archive_path, {"document_count": 0, "size_bytes": 0}

    doc_count = sum(1 for _ in KG_DIR.rglob("*.json"))

    with tarfile.open(archive_path, "w:gz") as tar:
        for kg_file in KG_DIR.rglob("*.json"):
            arcname = kg_file.relative_to(KG_DIR.parent)
            tar.add(kg_file, arcname=arcname)

    size_bytes = archive_path.stat().st_size
    logger.debug("Archived {} KG JSON files ({:.2f} MB)", doc_count, size_bytes / (1024 * 1024))

    return archive_path, {
        "document_count": doc_count,
        "size_bytes": size_bytes,
        "archived_at": datetime.now().isoformat(),
    }


def _archive_curation(temp_dir: Path) -> tuple[Path, dict]:
    """Archive the curation layer: `same_as.yaml` (Phase 6, tolerate absence)
    and every domain schema. Explicit files only, NEVER a glob of the run
    folder — `ARTMIND_HOME/.env` holds `ARTMIND_KG_NEO4J_PASSWORD` and
    `ARTMIND_KG_OPENROUTER_API_KEY`, and a snapshot zip is exactly the
    artifact people hand to each other.
    """
    from artmind.same_as import SAME_AS_PATH

    archive_path = temp_dir / "curation.tar.gz"
    schema_files = sorted(DOMAIN_SCHEMAS_DIR.glob("*.yaml")) if DOMAIN_SCHEMAS_DIR.exists() else []
    has_same_as = SAME_AS_PATH.exists()

    with tarfile.open(archive_path, "w:gz") as tar:
        if has_same_as:
            tar.add(SAME_AS_PATH, arcname="curation/same_as.yaml")
        for schema_file in schema_files:
            tar.add(schema_file, arcname=f"curation/domains/schemas/{schema_file.name}")

    size_bytes = archive_path.stat().st_size
    logger.debug(
        "Archived curation: same_as.yaml={}, {} schema file(s) ({:.2f} MB)",
        has_same_as, len(schema_files), size_bytes / (1024 * 1024),
    )
    return archive_path, {
        "has_same_as": has_same_as,
        "schema_count": len(schema_files),
        "size_bytes": size_bytes,
        "archived_at": datetime.now().isoformat(),
    }


def _archive_originals(temp_dir: Path) -> tuple[Path, dict]:
    """Archive `documents/originals/` — artmind's ONLY copy of every ingested
    binary. In the default snapshot set on purpose (deliberate, counter-
    intuitive per docs/redesign-phase-plan.md's Phase 5 "C": omitting it is
    what lets a data-dir wipe silently lose every binary source forever)."""
    archive_path = temp_dir / "originals.tar.gz"

    if not ORIGINALS_DIR.exists():
        with tarfile.open(archive_path, "w:gz"):
            pass
        return archive_path, {"file_count": 0, "size_bytes": 0}

    files = [f for f in ORIGINALS_DIR.rglob("*") if f.is_file()]
    with tarfile.open(archive_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=f.relative_to(ORIGINALS_DIR.parent))

    size_bytes = archive_path.stat().st_size
    logger.debug("Archived {} original file(s) ({:.2f} MB)", len(files), size_bytes / (1024 * 1024))
    return archive_path, {
        "file_count": len(files),
        "size_bytes": size_bytes,
        "archived_at": datetime.now().isoformat(),
    }


def _create_manifest(components: dict) -> dict:
    """Create the manifest.json structure.

    `vault_commit`/`vault_dirty` turn "why does this restored graph
    reference a document that isn't there?" into a one-line diagnostic —
    both `None` when no vault is configured, never fatal to snapshot
    creation.
    """
    return {
        "created_at": datetime.now().isoformat(),
        "version": 1,
        "components": components,
        "vault_commit": vault_git.current_commit(),
        "vault_dirty": vault_git.is_dirty(),
    }


# ── export ────────────────────────────────────────────────────────────────────


VALID_COMPONENTS = {"graph", "structured", "kg_staging", "curation", "originals"}
# Phase 5 registry shrink: `registry` dropped from every component set below
# (docs/redesign-phase-plan.md, "C") — it's a pure path<->id cache now,
# rebuildable in seconds by `docs reindex`, and keeping a cache in a snapshot
# invites restoring a stale one over a correct one. `originals` joins the
# DEFAULT set (deliberate, and counter-intuitive — see `_archive_originals`);
# `curation` does not ship in the corpus's baseline snapshot config but is
# available to opt into via `--only`.
DEFAULT_COMPONENTS = {"graph", "structured", "kg_staging", "originals"}


def create_snapshot(include: set[str] | None = None) -> Path:
    """Create a unified snapshot zip containing selected components.

    Args:
        include: Set of component names to include. If None, uses
                DEFAULT_COMPONENTS. Valid: VALID_COMPONENTS.

    Returns:
        Path to the created .zip file.
    """
    if include is None:
        include = set(DEFAULT_COMPONENTS)

    invalid = include - VALID_COMPONENTS
    if invalid:
        raise ValueError(f"Invalid components: {invalid}")

    t0 = time.monotonic()
    components_meta = {}

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Export graph
        if "graph" in include:
            logger.info("Exporting graph...")
            graph_snapshot = _export_graph_inner()
            graph_data = _read_graph_snapshot(graph_snapshot)
            graph_copy = tmp_path / "graph_snapshot.tar.gz"
            shutil.copy2(graph_snapshot, graph_copy)
            components_meta["graph"] = {
                "node_counts": graph_data.get("meta", {}).get("node_counts", {}),
                "relationship_count": graph_data.get("meta", {}).get("relationship_count", 0),
                "size_bytes": graph_copy.stat().st_size,
                "exported_at": graph_data.get("meta", {}).get("exported_at", ""),
            }

        # Export structured data
        if "structured" in include:
            logger.info("Exporting structured data...")
            structured_snapshot = _export_structured_inner()
            structured_copy = tmp_path / "structured_snapshot.tar.gz"
            shutil.copy2(structured_snapshot, structured_copy)
            components_meta["structured"] = {
                "size_bytes": structured_copy.stat().st_size,
                "exported_at": datetime.now().isoformat(),
            }

        # Archive KG JSONs
        if "kg_staging" in include:
            logger.info("Archiving KG staging JSONs...")
            kg_path, kg_meta = _archive_kgs(tmp_path)
            components_meta["kg_staging"] = kg_meta

        # Archive curation (same_as.yaml + domain schemas)
        if "curation" in include:
            logger.info("Archiving curation...")
            curation_path, curation_meta = _archive_curation(tmp_path)
            components_meta["curation"] = curation_meta

        # Archive originals (documents/originals/ -- the only copy of every
        # ingested binary)
        if "originals" in include:
            logger.info("Archiving originals...")
            originals_path, originals_meta = _archive_originals(tmp_path)
            components_meta["originals"] = originals_meta

        # Create manifest
        manifest = _create_manifest(components_meta)
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # Create zip (flat structure: manifest.json at root, not nested)
        GRAPH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        zip_path = GRAPH_SNAPSHOT_DIR / f"artmind_snapshot_{timestamp}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in tmp_path.glob("*"):
                if file_path.is_file():
                    # Add file at root level, not nested
                    zf.write(file_path, arcname=file_path.name)
                elif file_path.is_dir():
                    # If it's a directory (shouldn't happen, but be safe)
                    for item in file_path.rglob("*"):
                        if item.is_file():
                            arcname = item.relative_to(tmp_path)
                            zf.write(item, arcname=str(arcname))

    elapsed = time.monotonic() - t0
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    logger.info(
        "Unified snapshot created in {:.1f}s: {} ({:.2f} MB)",
        elapsed, zip_path.name, size_mb,
    )
    return zip_path


# ── import ────────────────────────────────────────────────────────────────────


def _read_manifest_from_zip(zip_path: Path) -> dict:
    """Read just the manifest from a snapshot zip without extracting."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find manifest.json (may be nested in a directory)
            manifest_in_zip = None
            for name in zf.namelist():
                if name.endswith("manifest.json"):
                    manifest_in_zip = name
                    break

            if not manifest_in_zip:
                raise ValueError("Snapshot missing manifest.json")

            zf.extract(manifest_in_zip, path=tmp_path)

        manifest_path = tmp_path / manifest_in_zip
        return json.loads(manifest_path.read_text(encoding="utf-8"))


def _warn_stale_state(manifest: dict, restore_components: set[str]) -> None:
    """Warn if restoring a subset might result in inconsistency."""
    all_available = set(manifest.get("components", {}).keys())
    missing = all_available - restore_components

    if not missing:
        return  # Restoring everything, no issue

    if "graph" in restore_components and missing:
        logger.warning(
            "⚠️  Restoring graph but skipping: {}. "
            "Your graph may be out of sync with structured/originals data.",
            ", ".join(sorted(missing)),
        )
    elif "structured" in restore_components and missing:
        logger.warning(
            "⚠️  Restoring structured data but skipping: {}. "
            "SQL tables may not match the knowledge graph.",
            ", ".join(sorted(missing)),
        )


def _extract_snapshot_files(zip_path: Path) -> tuple[Path, dict]:
    """Extract snapshot zip to persistent temp dir. Returns (extract_dir, manifest).

    Handles both flat structure (manifest.json at root) and nested structure
    (artmind_snapshot_*/manifest.json). Caller is responsible for cleanup.
    """
    extract_dir = Path(tempfile.mkdtemp(prefix="artmind_restore_"))
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # Find manifest.json (may be nested in a directory)
    manifest_path = None
    for path in extract_dir.rglob("manifest.json"):
        manifest_path = path
        break

    if not manifest_path or not manifest_path.exists():
        shutil.rmtree(extract_dir)
        raise ValueError("Snapshot missing manifest.json")

    # If manifest is nested, move all files to extract_dir root
    manifest_dir = manifest_path.parent
    if manifest_dir != extract_dir:
        for item in manifest_dir.iterdir():
            if item.name != "manifest.json":
                dest = extract_dir / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            else:
                shutil.move(str(item), str(extract_dir / item.name))
        # Remove the now-empty nested directory
        if manifest_dir != extract_dir:
            shutil.rmtree(manifest_dir)

    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return extract_dir, manifest


def restore_snapshot_impl(
    zip_path: Path,
    include: set[str] | None = None,
    *,
    rebuild_projection: bool | None = None,
    progress_cb=None,
) -> dict:
    """Restore components from a unified snapshot.

    Args:
        zip_path: Path to the snapshot .zip file.
        include: Set of component names to restore. If None, restores all available.
        rebuild_projection: Passed straight through to `graph_snapshot.import_graph`
            as its `force_rebuild` — `None` (default) auto-detects whether the
            restored `:Entity` layer is trustworthy, `True`/`False` force a
            rebuild on or off regardless of what's detected. Only meaningful
            when "graph" is restored.
        progress_cb: Passed straight through to `graph_snapshot.import_graph`,
            called `progress_cb(phase, detail)` at each of its phase
            boundaries. Only meaningful when "graph" is restored — the other
            components are comparatively quick and don't report phases.

    Returns:
        Summary dict with restoration results per component. If "structured"
        was restored, this also (best-effort) rebuilds Neo4j's structured-data
        catalogue subgraph from the freshly-restored registry and reports it
        under the "catalogue" key — that subgraph is a derived projection the
        graph snapshot itself never carries (see graph_snapshot._export_relationships).
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {zip_path}")

    t0 = time.monotonic()
    extract_dir = None
    restored_components = {}
    errors = []

    try:
        # Extract and read manifest
        extract_dir, manifest = _extract_snapshot_files(zip_path)
        available = set(manifest.get("components", {}).keys())

        if include is None:
            include = available
        else:
            invalid = include - VALID_COMPONENTS
            if invalid:
                raise ValueError(f"Invalid components: {invalid}")
            include = include & available

        # Warn about staleness
        _warn_stale_state(manifest, include)

        # Restore graph
        if "graph" in include:
            try:
                logger.info("Restoring graph...")
                graph_path = extract_dir / "graph_snapshot.tar.gz"
                summary = _import_graph_inner(
                    graph_path, force_rebuild=rebuild_projection, progress_cb=progress_cb
                )
                restored_components["graph"] = summary
                logger.info("Graph restored: {} nodes, {} relationships",
                           sum(summary.get("node_counts", {}).values()),
                           summary.get("relationship_count", 0))
            except Exception as exc:
                errors.append(f"Graph restore failed: {exc}")
                logger.error("Graph restore failed: {}", exc)

        # Restore structured data
        if "structured" in include:
            try:
                logger.info("Restoring structured data...")
                structured_path = extract_dir / "structured_snapshot.tar.gz"
                summary = _import_structured_inner(structured_path)
                restored_components["structured"] = summary
                logger.info("Structured data restored")
            except Exception as exc:
                errors.append(f"Structured restore failed: {exc}")
                logger.error("Structured restore failed: {}", exc)

        # Restore curation (same_as.yaml + domain schemas)
        if "curation" in include:
            try:
                logger.info("Restoring curation...")
                curation_path = extract_dir / "curation.tar.gz"
                restored_files = 0
                with tarfile.open(curation_path, "r:gz") as tar:
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        # Members are archived as "curation/..." -- strip that
                        # prefix and re-root under ARTMIND_HOME, never trusting
                        # the archive's own path for where it lands.
                        rel = Path(member.name)
                        if rel.parts and rel.parts[0] == "curation":
                            rel = Path(*rel.parts[1:])
                        dest = ARTMIND_HOME / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
                        restored_files += 1
                restored_components["curation"] = {
                    "files_restored": restored_files,
                    "restored_at": datetime.now().isoformat(),
                }
                logger.info("Curation restored: {} file(s)", restored_files)
            except Exception as exc:
                errors.append(f"Curation restore failed: {exc}")
                logger.error("Curation restore failed: {}", exc)

        # Restore originals (documents/originals/)
        if "originals" in include:
            try:
                logger.info("Restoring originals...")
                originals_path = extract_dir / "originals.tar.gz"
                if ORIGINALS_DIR.exists():
                    backup_path = ORIGINALS_DIR.with_name(f"{ORIGINALS_DIR.name}.backup")
                    if backup_path.exists():
                        shutil.rmtree(backup_path)
                    shutil.move(str(ORIGINALS_DIR), str(backup_path))
                    logger.debug("Backed up existing originals to {}", backup_path)

                ORIGINALS_DIR.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(originals_path, "r:gz") as tar:
                    tar.extractall(ORIGINALS_DIR.parent)

                file_count = sum(1 for f in ORIGINALS_DIR.rglob("*") if f.is_file()) if ORIGINALS_DIR.exists() else 0
                restored_components["originals"] = {
                    "file_count": file_count,
                    "restored_at": datetime.now().isoformat(),
                }
                logger.info("Originals restored: {} file(s)", file_count)
            except Exception as exc:
                errors.append(f"Originals restore failed: {exc}")
                logger.error("Originals restore failed: {}", exc)

        # Rebuild the structured-store catalogue subgraph in Neo4j. It's a
        # derived projection (project_catalogue's own docstring: "nothing
        # authoritative lives in this subgraph"), and graph_snapshot.py
        # deliberately excludes it from graph export/import (see
        # _export_relationships), so a restore of "structured" leaves Neo4j's
        # copy stale/absent until something re-projects it. Best-effort:
        # Neo4j may legitimately be unreachable here, so a failure is
        # reported but doesn't fail the rest of the restore.
        if "structured" in restored_components:
            try:
                logger.info("Rebuilding structured-data catalogue subgraph...")
                from artmind.structured import registry as structured_registry
                from artmind.structured.catalogue import project_catalogue

                domains = sorted({
                    table["domain"].split(".", 1)[0]
                    for table in structured_registry.list_tables()
                })
                catalogue_summary = {"tables": 0, "columns": 0, "mappings": 0, "domains": domains}
                for domain in domains:
                    domain_result = project_catalogue(domain)
                    catalogue_summary["tables"] += domain_result["tables"]
                    catalogue_summary["columns"] += domain_result["columns"]
                    catalogue_summary["mappings"] += domain_result["mappings"]

                restored_components["catalogue"] = catalogue_summary
                logger.info(
                    "Catalogue rebuilt for {} domain(s): {} table(s), {} column(s), {} mapping(s)",
                    len(domains), catalogue_summary["tables"],
                    catalogue_summary["columns"], catalogue_summary["mappings"],
                )
            except Exception as exc:
                errors.append(f"Catalogue rebuild failed: {exc}")
                logger.error("Catalogue rebuild failed: {}", exc)

        # Restore KG staging JSONs
        if "kg_staging" in include:
            try:
                logger.info("Restoring KG staging JSONs...")
                kg_path = extract_dir / "kg_staging.tar.gz"

                # Remove existing KG dir and extract
                if KG_DIR.exists():
                    backup_path = KG_DIR.with_name(f"{KG_DIR.name}.backup")
                    if backup_path.exists():
                        shutil.rmtree(backup_path)
                    shutil.move(str(KG_DIR), str(backup_path))
                    logger.debug("Backed up existing KG dir to {}", backup_path)

                KG_DIR.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(kg_path, "r:gz") as tar:
                    tar.extractall(KG_DIR.parent)

                # Count extracted documents
                doc_count = sum(1 for _ in KG_DIR.rglob("*.json")) if KG_DIR.exists() else 0

                restored_components["kg_staging"] = {
                    "document_count": doc_count,
                    "restored_at": datetime.now().isoformat(),
                }
                logger.info("KG staging restored: {} JSON file(s)", doc_count)
            except Exception as exc:
                errors.append(f"KG staging restore failed: {exc}")
                logger.error("KG staging restore failed: {}", exc)

        elapsed = time.monotonic() - t0

        result = {
            "snapshot": zip_path.name,
            "components_restored": sorted(restored_components.keys()),
            "elapsed_seconds": round(elapsed, 1),
            "success": len(errors) == 0,
        }

        if restored_components:
            result["details"] = restored_components
        if errors:
            result["errors"] = errors

        if errors:
            logger.warning("Restore completed with {} error(s)", len(errors))
        else:
            logger.info("Restore complete in {:.1f}s", elapsed)

        return result

    finally:
        # Cleanup extracted files
        if extract_dir and extract_dir.exists():
            shutil.rmtree(extract_dir)
            logger.debug("Cleaned up temporary extraction dir")


def analyze_snapshot(zip_path: Path, include: set[str] | None = None) -> dict:
    """Analyze a snapshot without restoring it.

    Args:
        zip_path: Path to the snapshot .zip file.
        include: Set of component names to check. If None, uses all available.

    Returns:
        Analysis dict with manifest info and stale warnings.
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {zip_path}")

    t0 = time.monotonic()
    manifest = _read_manifest_from_zip(zip_path)
    available = set(manifest.get("components", {}).keys())

    if include is None:
        include = available
    else:
        invalid = include - VALID_COMPONENTS
        if invalid:
            raise ValueError(f"Invalid components: {invalid}")
        include = include & available

    # Check for stale state but don't raise, just collect warnings
    warnings = []
    if include != available:
        missing = available - include
        if "graph" in include and missing:
            warnings.append(
                f"Restoring graph but skipping: {', '.join(sorted(missing))}. "
                "Graph may be out of sync."
            )

    elapsed = time.monotonic() - t0
    logger.debug("Snapshot analysis complete in {:.1f}s", elapsed)

    return {
        "snapshot": zip_path.name,
        "available_components": sorted(available),
        "requested_components": sorted(include),
        "manifest": manifest,
        "warnings": warnings,
    }
