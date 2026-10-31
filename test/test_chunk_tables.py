"""A table split across chunks: every chunk holding its rows starts with the
table's header row, and no chunk ends on a bare header."""
import artmind.ingest as ing

HEADER = "| Check | Rule |\n| - | - |"


def _table_body(rows: int, width: int = 60) -> str:
    lines = [f"| Check {i:02d} {'x' * width} | Rule {i:02d} |" for i in range(rows)]
    return "Intro paragraph.\n\n" + HEADER + "\n" + "\n".join(lines) + "\n\nAfter."


def _rows(text: str) -> list[str]:
    return [l for l in text.split("\n") if l.startswith("| Check ") and l != HEADER.split("\n")[0]]


def test_every_chunk_holding_rows_starts_with_the_header():
    body = _table_body(30)

    chunks = ing._split_markdown(body, chunk_size=600)

    with_rows = [c["text"] for c in chunks if _rows(c["text"])]
    assert len(with_rows) > 2
    for text in with_rows:
        table_lines = [l for l in text.split("\n") if ing._is_table_row(l)]
        assert "\n".join(table_lines[:2]) == HEADER, text
    assert sum(len(_rows(t)) for t in with_rows) == 30, "every row once"


def test_a_header_that_would_end_a_chunk_moves_to_the_next():
    filler = "Filler sentence that takes up room. " * 14  # ~500 chars before the table
    body = filler.strip() + "\n" + HEADER + "\n" + "\n".join(
        f"| Check {i:02d} {'y' * 70} | Rule |" for i in range(6)
    )

    pieces = ing._carry_trailing_table_headers([filler.strip() + "\n" + HEADER, body.split(HEADER + "\n", 1)[1]])
    chunks = ing._split_markdown(body, chunk_size=560)

    assert pieces[0] == filler.strip()
    assert pieces[1].startswith(HEADER + "\n| Check 00")
    for c in chunks:
        lines = ["", *c["text"].rstrip().split("\n")]
        assert not (ing._is_table_row(lines[-2]) and ing._is_table_separator_row(lines[-1])), c["text"]
        assert c["char_start"] != -1 and c["char_end"] > c["char_start"]


def test_a_chunk_keeps_the_blank_line_before_a_table_and_list_indentation():
    """The header splitter joins lines with "  \\n" and drops blank lines and
    indentation; a chunk is the source span instead, so a table stays a table
    (a markdown reader needs the blank line) and a callout stays in its item."""
    body = (
        "# Costs\n\nThe summary is here:\n\n" + HEADER + "\n| Rent | 10 |\n\n"
        "1. Terms\n    - (a) pay on time\n      > [!comment] Comment\n      > A: Agreed.\n\n## Next\n\nMore."
    )

    chunks = ing._split_markdown(body, chunk_size=6000)

    text = "\n\n".join(c["text"] for c in chunks)
    assert "The summary is here:\n\n" + HEADER in text
    assert "    - (a) pay on time\n      > [!comment] Comment" in text
    assert "  \n" not in text
    for c in chunks:
        assert body[c["char_start"]:c["char_end"]] == c["text"]
