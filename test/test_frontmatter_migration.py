"""`artmind vault migrate-frontmatter` (spec 2026-09-26 §5 R6, §12 item 5):
per-ingest fields move from each note into its staging document.json, and
each note is rewritten once, without them -- body, the user's keys, their
order and line endings byte-for-byte. Real throwaway git repos; no graph."""
import json
import subprocess

import pytest
from click.testing import CliRunner

from artmind import frontmatter_migration as fmm
from artmind.document_identity import compute_content_sha256

BODY = "\r\n# Policy\r\n\r\nBody text.\r\n"
SHA = compute_content_sha256("# Policy\n\nBody text.\n")


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def _fat(version=2, sha=SHA):
    """A note as pre-R6 artmind left it (CRLF; the user's own keys around
    artmind's)."""
    return (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        f"_version: {version}\r\n"
        f"_content_sha256: {sha}\r\n"
        "_domain: general\r\n"
        "_status: latest\r\n"
        "_source_commit: " + "c" * 40 + "\r\n"
        "_source_path: notes/policy.md\r\n"
        "_source_type: md\r\n"
        "_ingested_at: '2026-09-01T10:00:00+00:00'\r\n"
        "title: Policy\r\n"
        "tags: [b, a]  # mine\r\n"
        "---\r\n" + BODY
    )


MINIMAL = "---\r\n_artmind_id: id-1\r\n_domain: general\r\ntitle: Policy\r\ntags: [b, a]  # mine\r\n---\r\n" + BODY


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    import paths

    root = tmp_path / "v"
    (root / ".artmind" / "data").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    monkeypatch.setattr(paths, "KG_DIR", root / ".artmind" / "data" / "kg")
    monkeypatch.setattr(paths, "MARKDOWNS_DIR", root / ".artmind" / "data" / "documents" / "markdowns")
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    (root / "notes").mkdir()
    note = root / "notes" / "policy.md"
    note.write_bytes(_fat().encode())
    return root, note


def _stage(root, document, domain="general", stem="policy"):
    folder = root / ".artmind" / "data" / "kg" / domain / stem
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(json.dumps(document, indent=2, ensure_ascii=False))
    (folder / "observations.json").write_text("[]")
    return folder


def test_a_fat_note_moves_its_fields_into_document_json_and_is_stripped_once(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2, "name": "policy.md"})

    report = fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert json.loads((folder / "document.json").read_text()) == {
        "id": "id-1", "version": 2, "name": "policy.md",
        "content_sha256": SHA, "source_commit": "c" * 40, "ingested_at": "2026-09-01T10:00:00+00:00",
        "source_path": "notes/policy.md", "source_type": "md",
    }
    assert (folder / "observations.json").read_text() == "[]", "the rest of the folder is carried over"
    assert report["migrated"] == [{
        "path": "notes/policy.md",
        "removed": ["_version", "_content_sha256", "_source_commit", "_ingested_at", "_source_path", "_source_type", "_status"],
        "document_json": {
            "folder": ".artmind/data/kg/general/policy",
            "added": ["content_sha256", "ingested_at", "source_commit", "source_path", "source_type"],
        },
    }]
    assert list((root / ".artmind" / "data").glob("*.artmind-tmp")) == []

    before = (note.read_bytes(), (folder / "document.json").read_bytes())
    again = fmm.migrate(root)

    assert (again["migrated"], again["already_minimal"]) == ([], 1)
    assert (note.read_bytes(), (folder / "document.json").read_bytes()) == before


def test_a_dry_run_reports_and_writes_nothing(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2})
    doc_before = (folder / "document.json").read_bytes()

    report = fmm.migrate(root, dry_run=True)

    assert report["dry_run"] is True
    assert [m["path"] for m in report["migrated"]] == ["notes/policy.md"]
    assert note.read_bytes() == _fat().encode()
    assert (folder / "document.json").read_bytes() == doc_before


def test_a_value_a_newer_ingest_recorded_is_never_overwritten(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 3, "content_sha256": "newer", "ingested_at": "2026-09-20"})

    report = fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert (staged["version"], staged["content_sha256"], staged["ingested_at"]) == (3, "newer", "2026-09-20")
    assert note.read_bytes() == MINIMAL.encode()
    assert report["version_mismatch"] == ["notes/policy.md"]


def test_fields_of_an_extraction_that_never_landed_are_not_moved(vault):
    """The note says v3 but the staged extraction is v2 (v3's extraction
    failed): v3's hash must not be written beside v2's observations, or the
    next ingest would skip the extraction the note still needs."""
    root, note = vault
    note.write_bytes(_fat(version=3).encode())
    folder = _stage(root, {"id": "id-1", "version": 2})

    report = fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert "content_sha256" not in staged and "ingested_at" not in staged
    assert (staged["source_path"], staged["source_type"]) == ("notes/policy.md", "md")
    assert report["version_mismatch"] == ["notes/policy.md"]
    assert note.read_bytes() == MINIMAL.encode()


def test_a_note_never_extracted_keeps_its_fields_and_is_reported(vault):
    root, note = vault

    report = fmm.migrate(root)

    assert note.read_bytes() == _fat().encode()
    assert [s["path"] for s in report["skipped"]] == ["notes/policy.md"]
    assert "no staging folder" in report["skipped"][0]["reason"]


def test_a_renamed_note_finds_its_folder_by_id(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2}, stem="old-name")

    fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert json.loads((folder / "document.json").read_text())["content_sha256"] == SHA


def test_an_interrupted_run_resumes(vault, monkeypatch):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2})
    from artmind import frontmatter

    real = frontmatter.write_note

    def _crash(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(frontmatter, "write_note", _crash)
    with pytest.raises(KeyboardInterrupt):
        fmm.migrate(root)
    assert note.read_bytes() == _fat().encode(), "the note is untouched: the crash hit before its rewrite"
    staged_after_crash = (folder / "document.json").read_bytes()

    monkeypatch.setattr(frontmatter, "write_note", real)
    report = fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert (folder / "document.json").read_bytes() == staged_after_crash
    assert report["document_json_updated"] == 0


def test_a_converted_binarys_markdown_is_migrated_too(vault):
    root, _note = vault
    markdowns = root / ".artmind" / "data" / "documents" / "markdowns"
    markdowns.mkdir(parents=True)
    derived = markdowns / "deck.md"
    derived.write_text(
        "---\n_artmind_id: id-2\n_version: 1\n_domain: general\n_source_sha256: bin\n"
        "_source_type: pptx\ntitle: deck\n---\n\n# Deck\n"
    )
    folder = _stage(root, {"id": "id-2", "version": 1}, stem="deck")

    fmm.migrate(root)

    assert derived.read_text() == "---\n_artmind_id: id-2\n_domain: general\ntitle: deck\n---\n\n# Deck\n"
    staged = json.loads((folder / "document.json").read_text())
    assert (staged["source_sha256"], staged["source_type"]) == ("bin", "pptx")


def test_a_migrated_note_ingests_as_unchanged_and_is_not_rewritten(vault, tmp_path, monkeypatch):
    """Backward compatibility, end to end: after migration the next ingest
    finds its baseline in document.json -- same version, no re-extraction,
    no rewrite."""
    import artmind.db as db
    import artmind.document_identity as di
    import artmind.ingest as ing
    import paths

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    fmm.migrate(root)
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(ing, "KG_DIR", paths.KG_DIR)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)

    result = ing.ingest_file(note, "gemma4:e4b", "general", chunk_size=6000)

    assert (result["tier"], result["version"], result["artmind_id"]) == ("metadata_only", 2, "id-1")
    assert note.read_bytes() == MINIMAL.encode()


def test_refuses_during_a_merge(vault):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    (root / "x.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "other")
    (root / "x.md").write_text("theirs\n")
    _git(root, "commit", "-qam", "theirs")
    _git(root, "checkout", "-q", "-")
    (root / "x.md").write_text("ours\n")
    _git(root, "commit", "-qam", "ours")
    subprocess.run(["git", "merge", "-q", "other"], cwd=root, capture_output=True)

    with pytest.raises(fmm.MigrationError, match="a merge is in progress"):
        fmm.migrate(root)
    assert note.read_bytes() == _fat().encode()


def test_refuses_while_the_worker_runs(vault):
    """A live worker holds an exclusive flock on the vault's pid file (the
    liveness test `vault sync` uses); hold it the same way."""
    import fcntl
    import os

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    pid_file = root / ".artmind" / "worker.pid"
    pid_file.write_text(str(os.getpid()))
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(fmm.MigrationError, match="worker is running"):
            fmm.migrate(root)
    finally:
        holder.close()
    assert note.read_bytes() == _fat().encode()


def test_the_cli_prints_the_pause_notice_first_and_reports_json(vault, monkeypatch):
    from artmind.cli import cli

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    monkeypatch.setattr("artmind.vault.resolve_vault", lambda explicit=None: root)

    result = CliRunner().invoke(cli, ["vault", "migrate-frontmatter", "--dryRun", "--compact"])

    assert result.exit_code == 0, result.output
    assert "pause Obsidian Git" in result.stderr
    assert json.loads(result.stdout)["migrated"][0]["path"] == "notes/policy.md"
    assert note.read_bytes() == _fat().encode()


# ── review fixes: malformed inputs, safety, duplicates ─────────────────────────


def _reasons(report):
    return {s["path"]: s["reason"] for s in report["skipped"]}


def _snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_unparsable_yaml_is_skipped_and_reported_not_counted_minimal(vault):
    root, note = vault
    bad = "---\n_artmind_id: id-1\n_version: [unclosed\n_domain: general\n---\n\nBody\n"
    note.write_bytes(bad.encode())
    _stage(root, {"id": "id-1", "version": 2})

    report = fmm.migrate(root)

    assert note.read_bytes() == bad.encode()
    assert report["already_minimal"] == 0 and report["migrated"] == []
    assert "not valid YAML" in _reasons(report)["notes/policy.md"]


def test_a_note_behind_a_bom_is_skipped_and_reported(vault):
    root, note = vault
    bom = b"\xef\xbb\xbf" + _fat().encode()
    note.write_bytes(bom)
    _stage(root, {"id": "id-1", "version": 2})

    report = fmm.migrate(root)

    assert note.read_bytes() == bom
    assert report["already_minimal"] == 0
    assert "byte-order mark" in _reasons(report)["notes/policy.md"]


def test_a_bom_note_without_frontmatter_is_simply_minimal(vault):
    root, note = vault
    note.write_bytes(b"\xef\xbb\xbf# Just a heading\n")

    report = fmm.migrate(root)

    assert (report["already_minimal"], report["skipped"]) == (1, [])


@pytest.mark.parametrize("text, why", [
    ("---\n5\n---\n\nBody\n", "not a YAML mapping"),
    ("---\n_artmind_id: 123\n_domain: general\n_version: 1\n---\n", "must be strings"),
    ("---\n_artmind_id: id-1\n_domain: [a]\n_version: 1\n---\n", "must be strings"),
    ("---\n_artmind_id: id-1\n_domain: general\n_version: 2\n_source_type: !!binary aGk=\n---\n", "not a plain value"),
    ("---\n_artmind_id: id-1\n_domain: general\n_version: 2\n_source_path: [a, b]\n---\n", "not a plain value"),
    ("---\n_artmind_id: id-1\n_domain: general\n_version: 2\n_source_path: .nan\n---\n", "not a plain value"),
])
def test_a_malformed_note_is_skipped_and_the_run_carries_on(vault, text, why):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    _stage(root, {"id": "id-2", "version": 2}, stem="aaa")
    text = text.replace("id-1", "id-2")
    (root / "notes" / "aaa.md").write_bytes(text.encode())     # sorts before policy.md

    report = fmm.migrate(root)

    assert (root / "notes" / "aaa.md").read_bytes() == text.encode()
    assert why in _reasons(report)["notes/aaa.md"]
    assert note.read_bytes() == MINIMAL.encode(), "the well-formed note still migrated"
    assert [m["path"] for m in report["migrated"]] == ["notes/policy.md"]


def test_an_unexpected_failure_for_one_note_is_recorded_and_the_run_continues(vault, monkeypatch):
    root, note = vault
    other = root / "notes" / "aaa.md"
    other.write_bytes(_fat().replace("id-1", "id-2").encode())
    _stage(root, {"id": "id-2", "version": 2}, stem="aaa")
    _stage(root, {"id": "id-1", "version": 2})
    from artmind import frontmatter

    real = frontmatter.write_note

    def _boom(path, text, **k):
        if str(path).endswith("aaa.md"):
            raise RuntimeError("disk on fire")
        return real(path, text, **k)

    monkeypatch.setattr(frontmatter, "write_note", _boom)

    report = fmm.migrate(root)

    assert other.read_bytes() == _fat().replace("id-1", "id-2").encode(), "the note is untouched"
    assert "disk on fire" in _reasons(report)["notes/aaa.md"]
    assert [m["path"] for m in report["migrated"]] == ["notes/policy.md"]
    assert note.read_bytes() == MINIMAL.encode()


def test_a_note_that_fails_is_not_counted_as_migrated(vault, monkeypatch):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    from artmind import frontmatter

    monkeypatch.setattr(frontmatter, "write_note", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    report = fmm.migrate(root)

    assert (report["migrated"], report["document_json_updated"]) == ([], 0)
    assert len(report["skipped"]) == 1


@pytest.mark.parametrize("domain", ["../../outside", "/abs/elsewhere", "a/b", ".."])
def test_a_domain_that_is_not_a_plain_name_never_reaches_the_filesystem(vault, tmp_path, domain):
    root, note = vault
    fat = _fat().replace("_domain: general", f"_domain: '{domain}'")
    note.write_bytes(fat.encode())
    _stage(root, {"id": "id-1", "version": 2})
    before = _snapshot(tmp_path)

    report = fmm.migrate(root)

    assert _snapshot(tmp_path) == before
    assert "not a plain folder name" in _reasons(report)["notes/policy.md"]


def test_a_traversing_domain_does_not_touch_a_matching_folder_outside_the_kg_dir(vault, tmp_path):
    root, note = vault
    # kg/../../outside/policy == <vault>/.artmind/outside/policy ... build it exactly
    kg = root / ".artmind" / "data" / "kg"
    (kg / "general").mkdir(parents=True)
    outside = (kg / "../../outside" / "policy").resolve()
    outside.mkdir(parents=True)
    (outside / "document.json").write_text(json.dumps({"id": "id-1", "version": 2}))
    note.write_bytes(_fat().replace("_domain: general", "_domain: ../../outside").encode())
    before = (outside / "document.json").read_bytes()

    report = fmm.migrate(root)

    assert (outside / "document.json").read_bytes() == before
    assert note.read_bytes().startswith(b"---\r\n_artmind_id: id-1\r\n_version: 2")
    assert report["migrated"] == []


@pytest.mark.parametrize("copy_first", [True, False])
def test_a_copy_of_an_ingested_note_does_not_hijack_the_originals_folder(vault, copy_first):
    """Obsidian's "make a copy" carries `_artmind_id`. Whichever sorts first,
    the note named after the staging folder migrates and the copy is
    reported, its fields left alone."""
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2})
    copy = root / "notes" / ("Policy copy.md" if copy_first else "zz copy.md")
    copy.write_bytes(_fat(version=9).encode())
    assert (copy.name < note.name) is copy_first

    report = fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert copy.read_bytes() == _fat(version=9).encode()
    assert [m["path"] for m in report["migrated"]] == ["notes/policy.md"]
    assert "duplicate _artmind_id" in _reasons(report)[f"notes/{copy.name}"]
    staged = json.loads((folder / "document.json").read_text())
    assert staged["content_sha256"] == SHA and staged["version"] == 2


def test_a_renamed_note_is_still_found_by_id_when_the_id_is_unique(vault):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2}, stem="old-name")
    (root / "notes" / "other.md").write_bytes(_fat().replace("id-1", "id-7").encode())

    fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()


def test_a_symlink_to_a_note_outside_the_vault_is_skipped(vault, tmp_path):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    target = tmp_path / "elsewhere.md"
    target.write_bytes(_fat().encode())
    note.unlink()
    note.symlink_to(target)
    (root / "notes" / "real.md").write_bytes(b"---\n_artmind_id: id-5\n_domain: general\n---\n")

    report = fmm.migrate(root)

    assert target.read_bytes() == _fat().encode()
    assert note.is_symlink()
    assert "outside the vault" in _reasons(report)["notes/policy.md"]


def test_a_symlink_to_a_note_inside_the_vault_is_migrated(vault):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    (root / "elsewhere").mkdir()
    real = root / "elsewhere" / "policy.md"
    real.write_bytes(note.read_bytes())
    note.unlink()
    note.symlink_to(real)

    fmm.migrate(root)

    assert real.read_bytes() == MINIMAL.encode()


def test_no_version_on_either_side_is_not_a_match(vault):
    """Two Nones must not read as "the same version": the version-bound hash
    would land beside an extraction it does not describe."""
    root, note = vault
    note.write_bytes(_fat().replace("_version: 2\r\n", "").encode())
    folder = _stage(root, {"id": "id-1"})

    report = fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert not {"content_sha256", "source_commit", "ingested_at"} & set(staged)
    assert staged["source_type"] == "md"
    assert report["version_mismatch"] == ["notes/policy.md"]


def test_a_stem_folder_holding_another_notes_id_is_not_used(vault):
    root, note = vault
    _stage(root, {"id": "id-9", "version": 2}, stem="policy")          # someone else's
    real = _stage(root, {"id": "id-1", "version": 2}, stem="filed-under-another-name")

    fmm.migrate(root)

    assert json.loads((root / ".artmind/data/kg/general/policy/document.json").read_text()) == {"id": "id-9", "version": 2}
    assert json.loads((real / "document.json").read_text())["content_sha256"] == SHA
    assert note.read_bytes() == MINIMAL.encode()


@pytest.mark.parametrize("suffix", [".artmind-tmp", ".artmind-old"])
def test_leftover_swap_folders_are_never_a_staging_folder(vault, suffix):
    root, note = vault
    leftover = _stage(root, {"id": "id-1", "version": 2}, stem="policy" + suffix)
    before = (leftover / "document.json").read_bytes()

    report = fmm.migrate(root)

    assert (leftover / "document.json").read_bytes() == before
    assert note.read_bytes() == _fat().encode()
    assert "no staging folder" in _reasons(report)["notes/policy.md"]


def test_the_notes_temp_file_is_made_in_the_vaults_scratch_dir(vault, monkeypatch):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    from artmind import frontmatter

    seen = []
    real = frontmatter.write_note

    def _spy(path, text, **k):
        seen.append(k.get("scratch_dir"))
        return real(path, text, **k)

    monkeypatch.setattr(frontmatter, "write_note", _spy)

    fmm.migrate(root)

    assert seen == [root / ".artmind" / "data"]


def test_an_unquoted_yaml_timestamp_is_stored_as_iso_text(vault):
    root, note = vault
    note.write_bytes(_fat().replace("_ingested_at: '2026-09-01T10:00:00+00:00'", "_ingested_at: 2026-09-01T10:00:00+00:00").encode())
    folder = _stage(root, {"id": "id-1", "version": 2})

    fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert staged["ingested_at"] == "2026-09-01T10:00:00+00:00"


def test_plain_turns_dates_into_iso_text_and_leaves_the_rest():
    import datetime

    assert fmm._plain(datetime.date(2026, 9, 1)) == "2026-09-01"
    assert fmm._plain(datetime.datetime(2026, 9, 1, 10, tzinfo=datetime.timezone.utc)) == "2026-09-01T10:00:00+00:00"
    assert fmm._plain("x") == "x" and fmm._plain(3) == 3 and fmm._plain(None) is None


def test_a_note_with_a_moved_field_but_no_identity_is_skipped(vault):
    root, note = vault
    text = "---\n_version: 3\ntitle: T\n---\n\nBody\n"
    note.write_bytes(text.encode())

    report = fmm.migrate(root)

    assert note.read_bytes() == text.encode()
    assert "not an artmind note" in _reasons(report)["notes/policy.md"]


def test_the_cli_outside_a_vault_is_a_clean_error(tmp_path, monkeypatch):
    from artmind.cli import cli

    monkeypatch.setattr("artmind.vault.resolve_vault", lambda explicit=None: None)

    result = CliRunner().invoke(cli, ["vault", "migrate-frontmatter"])

    assert result.exit_code == 1
    assert "Not inside an artmind vault" in result.output
    assert "Traceback" not in result.output and isinstance(result.exception, SystemExit)


def test_a_vault_that_is_not_a_git_repo_is_a_clean_error(vault, monkeypatch):
    from artmind.cli import cli

    root, note = vault
    import shutil

    shutil.rmtree(root / ".git")
    monkeypatch.setattr("artmind.vault.resolve_vault", lambda explicit=None: root)

    result = CliRunner().invoke(cli, ["vault", "migrate-frontmatter", "--compact"])

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "git repository" in result.output
    assert note.read_bytes() == _fat().encode()


def test_the_staging_lookup_itself_refuses_a_name_that_could_leave_the_kg_dir(vault, tmp_path):
    root, _ = vault
    kg = root / ".artmind" / "data" / "kg"
    (kg / "general").mkdir(parents=True)
    (kg / "document.json").write_text(json.dumps({"id": "id-1"}))        # what stem ".." would reach
    outside = (kg / "../../outside" / "policy").resolve()
    outside.mkdir(parents=True)
    (outside / "document.json").write_text(json.dumps({"id": "id-1"}))
    index = fmm._StagingIndex(kg)

    assert index.by_stem("../../outside", "policy", "id-1") is None
    assert index.by_id("../../outside", "id-1") is None
    assert index.by_stem("general", "..", "id-1") is None
    assert index.by_stem("/", "x", "id-1") is None

