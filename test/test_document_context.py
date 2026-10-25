"""The pure part of the document context (spec 2026-10-25-document-context-design.md).

No LLM, no Neo4j, no files: every function here takes plain values and returns
plain values, so the tests assert on the returned values directly."""
import pytest

from artmind.document_context import context_settings


def test_no_setting_means_everything_on_with_the_default_cap():
    s = context_settings({})
    assert s.enabled is True
    assert s.summary is True
    assert s.focus == ""
    assert s.cap == 2000


def test_document_context_false_turns_the_whole_block_off():
    s = context_settings({"document_context": False})
    assert s.enabled is False
    assert s.summary is False


def test_summary_false_keeps_metadata_and_outline_without_a_call():
    s = context_settings({"document_context": {"summary": False}})
    assert s.enabled is True
    assert s.summary is False


def test_focus_text_is_read_and_stripped():
    s = context_settings({"document_context": {"focus": "  Parties and effective dates.  "}})
    assert s.focus == "Parties and effective dates."


def test_legacy_document_header_false_maps_to_document_context_false():
    s = context_settings({"document_header": False})
    assert s.enabled is False


def test_legacy_document_header_chars_sets_the_cap():
    s = context_settings({"document_header": {"chars": 500}})
    assert s.enabled is True
    assert s.cap == 500


def test_document_context_wins_over_the_legacy_key_when_both_are_given():
    s = context_settings({"document_header": False, "document_context": {"focus": "x"}})
    assert s.enabled is True
    assert s.focus == "x"


def test_a_bad_chars_value_falls_back_to_the_default_cap():
    assert context_settings({"document_header": {"chars": "lots"}}).cap == 2000


def test_a_negative_legacy_cap_clamps_to_zero():
    assert context_settings({"document_header": {"chars": -5}}).cap == 0


def test_a_bare_document_context_key_means_on_with_defaults():
    s = context_settings({"document_context": None})
    assert s.enabled is True and s.summary is True and s.focus == ""


def test_a_non_string_focus_is_ignored():
    assert context_settings({"document_context": {"focus": 5}}).focus == ""
    assert context_settings({"document_context": {"focus": ["a", "b"]}}).focus == ""


from artmind.document_context import build_outline


def test_outline_names_the_chunks_heading_and_its_position():
    meta = {
        "001": {"header_path": ""},
        "002": {"header_path": "Schedule 2 > Rates"},
        "003": {"header_path": "Schedule 2 > Rates"},
    }
    assert build_outline(meta, 2, 3) == "You are in: Schedule 2 > Rates (chunk 2 of 3)"


def test_a_chunk_under_no_heading_gets_only_its_position():
    assert build_outline({"001": {"header_path": ""}}, 1, 4) == "You are in: chunk 1 of 4"


def test_missing_chunks_meta_still_gives_the_position():
    assert build_outline({}, 5, 9) == "You are in: chunk 5 of 9"


def test_the_outline_lists_the_document_headings_in_order_when_given_them():
    meta = {
        "001": {"header_path": "Intro"},
        "002": {"header_path": "Schedule 2 > Rates"},
        "003": {"header_path": "Schedule 2 > Fees"},
    }
    text = build_outline(meta, 2, 3, document_outline=True)
    assert text.splitlines() == [
        "You are in: Schedule 2 > Rates (chunk 2 of 3)",
        "Document outline:",
        "  - Intro",
        "  - Schedule 2 > Rates",
        "  - Schedule 2 > Fees",
    ]


from artmind.document_context import build_metadata


def test_metadata_renders_the_fields_that_have_values_in_a_fixed_order():
    document = {
        "name": "CCN001.pdf",
        "path": "contracts/CCN001.pdf",
        "title": "Change Control Notice 001",
        "project": "Alpha",
        "area": "Legal",
        "tags": ["amendment", "rates"],
        "author": "J. Smith",
        "_valid_from": "2026-03-01",
        "_valid_time_source": "property",
        "provenance": {"source_type": "pdf"},
    }
    assert build_metadata(document).splitlines() == [
        "Title: Change Control Notice 001",
        "Source: contracts/CCN001.pdf (pdf)",
        "Date: 2026-03-01",
        "Author: J. Smith",
        "Project: Alpha",
        "Area: Legal",
        "Tags: amendment, rates",
    ]


def test_metadata_skips_what_is_missing_and_never_renders_empty_lines():
    text = build_metadata({"name": "a.md", "path": "notes/a.md"})
    assert text == "Source: notes/a.md"


def test_metadata_falls_back_to_the_file_name_when_there_is_no_path():
    assert build_metadata({"name": "a.md"}) == "Source: a.md"


def test_metadata_is_empty_for_an_empty_document():
    assert build_metadata({}) == ""


def test_a_multi_line_value_cannot_forge_another_field():
    assert build_metadata({"title": "a\nSummary: x"}) == "Title: a Summary: x"
    assert build_metadata({"author": "J.\r\n\tSmith"}) == "Author: J. Smith"


def test_blank_and_none_list_items_are_dropped_from_tags():
    assert build_metadata({"tags": ["a", None, "", "  ", "b"]}) == "Tags: a, b"


from artmind.document_context import SAMPLE_CHARS, sample_for_summary


def test_a_short_document_is_sampled_whole():
    chunks = ["Opening para.", "Middle para.", "Closing para."]
    sample = sample_for_summary(chunks, {})
    assert "Opening para." in sample and "Middle para." in sample and "Closing para." in sample


def test_the_sample_takes_the_first_paragraph_under_each_top_level_heading():
    chunks = ["# Intro\n\nIntro text.", "## Rates\n\nRates detail.\n\nMore rates."]
    meta = {"001": {"header_path": "Intro"}, "002": {"header_path": "Intro > Rates"}}
    sample = sample_for_summary(chunks, meta)
    assert "Intro text." in sample
    assert "Rates detail." in sample


def test_without_headings_ten_evenly_spaced_chunks_are_sampled():
    chunks = [f"chunk {i} text." for i in range(1, 41)]
    sample = sample_for_summary(chunks, {})
    assert "chunk 1 text." in sample and "chunk 40 text." in sample
    assert sample.count("text.") == 10


def test_the_sample_never_exceeds_the_cap():
    chunks = [("word " * 2000) for _ in range(20)]
    assert len(sample_for_summary(chunks, {})) <= SAMPLE_CHARS


def test_an_empty_document_gives_an_empty_sample():
    assert sample_for_summary([], {}) == ""
