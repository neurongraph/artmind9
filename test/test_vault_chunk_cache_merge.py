"""The chunk cache (`documents/markdowns/<stem>_chunks/`) is derived from the
note's body, is committed with the vault, and so can arrive from a merge
belonging to a different version of the note than the one now on disk. Two
defences, tested here against real git merges:

- `extract_kg` verifies the cache against the note's current body and
  re-chunks when it does not correspond (the cache is derived data, so the
  note wins) -- the safety net that holds however the cache got there;
- `vault resolve` settles a `<stem>_chunks` folder as one unit that follows
  the merged note's body, like the kg folder beside it, instead of as
  independent files decided by content hash.
"""
import json
import subprocess

import pytest

import artmind.db as db
import artmind.document_identity as di
import artmind.ingest as ing
import artmind.vault_git as vg
from artmind import vault, vault_resolve as vr
from artmind.document_identity import compute_content_sha256

NOTE = "notes/policy.md"
CHUNKS = ".artmind/data/documents/markdowns/policy_chunks"


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check).stdout


def _configure(repo):
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "pull.rebase", "false")


def _machine(monkeypatch, clone):
    """Make this process behave as the machine holding `clone`: its vault,
    its staging folders and chunk caches, its own registry."""
    data = clone / ".artmind" / "data"
    for module in (ing, di, vg):
        monkeypatch.setattr(module, "ARTMIND_VAULT_DIR", clone)
    monkeypatch.setattr(ing, "KG_DIR", data / "kg")
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", data / "documents" / "markdowns")
    monkeypatch.setattr(db, "DB_PATH", data / "document_registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)


def _seams(monkeypatch, seen):
    """No LLM, no embedder: record the chunk text each extraction sees."""
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])

    def entities_prompt(text, schema, vocabulary=None):
        seen.append(text)
        return "p"

    monkeypatch.setattr(ing, "build_entities_prompt", entities_prompt)
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda step_name, model, prompt, debug_dir: ([], True))


def _sync(note):
    """`ingest sync` of one note: split, register, extract."""
    result = ing.ingest_file(note, "gemma4:e4b", "general", chunk_size=6000)
    assert result["status"] == "ok"
    assert ing.extract_kg(result, "general", max_workers=1) is not None
    return result


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture()
def clones(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    a = tmp_path / "A"
    subprocess.run(["git", "clone", "-q", str(bare), str(a)], check=True, capture_output=True)
    _configure(a)
    (a / ".artmind").mkdir()
    (a / ".artmind" / "vault.yaml").write_text("version: 1\n", encoding="utf-8")
    vault.write_gitattributes(a)
    vault.write_gitignore(a)
    (a / "notes").mkdir()
    (a / NOTE).write_text("# Policy\n\nBody one.\n", encoding="utf-8")
    _machine(monkeypatch, a)
    _seams(monkeypatch, [])
    _sync(a / NOTE)
    _commit(a, "a1")
    _git(a, "push", "-q", "-u", "origin", "main")
    b = tmp_path / "B"
    subprocess.run(["git", "clone", "-q", str(bare), str(b)], check=True, capture_output=True)
    _configure(b)
    return a, b


def _edit_and_sync(monkeypatch, clone, word):
    _machine(monkeypatch, clone)
    note = clone / NOTE
    note.write_text(note.read_text(encoding="utf-8").replace("one.", f"{word} edit."), encoding="utf-8")
    _sync(note)
    _commit(clone, word)


def test_extract_kg_after_a_resolved_merge_reads_the_merged_note_not_a_stale_chunk_cache(
    clones, monkeypatch
):
    """Both machines edited the same note and re-extracted; a human resolved
    the note by hand to a body neither wrote. Whatever side `vault resolve`
    left the chunk cache from, `extract-kg` must extract the merged note's
    text -- the text `document.json` records the hash of."""
    a, b = clones
    _edit_and_sync(monkeypatch, a, "A")
    _git(a, "push", "-q")
    _edit_and_sync(monkeypatch, b, "B")
    merge = subprocess.run(["git", "pull", "--no-edit"], cwd=b, capture_output=True, text=True)
    assert merge.returncode != 0, "the two edits conflict"

    merged_body = "# Policy\n\nMerged by a human.\n"
    note = b / NOTE
    frontmatter = note.read_text(encoding="utf-8").split("---\n")[1]
    note.write_text(f"---\n{frontmatter}---\n\n{merged_body}", encoding="utf-8")
    _git(b, "add", NOTE)
    report = vr.resolve(b)
    assert any(unit["kind"] in ("kg", "chunks") for unit in report["resolved"])
    _commit(b, "merge")

    _machine(monkeypatch, b)
    seen: list[str] = []
    _seams(monkeypatch, seen)
    file_result = ing._build_file_result_from_db("policy.md", "general")
    assert file_result is not None
    doc_kg_dir = ing.extract_kg(file_result, "general", max_workers=1)

    assert doc_kg_dir is not None
    assert seen, "the extractor was reached"
    assert all("A edit" not in text and "B edit" not in text for text in seen), seen
    assert seen == [chunk["text"] for chunk in ing._split_markdown(merged_body, 6000)]
    document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
    assert document["content_sha256"] == compute_content_sha256(merged_body.lstrip("\n"))


# ── extract_kg checks the cache against the note on its own ─────────────────


@pytest.fixture()
def synced(tmp_path, monkeypatch):
    """One note, synced (chunk cache written), extraction not yet run."""
    vault_dir = tmp_path / "vault"
    (vault_dir / "notes").mkdir(parents=True)
    note = vault_dir / NOTE
    note.write_text("# Policy\n\nBody one.\n", encoding="utf-8")
    _machine(monkeypatch, vault_dir)
    seen: list[str] = []
    _seams(monkeypatch, seen)
    result = ing.ingest_file(note, "gemma4:e4b", "general", chunk_size=6000)
    return note, result, seen


def _body(note):
    return ing._parse_md_frontmatter(note.read_text(encoding="utf-8"))[1]


def _expected(note, size=6000):
    return [chunk["text"] for chunk in ing._split_markdown(_body(note), size)]


def test_a_stamped_cache_from_another_body_is_re_split_from_the_note(synced):
    note, result, seen = synced
    chunks_dir = ing.Path(result["chunks_dir"])
    note.write_text(note.read_text(encoding="utf-8").replace("one.", "two, added after the split."), encoding="utf-8")
    assert "one." in (chunks_dir / "chunk_001.md").read_text(encoding="utf-8")

    assert ing.extract_kg(result, "general", max_workers=1) is not None

    assert seen == _expected(note)
    meta = json.loads((chunks_dir / "chunks_meta.json").read_text(encoding="utf-8"))
    assert meta[ing.CHUNK_CACHE_BODY_KEY] == compute_content_sha256(_body(note))
    assert result["chunk_count"] == len(_expected(note))


def test_an_unstamped_cache_that_no_longer_splits_from_the_note_is_re_split(synced):
    """A cache written before the stamp existed is judged by splitting again."""
    note, result, seen = synced
    chunks_dir = ing.Path(result["chunks_dir"])
    meta_path = chunks_dir / "chunks_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta[ing.CHUNK_CACHE_BODY_KEY]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (chunks_dir / "chunk_001.md").write_text("# Policy\n\nBody from another version.", encoding="utf-8")

    assert ing.extract_kg(result, "general", max_workers=1) is not None

    assert seen == _expected(note)


def test_an_unstamped_cache_that_matches_the_note_is_kept_as_it_is(synced):
    note, result, seen = synced
    chunks_dir = ing.Path(result["chunks_dir"])
    meta_path = chunks_dir / "chunks_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta[ing.CHUNK_CACHE_BODY_KEY]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    before = meta_path.stat().st_mtime_ns

    assert ing.extract_kg(result, "general", max_workers=1) is not None

    assert seen == _expected(note)
    assert meta_path.stat().st_mtime_ns == before and ing.CHUNK_CACHE_BODY_KEY not in json.loads(
        meta_path.read_text(encoding="utf-8")
    ), "a matching cache is not rewritten"


def test_a_current_cache_is_not_re_split_when_the_chunk_size_setting_changed(synced):
    """The stamp names the body, not the size: a resumed extraction keeps the
    chunks its cached per-chunk results were made for."""
    note, result, seen = synced
    chunks_dir = ing.Path(result["chunks_dir"])
    before = (chunks_dir / "chunks_meta.json").stat().st_mtime_ns

    assert ing.extract_kg(result, "general", max_workers=1, chunk_size=50) is not None

    assert (chunks_dir / "chunks_meta.json").stat().st_mtime_ns == before
    assert seen == _expected(note)
