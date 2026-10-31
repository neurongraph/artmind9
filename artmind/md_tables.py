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
