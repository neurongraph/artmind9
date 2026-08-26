"""A1a — block/offset ids on chunks.

_split_markdown now returns dicts carrying source offsets, a header breadcrumb,
and a content hash; _persist_chunks writes the chunks_meta.json sidecar; and the
four keys flow through to the DocChunk MERGE props in _write_to_neo4j.
"""
import json
from hashlib import sha1

import artmind.ingest as ing


BODY = """# Fees Overview

Banks charge a variety of fees to their customers.

## Monthly Maintenance

A monthly maintenance fee of six pounds applies to standard accounts.

## Overdraft

An unarranged overdraft incurs a daily charge until the balance is restored.
"""


def _anchor_lines(text):
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines[0], lines[-1]


def test_offsets_span_first_to_last_line_of_each_chunk():
    chunks = ing._split_markdown(BODY, chunk_size=6000)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        # The header splitter reformats whitespace, so the span is line-anchored,
        # not byte-identical: it starts at the first line and ends at the last.
        assert c["char_start"] != -1
        first, last = _anchor_lines(c["text"])
        span = BODY[c["char_start"] : c["char_end"]]
        assert span.startswith(first)
        assert span.endswith(last)


def test_offsets_track_source_when_char_splitter_forces_sub_chunks():
    # A tiny chunk_size forces RecursiveCharacterTextSplitter to fragment a
    # section; each fragment must still anchor to a real source span.
    chunks = ing._split_markdown(BODY, chunk_size=60)
    assert len(chunks) > 3, "small chunk_size should fragment sections"
    for c in chunks:
        assert c["char_start"] != -1
        first, last = _anchor_lines(c["text"])
        span = BODY[c["char_start"] : c["char_end"]]
        assert span.startswith(first)
        assert span.endswith(last)
    # Starts advance monotonically — the running cursor never rewinds.
    starts = [c["char_start"] for c in chunks]
    assert starts == sorted(starts)


def test_header_path_breadcrumb_tracks_nesting():
    chunks = ing._split_markdown(BODY, chunk_size=6000)
    by_text = {c["text"].split("\n", 1)[0]: c["header_path"] for c in chunks}
    # The "## Monthly Maintenance" chunk sits under the h1 title.
    monthly = next(c for c in chunks if "Monthly Maintenance" in c["text"])
    assert "Fees Overview" in monthly["header_path"]
    assert "Monthly Maintenance" in monthly["header_path"]
    assert monthly["header_path"].index("Fees Overview") < monthly["header_path"].index(
        "Monthly Maintenance"
    )


def test_block_hash_is_content_addressed_and_stable():
    a = ing._split_markdown(BODY, chunk_size=6000)
    b = ing._split_markdown(BODY, chunk_size=6000)
    assert [c["block_hash"] for c in a] == [c["block_hash"] for c in b]
    for c in a:
        assert c["block_hash"] == sha1(c["text"].encode("utf-8")).hexdigest()


def test_persist_chunks_writes_sidecar_with_four_keys(tmp_path):
    chunks = ing._split_markdown(BODY, chunk_size=60)
    ing._persist_chunks(chunks, tmp_path)

    md_files = sorted(tmp_path.glob("chunk_*.md"))
    assert len(md_files) == len(chunks)
    assert md_files[0].read_text(encoding="utf-8") == chunks[0]["text"]

    meta = json.loads((tmp_path / "chunks_meta.json").read_text(encoding="utf-8"))
    assert set(meta.keys()) == {f"{i:03d}" for i in range(1, len(chunks) + 1)}
    first = meta["001"]
    assert set(first) == {"char_start", "char_end", "header_path", "block_hash"}
    assert first["block_hash"] == chunks[0]["block_hash"]


def test_persist_chunks_clears_stale_chunk_files(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "chunk_099.md").write_text("stale", encoding="utf-8")
    chunks = ing._split_markdown(BODY, chunk_size=6000)
    ing._persist_chunks(chunks, tmp_path)
    assert not (tmp_path / "chunk_099.md").exists()


# ── the four keys reach the DocChunk MERGE props ────────────────────────────


class _Rec:
    def __init__(self, single=None, data=None):
        self._single = single
        self._data = data or []

    def single(self):
        return self._single

    def data(self):
        return self._data


class _RecordingSession:
    """Records every run() and its parameters — these tests assert on what was
    SENT, never on a return value an empty fake could not produce."""

    def __init__(self, runs):
        self.runs = runs

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec(single=None, data=[])

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, **kwargs):
        class _Ctx:
            def __init__(self, s):
                self._s = s

            def __enter__(self_inner):
                return self_inner._s

            def __exit__(self_inner, *exc):
                return False

        return _Ctx(self._session)

    def close(self):
        pass


def test_docchunk_merge_props_carry_the_four_keys(tmp_path, monkeypatch):
    doc_kg = tmp_path
    (doc_kg / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md", "domain": "banking"}), encoding="utf-8"
    )
    (doc_kg / "chunks.json").write_text(
        json.dumps(
            [
                {
                    "id": "d1_001",
                    "doc_id": "d1",
                    "name": "Chunk 1/1",
                    "text": "some text",
                    "domain": "banking",
                    "embedding": [],
                    "char_start": 0,
                    "char_end": 9,
                    "header_path": "Fees Overview",
                    "block_hash": "abc123",
                }
            ]
        ),
        encoding="utf-8",
    )
    for name in ("entities.json", "properties.json", "relationships.json"):
        (doc_kg / name).write_text("[]", encoding="utf-8")

    runs: list = []
    session = _RecordingSession(runs)
    monkeypatch.setattr("artmind.graph_query.neo4j_session", lambda *a, **k: session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)

    assert ing._write_to_neo4j(doc_kg) is not None

    chunk_runs = [(c, k) for c, k in runs if "CREATE (n:DocChunk" in c]
    assert chunk_runs, "expected a DocChunk MERGE"
    _, kwargs = chunk_runs[0]
    props = kwargs["props"]
    assert props["char_start"] == 0
    assert props["char_end"] == 9
    assert props["header_path"] == "Fees Overview"
    assert props["block_hash"] == "abc123"
