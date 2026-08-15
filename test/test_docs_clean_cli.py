"""Tests for `artmind docs clean` (soft tombstone) and `artmind docs purge` (hard).

A1d split the old hard-delete `docs clean` into two commands: `docs clean` now
soft-tombstones (reversible, knowledge preserved) and the new `docs purge` does
the hard scoped retraction + local/registry removal the old `clean` did.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import artmind.ingest as ingest_module
from artmind.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch):
    originals = tmp_path / "data" / "documents" / "originals"
    markdowns = tmp_path / "data" / "documents" / "markdowns"
    kg = tmp_path / "data" / "kg"
    db_path = tmp_path / "data" / "document_registry.db"

    originals.mkdir(parents=True)
    markdowns.mkdir(parents=True)
    kg.mkdir(parents=True)

    monkeypatch.setattr(ingest_module, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ingest_module, "MARKDOWNS_DIR", markdowns)
    monkeypatch.setattr(ingest_module, "KG_DIR", kg)
    monkeypatch.setattr(ingest_module, "DB_PATH", db_path)

    return {
        "originals": originals,
        "markdowns": markdowns,
        "kg": kg,
        "db_path": db_path,
    }


def _insert_document(db_path: Path, domain: str, filename: str, original_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE documents (
                id           INTEGER PRIMARY KEY,
                domain       TEXT NOT NULL,
                filename     TEXT NOT NULL,
                sha256       TEXT NOT NULL,
                original_path TEXT NOT NULL,
                added_at     TEXT NOT NULL,
                UNIQUE(filename),
                UNIQUE(sha256)
            )
            """
        )
        conn.execute(
            "INSERT INTO documents (domain, filename, sha256, original_path, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (domain, filename, "abc123", str(original_path), "2026-05-03T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


class TestDocsCleanCli:
    """`docs clean` → soft tombstone."""

    def test_clean_calls_tombstone_document(self, runner):
        tombstone_result = {
            "domain": "general",
            "document_name": "sample.pdf",
            "neo4j_documents": 1,
            "neo4j_chunks": 2,
            "neo4j_error": None,
        }
        with patch(
            "artmind.cli.tombstone_document", return_value=tombstone_result
        ) as mock_tombstone:
            result = runner.invoke(
                cli, ["docs", "clean", "--domain", "general", "sample.pdf"]
            )

        assert result.exit_code == 0, result.output
        mock_tombstone.assert_called_once_with("general", "sample.pdf")
        assert "Tombstoned 'sample.pdf'" in result.output
        assert "neo4j chunks: 2" in result.output

    def test_clean_neo4j_failure_returns_nonzero(self, runner):
        tombstone_result = {
            "domain": "general",
            "document_name": "sample.pdf",
            "neo4j_documents": 0,
            "neo4j_chunks": 0,
            "neo4j_error": "connection refused",
        }
        with patch("artmind.cli.tombstone_document", return_value=tombstone_result):
            result = runner.invoke(
                cli, ["docs", "clean", "--domain", "general", "sample.pdf"]
            )

        assert result.exit_code != 0
        assert "Neo4j tombstone failed" in result.output


class TestDocsPurgeCli:
    """`docs purge` → hard removal."""

    def test_purge_calls_purge_document(self, runner):
        purge_result = {
            "domain": "general",
            "document_name": "sample.pdf",
            "registry_rows": 1,
            "originals": 1,
            "markdowns": 1,
            "markdown_artifacts": 0,
            "kg_dirs": 1,
            "chunk_status_rows": 0,
            "neo4j_documents": 1,
            "neo4j_chunks": 2,
            "neo4j_orphan_entities": 3,
            "neo4j_error": None,
        }
        with patch(
            "artmind.cli.purge_document", return_value=purge_result
        ) as mock_purge:
            result = runner.invoke(
                cli, ["docs", "purge", "--domain", "general", "sample.pdf"]
            )

        assert result.exit_code == 0, result.output
        mock_purge.assert_called_once_with("general", "sample.pdf")
        assert "Purged 'sample.pdf'" in result.output
        assert "neo4j orphan entities: 3" in result.output

    def test_purge_neo4j_failure_returns_nonzero(self, runner):
        purge_result = {
            "domain": "general",
            "document_name": "sample.pdf",
            "registry_rows": 0,
            "originals": 0,
            "markdowns": 0,
            "markdown_artifacts": 0,
            "kg_dirs": 0,
            "chunk_status_rows": 0,
            "neo4j_documents": 0,
            "neo4j_chunks": 0,
            "neo4j_orphan_entities": 0,
            "neo4j_error": "connection refused",
        }
        with patch("artmind.cli.purge_document", return_value=purge_result):
            result = runner.invoke(
                cli, ["docs", "purge", "--domain", "general", "sample.pdf"]
            )

        assert result.exit_code != 0
        assert "Neo4j purge failed" in result.output


class TestPurgeDocument:
    def test_removes_local_files_registry_and_kg_dir(self, isolated_storage):
        originals = isolated_storage["originals"]
        markdowns = isolated_storage["markdowns"]
        kg = isolated_storage["kg"]
        db_path = isolated_storage["db_path"]

        original = originals / "sample.pdf"
        markdown = markdowns / "sample.md"
        artifacts = markdowns / "sample_artifacts"
        kg_dir = kg / "general" / "sample"

        original.write_text("pdf")
        markdown.write_text("md")
        artifacts.mkdir()
        (artifacts / "image.png").write_text("image")
        kg_dir.mkdir(parents=True)
        (kg_dir / "document.json").write_text("{}")
        _insert_document(db_path, "general", "sample.pdf", original)

        result = ingest_module.purge_document("general", "sample.pdf", delete_neo4j=False)

        assert result["registry_rows"] == 1
        assert result["originals"] == 1
        assert result["markdowns"] == 1
        assert result["markdown_artifacts"] == 1
        assert result["kg_dirs"] == 1
        assert not original.exists()
        assert not markdown.exists()
        assert not artifacts.exists()
        assert not kg_dir.exists()

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT filename FROM documents").fetchall()
        finally:
            conn.close()
        assert rows == []

    def test_clean_document_is_a_backcompat_alias_for_purge(self):
        # The hard-removal helper kept its old name as an alias after A1d renamed
        # it, so pre-A1d callers (and the module API) still resolve.
        assert ingest_module.clean_document is ingest_module.purge_document
