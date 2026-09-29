"""Integration-level tests for artmind.ingest._ingest_vault_native: the
orchestration of resolve_identity + the identity stamp (`artmind.frontmatter`)
+ decide_version against the staged baseline (`document.json`, spec
2026-09-26 R6) + _register_document against a real (temp) vault and registry,
with only Neo4j/LLM stubbed out. A note carries `_artmind_id`/`_domain` and
nothing else artmind writes; every per-ingest field travels in
`file_result["provenance"]`, which `extract_kg` records in `document.json`
(tests here stage it with `conftest.stage_as_extracted`). This is exactly the seam where a path-representation
mismatch between registration and resolution hid (registering with a raw
`.resolve()` while resolution looked up with `canonical_path()`) — a purely
unit-level test of either side in isolation would not have caught it.
"""
import json
import subprocess

import pytest

import artmind.delta
import artmind.ingest as ing
from conftest import stage_as_extracted

_REAL_APPLY_MOVE = getattr(artmind.delta, "apply_move", None)


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _RecordingGraphSession:
    """What `graph_query.neo4j_session()` yields, recording every `run`."""

    def __init__(self, runs):
        self.runs = runs

    def run(self, query, **params):
        self.runs.append((query, params))


@pytest.fixture()
def graph_runs(monkeypatch):
    """Every Cypher statement sent through `neo4j_session()`, as (query, params)."""
    runs: list = []

    class _Ctx:
        def __enter__(self):
            return _RecordingGraphSession(runs)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("artmind.graph_query.neo4j_session", lambda *a, **k: _Ctx())
    return runs


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "notes").mkdir(parents=True)
    doc = v / "notes" / "doc.md"
    doc.write_text("# Doc\n\nOriginal body.\n", encoding="utf-8")
    _init_git_repo(v)

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", v)
    import artmind.document_identity as di

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", v)

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    # No real Neo4j: the metadata-only fast path's graph update is a no-op.
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
    monkeypatch.setattr("artmind.delta.apply_move", lambda **k: None, raising=False)
    # Staging folders (document.json: the versioning baseline, spec R6).
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")

    return v, doc


def test_first_ingest_stamps_identity_only_and_keeps_the_body(vault):
    """Spec 2026-09-26 R6: a human note carries `_artmind_id` and `_domain`
    and nothing else artmind writes; every per-ingest field travels in
    `provenance`, which the extraction records in document.json."""
    v, doc = vault
    original = doc.read_bytes()
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["resolution_verdict"] == "new"
    assert result["version"] == 1
    assert result["tier"] == "content"

    assert doc.read_bytes() == (
        f"---\n_artmind_id: {result['artmind_id']}\n_domain: general\n---\n".encode() + original
    )
    assert result["provenance"]["source_path"] == "notes/doc.md"
    assert result["provenance"]["source_type"] == "md"
    assert result["provenance"]["content_sha256"] == result["content_sha256"]
    assert set(result["provenance"]) == {"content_sha256", "source_commit", "ingested_at", "source_path", "source_type"}


def test_stamping_keeps_the_users_own_frontmatter_and_crlf(vault):
    v, doc = vault
    doc.write_bytes(b"---\r\ntags: [b, a]  # mine\r\ntitle: Mine\r\n---\r\n\r\n# Doc\r\nBody.\r\n")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert doc.read_bytes() == (
        f"---\r\n_artmind_id: {result['artmind_id']}\r\n_domain: general\r\n".encode()
        + b"tags: [b, a]  # mine\r\ntitle: Mine\r\n---\r\n\r\n# Doc\r\nBody.\r\n"
    )


def test_a_domain_change_rewrites_only_the_domain_line(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    folder = stage_as_extracted(ing.KG_DIR, r1)
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "version": 3}))
    before = doc.read_text()

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="technical_paper")

    assert r2["tier"] == "content", "a new schema means a new extraction"
    assert r2["version"] == 3, "the body is unchanged: the OLD domain's staging folder is the baseline"
    assert doc.read_text() == before.replace("_domain: general", "_domain: technical_paper")


def test_reingest_truly_unchanged_does_not_rewrite_or_commit(vault):
    """Spec 2026-09-26 R6: every per-ingest field lives in the staging
    folder's document.json, so re-ingesting an unchanged note writes
    nothing to it -- no touched_path, identical bytes, nothing for Obsidian
    Git to commit (and nothing to conflict on across machines)."""
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    before = doc.read_text()

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "metadata_only"
    assert r2["version"] == r1["version"]
    assert r2["artmind_id"] == r1["artmind_id"]
    assert "touched_path" not in r2, "truly unchanged must not trigger a git commit"
    assert doc.read_text() == before, "the file must not be rewritten at all"


def test_hand_editing_an_authored_field_still_pushes_to_the_graph(vault, monkeypatch):
    """The one place skipping the note rewrite could have silently regressed:
    a human hand-edits `tags` (or title/project/area) without touching the
    body. The note is never rewritten for that (a metadata-only re-ingest
    stamps nothing), so nothing here can carry the edit -- but
    apply_metadata_only must still run unconditionally for every
    metadata_only tier, using the note's CURRENT (edited) frontmatter as
    parsed fresh off disk, or the edit would never reach the graph until the
    next real content change."""
    v, doc = vault
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000))

    from artmind.document_identity import render_document

    meta, body = ing._parse_md_frontmatter(doc.read_text())
    meta["tags"] = ["urgent"]
    doc.write_text(render_document(meta, body))

    calls = []
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: calls.append(k) or {})

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["tier"] == "metadata_only"
    assert "touched_path" not in r2, "no new bytes for artmind itself to commit"
    assert len(calls) == 1
    assert calls[0]["metadata"]["tags"] == ["urgent"], (
        "the graph must still see the human's edit even though the file wasn't rewritten"
    )


def test_reingest_edited_body_bumps_version(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    stamped = doc.read_text()

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "content"
    assert r2["version"] == r1["version"] + 1
    assert r2["artmind_id"] == r1["artmind_id"]
    assert "touched_path" not in r2, "a content change does not rewrite the note"
    assert doc.read_text() == stamped + "\nA new paragraph.\n"


def test_a_failed_extraction_is_retried_at_the_same_version(vault):
    """The version lives in document.json, written by the extraction: an
    ingest whose extraction never staged anything leaves the baseline where
    it was, so the next ingest is a content change again at the same number
    -- never a metadata_only that skips the extraction the note still needs
    (the fat-frontmatter design recorded the new hash before extracting)."""
    v, doc = vault
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000))
    doc.write_text(doc.read_text() + "\nEdited.\n")

    failed = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails
    retried = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (failed["tier"], failed["version"]) == ("content", 2)
    assert (retried["tier"], retried["version"]) == ("content", 2)


def test_a_legacy_fat_frontmatter_note_still_versions_from_its_own_fields(vault):
    """Backward compatibility: a note stamped by pre-R6 artmind, whose staging
    folder predates `content_sha256` in document.json, versions from its own
    `_content_sha256`/`_version` -- and is not rewritten."""
    from artmind.document_identity import compute_content_sha256

    v, doc = vault
    body = "# Doc\n\nOriginal body.\n"
    doc.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 4\n_content_sha256: "
        + compute_content_sha256(body)
        + "\n_domain: general\n_status: latest\n_ingested_at: '2026-01-01T00:00:00+00:00'\n---\n\n"
        + body
    )
    folder = ing.KG_DIR / "general" / "doc"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "0192legacy", "version": 4}))
    before = doc.read_bytes()

    unchanged = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (unchanged["tier"], unchanged["version"], unchanged["artmind_id"]) == ("metadata_only", 4, "0192legacy")
    assert doc.read_bytes() == before

    doc.write_text(doc.read_text() + "More.\n")
    edited = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (edited["tier"], edited["version"]) == ("content", 5)


def test_a_note_starting_with_a_bom_fails_its_ingest_and_is_left_untouched(vault):
    """`frontmatter.set_fields` refuses a BOM note (artmind's own parser does
    not see frontmatter behind a BOM, so stamping would put a second block in
    front of the user's properties). The ingest turns that into this file's
    clear error (plan D decision 11) and writes nothing to the note."""
    v, doc = vault
    original = b"\xef\xbb\xbf---\ntags: [a]\n---\n\n# Doc\n\nBody.\n"
    doc.write_bytes(original)

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "failed"
    assert str(doc) in result["error"]
    assert "BOM" in result["error"]
    assert "cannot stamp identity" in result["error"]
    assert doc.read_bytes() == original, "a note that cannot be stamped is never rewritten"
    assert not (ing.KG_DIR / "general").exists(), "nothing was staged for it"
    assert "touched_path" not in result


def test_an_unparsable_frontmatter_fails_its_ingest_and_is_left_untouched(vault):
    v, doc = vault
    original = b"---\ntags: [unclosed\n---\n\n# Doc\n\nBody.\n"
    doc.write_bytes(original)

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "failed"
    assert str(doc) in result["error"]
    assert "cannot stamp identity" in result["error"]
    assert doc.read_bytes() == original


def test_git_mv_is_recognised_as_a_silent_move(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)  # kg/general/doc/ -- under the OLD name

    new_path = v / "notes" / "renamed.md"
    subprocess.run(["git", "mv", "notes/doc.md", "notes/renamed.md"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "rename"], cwd=v, check=True)

    r2 = ing.ingest_file(new_path, "gemma4:e4b", "general", chunk_size=6000)
    assert r2["resolution_verdict"] == "move"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert r2["tier"] == "metadata_only", "the old name's staging folder is still the baseline"
    assert "touched_path" not in r2, "a move never rewrites the note"


def _git_mv(v, src, dst):
    subprocess.run(["git", "mv", src, dst], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"mv {src}"], cwd=v, check=True)
    return v / dst


def _ingest_to_version_2(doc):
    """v1 ingested and staged, then a body edit ingested and staged: the
    staging folder kg/general/<stem>/ records version 2."""
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    doc.write_text(doc.read_text() + "\nMore.\n")
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert (r2["tier"], r2["version"]) == ("content", 2)
    stage_as_extracted(ing.KG_DIR, r2)
    return r2


def test_the_second_ingest_after_a_git_mv_keeps_the_version(vault):
    """The `move` ingest is metadata_only, so the staging folder stays under
    the OLD name. The NEXT ingest (verdict `reingest`, no prior_path any more)
    must still find it -- not reset the note to version 1 and re-extract."""
    v, doc = vault
    r2 = _ingest_to_version_2(doc)
    new = _git_mv(v, "notes/doc.md", "notes/renamed.md")

    moved = ing.ingest_file(new, "gemma4:e4b", "general", chunk_size=6000)
    again = ing.ingest_file(new, "gemma4:e4b", "general", chunk_size=6000)

    assert (moved["resolution_verdict"], moved["tier"], moved["version"]) == ("move", "metadata_only", 2)
    assert again["resolution_verdict"] == "reingest"
    assert (again["tier"], again["version"]) == ("metadata_only", 2)
    assert again["artmind_id"] == r2["artmind_id"]


def test_a_hand_edited_domain_keeps_the_version(vault):
    """Editing `_domain` in the note leaves the staging folder in the OLD
    domain; the note no longer names it, the registry row still does."""
    v, doc = vault
    r2 = _ingest_to_version_2(doc)
    doc.write_text(doc.read_text().replace("_domain: general", "_domain: banking"))

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["domain"] == "banking"
    assert result["artmind_id"] == r2["artmind_id"]
    assert (result["tier"], result["version"]) == ("metadata_only", 2)


def test_a_truly_new_note_looks_in_no_staging_folder(vault, monkeypatch):
    """`new` means no baseline can exist: no lookup, and above all no scan of
    the domain's staging folders. A note whose folder sits at its own name is
    found by the direct lookup, also without a scan."""
    v, doc = vault
    scans = []
    real_glob = type(ing.KG_DIR).glob

    def spy(self, pattern, *a, **k):
        scans.append(str(self))
        return real_glob(self, pattern, *a, **k)

    monkeypatch.setattr(type(ing.KG_DIR), "glob", spy)
    (ing.KG_DIR / "general" / "elsewhere").mkdir(parents=True)  # something a scan would open
    (ing.KG_DIR / "general" / "elsewhere" / "document.json").write_text(json.dumps({"id": "x", "version": 9}))

    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert (r1["resolution_verdict"], r1["version"]) == ("new", 1)
    assert not [s for s in scans if s.endswith("general")], scans

    stage_as_extracted(ing.KG_DIR, r1)
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert (r2["resolution_verdict"], r2["tier"]) == ("reingest", "metadata_only")
    assert not [s for s in scans if s.endswith("general")], "found at its own name: no scan"


def _stub_llm(monkeypatch):
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "build_properties_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda *a, **k: ([], True))


def _ingest_and_extract(doc, domain="general"):
    result = ing.ingest_file(doc, "gemma4:e4b", domain, chunk_size=6000)
    assert result["status"] == "ok"
    return result, ing.extract_kg(result, result["domain"], max_workers=1)


def test_a_git_mv_repoints_the_staged_document_at_the_new_path(vault):
    """A move is metadata_only -- nothing is re-extracted -- so without this
    the next replay would keep the :Document on the old path and name."""
    v, doc = vault
    _ingest_to_version_2(doc)
    folder = ing.KG_DIR / "general" / "doc"
    before = json.loads((folder / "document.json").read_text())
    new = _git_mv(v, "notes/doc.md", "notes/renamed.md")

    moved = ing.ingest_file(new, "gemma4:e4b", "general", chunk_size=6000)

    assert moved["tier"] == "metadata_only"
    expected = {**before, "source_path": "notes/renamed.md", "path": "notes/renamed.md", "name": "renamed.md"}
    raw = (folder / "document.json").read_text(encoding="utf-8")
    assert raw == json.dumps(expected, indent=2, ensure_ascii=False), "only those three keys change"
    assert sorted(p.name for p in folder.parent.iterdir()) == ["doc"], "no scratch left beside the folder"
    assert json.loads(raw)["version"] == 2 and json.loads(raw)["content_sha256"] == before["content_sha256"]


def test_a_git_mv_updates_the_graph_document_path_too(vault, graph_runs, monkeypatch):
    """The unchanged-content branch used to skip the graph: the machine that
    ran the ingest kept the OLD path (a machine that replays the repointed
    staging folder was right). The `:Document` is matched by id and gets the
    same three properties the staging folder's document.json got."""
    v, doc = vault
    _ingest_to_version_2(doc)
    doc_id = json.loads((ing.KG_DIR / "general" / "doc" / "document.json").read_text())["id"]
    monkeypatch.setattr("artmind.delta.apply_move", _REAL_APPLY_MOVE, raising=False)
    new = _git_mv(v, "notes/doc.md", "notes/renamed.md")

    moved = ing.ingest_file(new, "gemma4:e4b", "general", chunk_size=6000)

    assert moved["tier"] == "metadata_only"
    assert len(graph_runs) == 1
    query, params = graph_runs[0]
    assert "MATCH (d:Document {id: $doc_id})" in query and "SET d += $props" in query
    assert params == {
        "doc_id": doc_id,
        "props": {"path": "notes/renamed.md", "source_path": "notes/renamed.md", "name": "renamed.md"},
    }


def test_an_unchanged_note_that_did_not_move_sends_no_path_update(vault, graph_runs, monkeypatch):
    v, doc = vault
    _ingest_to_version_2(doc)
    monkeypatch.setattr("artmind.delta.apply_move", _REAL_APPLY_MOVE, raising=False)

    again = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert again["tier"] == "metadata_only"
    assert graph_runs == []


def test_document_json_holds_no_absolute_path(vault, monkeypatch):
    """The staging folder is committed; an absolute path differs per machine
    and leaks the local layout. `path` and `source_path` are vault-relative."""
    v, doc = vault
    _stub_llm(monkeypatch)
    _, folder = _ingest_and_extract(doc)

    document = json.loads((folder / "document.json").read_text())

    assert (document["path"], document["source_path"]) == ("notes/doc.md", "notes/doc.md")
    assert [k for k, val in document.items() if isinstance(val, str) and val.startswith("/")] == []


def test_content_change_after_a_git_mv_extracts_under_the_new_name_and_drops_the_old_folder(vault, monkeypatch):
    """Two folders carrying one id would let a rebuild from staging replay
    the stale one over the new: the extraction that supersedes the old
    folder removes it."""
    v, doc = vault
    _stub_llm(monkeypatch)
    r1, folder1 = _ingest_and_extract(doc)
    assert folder1 == ing.KG_DIR / "general" / "doc"
    new = _git_mv(v, "notes/doc.md", "notes/renamed.md")
    ing.ingest_file(new, "gemma4:e4b", "general", chunk_size=6000)  # the silent move
    new.write_text(new.read_text() + "\nMore.\n")

    r2, folder2 = _ingest_and_extract(new)

    assert (r2["tier"], r2["version"]) == ("content", 2), "the version carries on from the old folder"
    assert folder2 == ing.KG_DIR / "general" / "renamed"
    document = json.loads((folder2 / "document.json").read_text())
    assert (document["id"], document["version"], document["source_path"]) == (r1["artmind_id"], 2, "notes/renamed.md")
    assert sorted(p.name for p in (ing.KG_DIR / "general").iterdir()) == ["renamed"], "the old folder is gone"


def test_a_domain_change_extracts_into_the_new_domain_and_drops_the_old_folder(vault, monkeypatch):
    v, doc = vault
    _stub_llm(monkeypatch)
    r1, _ = _ingest_and_extract(doc)

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="technical_paper")
    folder = ing.extract_kg(r2, "technical_paper", max_workers=1)

    assert folder == ing.KG_DIR / "technical_paper" / "doc"
    assert json.loads((folder / "document.json").read_text())["id"] == r1["artmind_id"]
    assert not (ing.KG_DIR / "general" / "doc").exists(), "one id, one staging folder"


def test_reextracting_keeps_the_created_on_first_recorded(vault, monkeypatch):
    """`created_on` falls back to the file's birthtime, which differs per
    machine and checkout: the value first recorded in document.json wins, so
    a re-extract does not churn the file (nor conflict across machines)."""
    v, doc = vault
    _stub_llm(monkeypatch)
    _r1, folder = _ingest_and_extract(doc)
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "created_on": "2001-01-01T00:00:00+00:00"}))
    doc.write_text(doc.read_text() + "\nMore.\n")

    _r2, folder = _ingest_and_extract(doc)

    assert json.loads((folder / "document.json").read_text())["created_on"] == "2001-01-01T00:00:00+00:00"


def test_an_authored_created_on_still_wins_over_the_recorded_one(vault, monkeypatch):
    v, doc = vault
    _stub_llm(monkeypatch)
    _r1, folder = _ingest_and_extract(doc)
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "created_on": "2001-01-01T00:00:00+00:00"}))
    text = doc.read_text().replace("_domain: general\n", "_domain: general\ncreated_on: 2020-05-05\n", 1)
    doc.write_text(text + "\nMore.\n")

    _r2, folder = _ingest_and_extract(doc)

    assert json.loads((folder / "document.json").read_text())["created_on"] == "2020-05-05"


def test_a_superseded_folder_is_only_removed_when_it_is_this_documents_under_kg(vault, monkeypatch, tmp_path):
    v, doc = vault
    new_folder = ing.KG_DIR / "general" / "new"
    new_folder.mkdir(parents=True)
    foreign = ing.KG_DIR / "general" / "someone_elses"
    foreign.mkdir()
    (foreign / "document.json").write_text(json.dumps({"id": "not-mine", "version": 1}))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "document.json").write_text(json.dumps({"id": "mine", "version": 1}))
    mine = ing.KG_DIR / "general" / "old"
    mine.mkdir()
    (mine / "document.json").write_text(json.dumps({"id": "mine", "version": 1}))

    ing._remove_superseded_staging(foreign, new_folder, "mine")
    ing._remove_superseded_staging(outside, new_folder, "mine")
    ing._remove_superseded_staging(new_folder, new_folder, "mine")
    ing._remove_superseded_staging(None, new_folder, "mine")

    assert foreign.exists() and outside.exists() and new_folder.exists() and mine.exists()

    ing._remove_superseded_staging(mine, new_folder, "mine")

    assert not mine.exists()
    assert foreign.exists() and new_folder.exists()


def test_copy_with_same_frontmatter_id_refuses(vault):
    """Two live files sharing one id -- the old path never went away."""
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    copy_path = v / "notes" / "copy.md"
    copy_path.write_text(doc.read_text())  # literal copy, same _artmind_id
    subprocess.run(["git", "add", "-A"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "copy"], cwd=v, check=True)

    result = ing.ingest_file(copy_path, "gemma4:e4b", "general", chunk_size=6000)
    assert result["status"] == "failed"
    assert "already registered" in result["error"]


def test_copy_with_fork_mints_independent_identity(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    copy_path = v / "notes" / "copy.md"
    copy_path.write_text(doc.read_text())
    subprocess.run(["git", "add", "-A"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "copy"], cwd=v, check=True)

    r2 = ing.ingest_file(copy_path, "gemma4:e4b", "general", chunk_size=6000, fork=True)
    assert r2["status"] == "ok"
    assert r2["resolution_verdict"] == "new"
    assert r2["artmind_id"] != r1["artmind_id"]


def test_frontmatter_domain_wins_over_domain_argument(vault):
    v, doc = vault
    doc.write_text("---\n_domain: technical_paper\n---\n\n# Doc\n\nBody.\n")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert result["domain"] == "technical_paper"


def test_set_domain_overrides_frontmatter_and_forces_content_tier(vault):
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # v1, domain=general
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # metadata_only, unchanged

    result = ing.ingest_file(
        doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="technical_paper",
    )
    assert result["domain"] == "technical_paper"
    assert result["tier"] == "content"  # forced re-extraction despite unchanged body


def test_missing_domain_fails_clearly(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", None, chunk_size=6000)
    assert result["status"] == "failed"
    assert "_domain" in result["error"]


def test_touched_path_is_set_for_git_batching(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert result["touched_path"] == doc


# ── the metadata-only path must not look in MARKDOWNS_DIR ───────────────────


def test_ingest_to_kg_resolves_a_vault_native_markdown_at_its_vault_path(tmp_path, monkeypatch):
    """Phase 2 stopped copying vault-native markdown into the data dir — the
    vault file IS the markdown. `ingest_to_kg`'s back-compat branch still built
    `MARKDOWNS_DIR / f"{stem}.md"` by hand, so any vault-native re-ingest that
    reached it died on "Markdown not found".

    That is how a live run lost data: an unchanged file returns the
    `metadata_only` tier with no `chunks_dir`, fell into this branch, failed,
    wrote no observations — and the deferred full rebuild then correctly
    deleted every entity whose observations had been cleaned.
    """
    import artmind.ingest as ing

    vault_file = tmp_path / "vault" / "rates.md"
    vault_file.parent.mkdir(parents=True)
    vault_file.write_text("---\n_artmind_id: abc\n---\n\n# Rates\n\nBody text.\n", encoding="utf-8")

    # Point MARKDOWNS_DIR somewhere that definitively does NOT hold a copy.
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", tmp_path / "data" / "markdowns")
    (tmp_path / "data" / "markdowns").mkdir(parents=True)

    seen: dict = {}
    monkeypatch.setattr(ing, "extract_kg", lambda fr, *a, **k: seen.setdefault("fr", fr) and None or None)
    monkeypatch.setattr(ing, "_persist_chunks", lambda chunks, d, body=None: d.mkdir(parents=True, exist_ok=True))

    # No chunks_dir — exactly what the metadata_only fast path returns.
    file_result = {
        "artmind_id": "abc",
        "version": 1,
        "registered_path": str(vault_file),
        "source_type": "md",
    }
    ing.ingest_to_kg(file_result, "banking.reference", stage_only=True)

    assert "chunks_dir" in file_result, (
        "the vault file should have been chunked; instead the markdown lookup failed"
    )


def test_the_markdown_lookup_uses_the_shared_resolver():
    """A guard on the fix, not the symptom: hand-building the path here is what
    broke, and `markdown_path_for` exists to be the one place that knows."""
    import inspect

    import artmind.ingest as ing

    src = inspect.getsource(ing.ingest_to_kg)
    back_compat = src[src.index("Back-compat: if ingest_file didn't split chunks"):]
    assert "markdown_path_for(" in back_compat
    assert 'MARKDOWNS_DIR / f"{registered_path.stem}.md"' not in back_compat


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\\\b", "..", ".hidden", "''", "[general]", "'general/../x'"])
def test_a_note_domain_that_is_not_a_plain_folder_name_is_rejected(vault, bad):
    """`_domain` is hand-editable and becomes `kg/<domain>/`: anything but one
    plain folder name (a path, `..`, a dotfolder, empty, not a string) fails
    the note with a clear error, and nothing is registered or written."""
    v, doc = vault
    original = f"---\n_domain: {bad}\n---\n\n# Doc\n\nBody.\n"
    doc.write_text(original, encoding="utf-8")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "failed"
    assert "_domain" in result["error"] and "plain folder name" in result["error"]
    assert doc.read_text(encoding="utf-8") == original, "the note is untouched"


def test_a_bad_set_domain_is_rejected_the_same_way(vault):
    v, doc = vault

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="../x")

    assert result["status"] == "failed" and "plain folder name" in result["error"]


def test_a_dotted_domain_family_name_is_still_a_plain_folder_name(vault):
    """`banking.reference` is one folder name, not a path."""
    v, doc = vault
    doc.write_text("---\n_domain: banking.reference\n---\n\n# Doc\n\nBody.\n", encoding="utf-8")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok" and result["domain"] == "banking.reference"


@pytest.mark.parametrize("value,ok", [
    ("general", True), ("banking.reference", True), ("a b", True),
    ("", False), (".", False), ("..", False), (".hidden", False), ("a/b", False),
    ("a\\b", False), ("x..y", False), ("/abs", False), (None, False), (["general"], False), (3, False),
])
def test_is_plain_folder_name_is_the_one_shared_domain_check(value, ok):
    from artmind.document_identity import is_plain_folder_name
    from artmind.frontmatter_migration import _plain_name

    assert is_plain_folder_name(value) is ok
    assert _plain_name(value) is ok
