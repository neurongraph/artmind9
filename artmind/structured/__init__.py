import re
from pathlib import Path

STRUCTURED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}


def is_structured_source(path: Path) -> bool:
    return path.suffix.lower() in STRUCTURED_EXTENSIONS


def sanitize_identifier(name: str) -> str:
    """Reduce an arbitrary filestem/sheet name to a valid SQL identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name.strip()).strip("_").lower()
    if not s:
        s = "table"
    if s[0].isdigit():
        s = f"t_{s}"
    return s


def view_name(domain: str, table_name: str) -> str:
    """Domain-qualified DuckDB view identifier for a registered table.

    Two domains can register a same-named table (the registry key is now
    ``(datasource, domain, table_name)``), but the shared persistent DuckDB
    catalog has a single view namespace — this gives each domain's table a
    collision-free identifier there, distinct from the bare ``table_name``
    convenience alias ``duckdb_adapter.ensure_views`` also creates when it's
    unambiguous.
    """
    return f"{sanitize_identifier(domain)}__{table_name}"
