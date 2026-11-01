from artmind.topics import check, render

TEXTS = {"c1_p01": "The notice period is 30 days under clause 12.2.", "c2_p01": "Fees rose to 1,250 in 2025."}


def test_unknown_citations_are_removed_and_counted():
    body, checks = check.check("A claim [#c1_p01][#nope].", TEXTS)
    assert body == "A claim [#c1_p01]." and checks["removed_citations"] == 1


def test_uncited_paragraphs_are_counted_but_headings_are_not():
    body = "## Overview\n\nFirst [#c1_p01].\n\nNo source here.\n\n## Where sources differ\n\nAlso none."
    _, checks = check.check(body, TEXTS)
    assert checks["uncited_paragraphs"] == 2


def test_numbers_must_appear_in_a_cited_passage():
    _, ok = check.check("Notice is 30 days (clause 12.2) [#c1_p01]. Fees were 1250 [#c2_p01].", TEXTS)
    assert ok["unsupported_numbers"] == 0
    _, bad = check.check("Notice is 45 days [#c1_p01].", TEXTS)
    assert bad["unsupported_numbers"] == 1


def test_a_sentence_without_its_own_marker_uses_its_paragraphs_citations():
    _, checks = check.check("Fees rose in 2025. They reached 1,250 [#c2_p01].", TEXTS)
    assert checks["unsupported_numbers"] == 0


def test_render_numbers_footnotes_in_order_of_first_use():
    links = {"c1_p01": {"path": "contracts/MSA.md", "title": "MSA | Alpha", "header_path": "Terms > Termination"},
             "c2_p01": {"path": "scans/fees.pdf", "title": "Fees"}}
    text, order = render.render_body("A [#c2_p01]. B [#c1_p01][#c2_p01].", links)
    assert order == ["c2_p01", "c1_p01"]
    assert text.splitlines()[0] == "A [^1]. B [^2][^1]."
    assert "[^1]: [[scans/fees.pdf|Fees]] — passage `c2_p01`" in text
    assert "[^2]: [[contracts/MSA|MSA - Alpha]] — passage `c1_p01`" in text


def test_render_without_citations_has_no_footnotes():
    text, order = render.render_body("Plain.", {})
    assert text == "Plain." and order == []


def test_a_converted_binary_links_to_its_source_file_not_the_converted_markdown():
    links = {"c1_p01": {"path": ".artmind/data/documents/markdowns/Fees.md", "source_path": "contracts/Fees.pdf",
                        "title": "Fees", "header_path": "Charges"}}
    text, _ = render.render_body("A [#c1_p01].", links)
    assert "[[contracts/Fees.pdf|Fees]]" in text
    assert ".artmind" not in text   # no resolved chunk file, so the document is linked alone


def test_a_resolved_chunk_is_an_indented_second_line_under_the_document_link():
    links = {"c1_007_p01": {"source_path": "contracts/Fees.pdf", "title": "Fees", "chunk_id": "c1_007",
                            "header_path": "Charges",
                            "chunk_file": ".artmind/data/documents/markdowns/Fees~abc_chunks/chunk_007.md"}}
    text, _ = render.render_body("A [#c1_007_p01].", links)
    assert ("[^1]: [[contracts/Fees.pdf|Fees]] — passage `c1_007_p01`\n"
            "    [Chunk 7 — Charges](<.artmind/data/documents/markdowns/Fees~abc_chunks/chunk_007.md>)") in text


def test_a_derived_year_near_a_cited_year_is_supported_but_figures_stay_strict():
    texts = {"a": "Indexation applies from 1 April 2023. The rate is 222.22 per day."}
    body = "The first adjustment is from 1 April 2024 [#a]. The rate is 1,111.11 [#a]. It starts in 2040 [#a]."
    _, counts = check.check(body, texts)
    assert counts["unsupported_numbers"] == 2   # 1,111.11 and 2040; 2024 is within 5 years of 2023


# --- Date tokenisation tests ---

def test_numeric_date_in_sentence_matches_word_date_in_passage():
    """14.9.26 in prose should match '14 September 2026' in a cited passage."""
    texts = {"a": "The payment was made on 14 September 2026."}
    _, counts = check.check("The date was 14.9.26 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_numeric_date_in_sentence_matches_same_numeric_date_in_passage():
    """14.9.26 in prose should match 14.9.26 in a cited passage."""
    texts = {"a": "The payment was made on 14.9.26."}
    _, counts = check.check("The date was 14.9.26 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_date_range_dmy_format_is_not_counted_as_bare_numbers():
    """14.9.26 - 13.11.26: both dates matched via passage, no fragments like 14.9 or 26."""
    texts = {"a": "Contract runs from 14.9.26 to 13.11.26."}
    _, counts = check.check("Active 14.9.26 - 13.11.26 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_word_date_in_sentence_matches_numeric_date_in_passage():
    """'20 January 2025' in prose should match '20.1.25' in a cited passage."""
    texts = {"a": "Signed on 20.1.25."}
    _, counts = check.check("Signed on 20 January 2025 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_unsupported_date_is_still_flagged():
    """A date with no match anywhere in the cited passages is still unsupported."""
    texts = {"a": "Effective 1 March 2025."}
    _, counts = check.check("The date was 14.9.26 [#a].", texts)
    # 14.9.26 doesn't match 1.3.25; the year 26 -> 2026 doesn't match 2025 within slack
    assert counts["unsupported_numbers"] > 0


def test_month_year_partial_date_is_supported():
    """'September 2026' (no day) should match when the passage has any Sept 2026 date."""
    texts = {"a": "Meeting held in September 2026."}
    _, counts = check.check("In September 2026 [#a], the parties met.", texts)
    assert counts["unsupported_numbers"] == 0


# --- Identifier tests ---

def test_space_separated_code_number_is_not_a_figure():
    """'AB 7' and 'XY 12345': the digit follows a space so _NUMBER extracts it, but it is
    part of an identifier code and must not be counted as an unsupported figure."""
    texts = {"a": "The order was confirmed and dispatched."}  # '34' and '20726' not in passage
    _, counts = check.check("Order AB 7, XY 12345 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_hyphen_compound_identifier_numbers_not_counted():
    """'QRS-2026-001' and 'ZZ-90210': digits after hyphens are not standalone figures."""
    texts = {"a": "Tax form submitted."}  # '2026', '001', '12700' not in passage
    _, counts = check.check("See QRS-2026-001 and ZZ-90210 [#a].", texts)
    assert counts["unsupported_numbers"] == 0


def test_identifier_number_not_exempt_when_also_bare_figure():
    """A bare figure in the same sentence is still flagged even when identifiers are present."""
    texts = {"a": "See clause B0338."}
    # 99 appears nowhere in the passage; B0338's digits are already blocked by _NUMBER lookbehind
    _, counts = check.check("There are 99 items in B0338 [#a].", texts)
    assert counts["unsupported_numbers"] == 1  # 99 is unsupported


def test_currency_figures_are_still_strict():
    """£1,111.11 must still be checked strictly, not ignored as an identifier."""
    texts = {"a": "The fee is £500."}
    _, counts = check.check("The fee is £1,111.11 [#a].", texts)
    assert counts["unsupported_numbers"] >= 1


def test_percentage_figures_are_still_strict():
    """5% and 4.8% must be checked strictly."""
    texts = {"a": "The rate is 3%."}
    _, counts = check.check("The rate is 5% and 4.8% [#a].", texts)
    assert counts["unsupported_numbers"] >= 1


def test_paragraph_clause_numbers_are_still_strict():
    """'paragraph 9.4' must still be checked; it is NOT an identifier by our rules."""
    texts = {"a": "See paragraph 9.3 for details."}
    _, counts = check.check("As per paragraph 9.4 [#a].", texts)
    assert counts["unsupported_numbers"] >= 1


def test_appendix_number_is_not_an_identifier():
    """'Appendix 5' — the digit after a word is not a bare letter-code pattern; kept strict."""
    texts = {"a": "See Appendix 3."}
    _, counts = check.check("Refer to Appendix 5 [#a].", texts)
    assert counts["unsupported_numbers"] >= 1


# --- Title numbers tests ---

def test_number_in_document_title_counts_as_supported():
    """A number that appears only in the cited document's title is supported."""
    # Passage text itself does NOT contain 12700
    texts = {"a": "This is the agreement details."}
    titles = {"a": "ZZ-90210 Service Agreement"}
    _, counts = check.check("See ZZ-90210 agreement [#a].", texts, titles=titles)
    # ZZ-90210 is an identifier anyway; let's test a plain number from the title
    texts2 = {"b": "Terms as follows."}
    titles2 = {"b": "Contract 2019-B"}
    _, counts2 = check.check("Signed under Contract 2019 [#b].", texts2, titles=titles2)
    assert counts2["unsupported_numbers"] == 0


def test_number_absent_from_both_passage_and_title_still_unsupported():
    texts = {"a": "Terms as follows."}
    titles = {"a": "Service Agreement"}
    _, counts = check.check("The fee is 5000 [#a].", texts, titles=titles)
    assert counts["unsupported_numbers"] >= 1


# --- Render: same chunk id deduplication ---

def test_passage_id_and_bare_chunk_id_share_one_footnote():
    """If both `<cid>_p01` and `<cid>` (bare chunk) appear, they should reuse one footnote."""
    links = {
        "c1_p01": {"path": "p/doc.md", "title": "Doc"},
        "c1": {"path": "p/doc.md", "title": "Doc"},
    }
    text, order = render.render_body("A [#c1_p01]. B [#c1].", links)
    # Both should map to the same footnote number
    lines = text.splitlines()
    assert lines[0] == "A [^1]. B [^1]."
    assert len([l for l in lines if l.startswith("[^")]) == 1


def test_bare_chunk_id_footnote_uses_chunk_label_not_passage():
    """A citation with no `_pNN` suffix labels the footnote `chunk`, not `passage`."""
    links = {"c1": {"path": "p/doc.md", "title": "Doc"}}
    text, _ = render.render_body("A [#c1].", links)
    assert "chunk `c1`" in text
    assert "passage `c1`" not in text


def test_dd_mon_yyyy_and_mon_apostrophe_dates_match_other_formats():
    texts = {"a": "Labour period 01/Sep/2025 to 23/Oct/2026, resource list to Aug'26."}
    body = "The extract runs from 1 September 2025 to 23 October 2026. The list ends in August 2026 [#a]."
    _, counts = check.check(body, texts)
    assert counts["unsupported_numbers"] == 0
    wrong = "The extract starts on 9 September 2025 [#a]."
    assert check.check(wrong, texts)[1]["unsupported_numbers"] >= 1


def test_numbers_in_the_topic_text_are_supported_everywhere():
    texts = {"a": "The threshold is 10,000."}
    body = "A 15,000 deposit exceeds the 10,000 threshold [#a]. The Q4 2025 audit is not covered [#a]."
    assert check.check(body, texts)[1]["unsupported_numbers"] == 2   # without the topic: 15000 and 2025 ("Q4" is attached to a letter)
    topic = "A customer deposits 15,000; the Q4 2025 audit"
    assert check.check(body, texts, topic=topic)[1]["unsupported_numbers"] == 0


def test_label_plus_number_is_still_strict():
    texts = {"a": "Milestone 7 is due in June."}
    assert check.check("Milestone 12 is due in June [#a].", texts)[1]["unsupported_numbers"] == 1
