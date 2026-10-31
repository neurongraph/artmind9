import pytest

from artmind import passages as P


def para(n, word="alpha"):
    return " ".join([word] * n) + "."


def test_short_chunk_is_one_trimmed_passage():
    text = "\n  Hello world.  \n"
    out = P.split(text, 1200)
    assert len(out) == 1
    p = out[0]
    assert (p.seq, p.text) == (1, "Hello world.")
    assert text[p.char_start:p.char_end] == "Hello world."


def test_blank_chunk_has_no_passages():
    assert P.split("   \n\n ", 1200) == []


def test_offsets_slice_the_parent_and_cover_it():
    text = "\n\n".join(para(40, w) for w in ("alpha", "beta", "gamma", "delta", "eps", "zeta"))
    out = P.split(text, 500)
    assert len(out) > 2
    for p in out:
        assert text[p.char_start:p.char_end] == p.text
        assert len(p.text) <= 500
        assert not p.text[0].isspace() and not p.text[-1].isspace()
    assert out[0].char_start == 0 and out[-1].char_end == len(text)
    for a, b in zip(out, out[1:]):
        assert text[a.char_end:b.char_start].strip() == ""   # overlap, or a whitespace-only gap
        assert b.char_start > a.char_start                    # progress


def test_prefers_paragraph_boundaries():
    text = para(60, "alpha") + "\n\n" + para(60, "beta")
    first = P.split(text, 500)[0]
    assert first.text.endswith(".") and "beta" not in first.text


def test_never_cuts_mid_word():
    text = " ".join(f"word{i}" for i in range(400))
    for p in P.split(text, 300):
        assert not p.text.split()[0][0].isdigit()   # starts at a word start
        assert p.text.split()[-1].startswith("word")


def test_table_header_reattached_to_a_later_passage():
    rows = "\n".join(f"| r{i} | {'v' * 40} |" for i in range(40))
    text = "| col | val |\n| --- | --- |\n" + rows
    out = P.split(text, 400)
    assert len(out) > 1
    assert out[1].text.startswith("| col | val |\n| --- | --- |")


def test_deterministic():
    text = "\n\n".join(para(50, w) for w in ("a", "b", "c", "d"))
    assert P.split(text, 300) == P.split(text, 300)


def test_split_key_and_ids(monkeypatch):
    monkeypatch.setattr(P, "passage_chars", lambda: 900)
    assert P.split_key() == f"{P.SPLIT_VERSION}:900"
    assert P.passage_id("doc_003", 2) == "doc_003_p02"


def test_embedding_text_puts_context_first():
    assert P.embedding_text("Body.", "Paper > Methods") == "Paper > Methods\nBody."
    assert P.embedding_text("Body.", "") == "Body."


def test_passage_rows_carry_chunk_fields(monkeypatch):
    monkeypatch.setattr(P, "passage_chars", lambda: 1200)
    chunk = {"id": "d1_001", "doc_id": "d1", "_domain": "papers", "text": "Some text.",
             "header_path": "Intro", "valid_from": "2026-01-01"}
    rows = P.passage_rows(chunk, "My Paper")
    assert rows == [{
        "id": "d1_001_p01", "chunk_id": "d1_001",
        "props": {"id": "d1_001_p01", "chunk_id": "d1_001", "doc_id": "d1", "_domain": "papers",
                  "seq": 1, "char_start": 0, "char_end": 10, "text": "Some text.",
                  "context": "My Paper > Intro", "_split_key": f"{P.SPLIT_VERSION}:1200",
                  "valid_from": "2026-01-01"},
    }]
