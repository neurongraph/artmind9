"""ingest.write_staging: the staging folder's JSON is swapped in as one unit
(spec 2026-09-26 §5 R2)."""
import datetime
import json

from artmind.ingest import write_staging


def test_write_staging_swaps_json_files_and_keeps_the_chunk_cache(tmp_path):
    doc = tmp_path / "kg" / "general" / "doc"
    (doc / "chunks" / "sha").mkdir(parents=True)
    (doc / "chunks" / "sha" / "0.json").write_text("cached")
    (doc / "observations.json").write_text('["old"]')

    write_staging(doc, {"document.json": {"id": "x", "name": "Café"}, "observations.json": [{"n": 1}]})

    assert json.loads((doc / "document.json").read_text(encoding="utf-8")) == {"id": "x", "name": "Café"}
    assert json.loads((doc / "observations.json").read_text()) == [{"n": 1}]
    assert (doc / "chunks" / "sha" / "0.json").read_text() == "cached"
    assert sorted(p.name for p in doc.parent.iterdir()) == ["doc"]


def test_write_staging_bytes_match_the_previous_format(tmp_path):
    """`vault sync` carries the embedding sidecar only when the live
    chunks.json is byte-identical to the committed one, so the on-disk
    format must not drift: indent=2, ensure_ascii=False, no trailing newline."""
    doc = tmp_path / "doc"
    chunks = [{"id": "c1", "text": "naïve"}]

    write_staging(doc, {"chunks.json": chunks})

    assert (doc / "chunks.json").read_text(encoding="utf-8") == json.dumps(chunks, indent=2, ensure_ascii=False)


def test_write_staging_default_serialises_dates(tmp_path):
    doc = tmp_path / "doc"

    write_staging(doc, {"report.json": {"as_of": datetime.date(2026, 9, 26)}}, default=str)

    assert json.loads((doc / "report.json").read_text()) == {"as_of": "2026-09-26"}
