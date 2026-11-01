"""Markdown table helpers shared by chunk and passage splitting."""


def is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1


def is_table_separator_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and "-" in stripped and set(stripped) <= set("|:- ")


def reattach_table_headers(sub_chunks: list[str]) -> list[str]:
    """Re-prepend the most recent markdown table header+separator row to any
    chunk that starts mid-table, so a table split across chunks doesn't hand
    the extraction LLM unlabeled rows with no column context."""
    result = []
    last_header: tuple[str, str] | None = None
    for chunk in sub_chunks:
        lines = chunk.split("\n")
        starts_mid_table = is_table_row(lines[0]) and not (
            len(lines) > 1 and is_table_separator_row(lines[1])
        )
        if starts_mid_table and last_header:
            chunk = "\n".join([*last_header, chunk])
            lines = chunk.split("\n")
        for i in range(len(lines) - 1):
            if is_table_row(lines[i]) and is_table_separator_row(lines[i + 1]):
                last_header = (lines[i], lines[i + 1])
        result.append(chunk)
    return result


def carry_trailing_table_headers(pieces: list[str]) -> list[str]:
    """`pieces` with a table header (header row + separator) that ends a piece,
    with none of its rows after it, moved to the start of the next piece: a
    chunk ending on a bare header extracts nothing from it, and the next one
    needs it anyway. The pieces stay verbatim slices of the text (the splitter
    cut them at the newline that is put back here)."""
    result = list(pieces)
    for i in range(len(result) - 1):
        lines = result[i].rstrip("\n").split("\n")
        if (
            len(lines) > 2
            and is_table_row(lines[-2])
            and is_table_separator_row(lines[-1])
            and is_table_row(result[i + 1].lstrip("\n").split("\n", 1)[0])
        ):
            result[i] = "\n".join(lines[:-2]).rstrip("\n")
            result[i + 1] = "\n".join(lines[-2:]) + "\n" + result[i + 1].lstrip("\n")
    return result
