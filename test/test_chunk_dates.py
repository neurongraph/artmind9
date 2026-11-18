"""Chunk dates (spec 2026-11-15 §5.1, plan question 15). Invented text."""
from artmind import ingest as ing

DOC = {"_valid_from": "2026-01-01", "_valid_to": "2026-02-01", "_valid_time_source": "header",
       "_valid_precision": "month"}


def test_a_chunk_under_a_dated_heading_has_its_own_date():
    chunk = {"id": "c", "header_path": "Notes > 2026-01-14", "text": "Planted the bulbs."}
    assert ing._chunk_valid_time(chunk, DOC) == {
        "_valid_from": "2026-01-14", "_valid_to": "2026-02-01", "_valid_precision": "day",
        "_doc_valid_from": "2026-01-01", "_doc_valid_to": "2026-02-01"}


def test_a_chunk_starting_with_a_date_line_has_its_own_date():
    chunk = {"id": "c", "header_path": "", "text": "\n2026-01-20\nRain all day."}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-20"


def test_a_markdown_heading_line_counts_as_the_first_line():
    chunk = {"id": "c", "text": "## 2026-01-21\nWind."}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-21"


def test_otherwise_the_documents_date_and_precision():
    chunk = {"id": "c", "header_path": "Notes", "text": "On 2026-01-20 it rained."}
    out = ing._chunk_valid_time(chunk, DOC)
    assert (out["_valid_from"], out["_valid_precision"]) == ("2026-01-01", "month")


def test_a_date_followed_by_prose_is_not_a_chunk_date():
    chunk = {"id": "c", "header_path": "", "text": "2026-01-20 evening by the lake\nMore."}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-01"


def test_a_month_heading_is_not_a_chunk_date():
    chunk = {"id": "c", "header_path": "January 2026", "text": "x"}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-01"


def test_a_range_heading_is_not_a_chunk_date():
    chunk = {"id": "c", "header_path": "2026-01-10 to 2026-01-12", "text": "x"}
    assert ing._chunk_valid_time(chunk, DOC)["_valid_from"] == "2026-01-01"


def test_a_default_dated_document_leaves_its_chunks_undated():
    doc = {"_valid_from": "2026-03-03", "_valid_precision": "none", "_valid_time_source": "default_ingestion"}
    out = ing._chunk_valid_time({"id": "c", "text": "Plain."}, doc)
    assert out == {"_valid_from": "2026-03-03", "_valid_precision": "none", "_doc_valid_from": "2026-03-03"}


def test_an_undated_document_gives_no_dates():
    assert ing._chunk_valid_time({"id": "c", "text": "Plain."}, {}) == {}


def test_date_chunks_drops_stale_dates():
    chunks = [{"id": "c", "text": "Plain.", "_valid_from": "2020-01-01", "_valid_precision": "day",
               "_doc_valid_to": "2020-02-01"}]
    out = ing._date_chunks(chunks, {"_valid_from": "2026-01-01", "_valid_precision": "day"})
    assert out == [{"id": "c", "text": "Plain.", "_valid_from": "2026-01-01", "_valid_precision": "day",
                    "_doc_valid_from": "2026-01-01"}]


def test_a_chunk_carries_the_documents_stated_end_as_its_own():
    out = ing._date_chunks([{"id": "c", "text": "Plain.", "_valid_to": "2019-01-01"}], DOC)
    assert (out[0]["_valid_to"], out[0]["_doc_valid_to"]) == ("2026-02-01", "2026-02-01")


def test_a_chunk_of_a_document_with_no_stated_end_has_none():
    out = ing._date_chunks([{"id": "c", "text": "Plain.", "_valid_to": "2019-01-01"}],
                           {"_valid_from": "2026-01-01", "_valid_precision": "day"})
    assert "_valid_to" not in out[0] and "_doc_valid_to" not in out[0]
