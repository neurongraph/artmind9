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
