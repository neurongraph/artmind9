"""Temporal normalization: canonical valid-time / event-time properties.

Non-destructive: original domain-named properties are never touched; canonical
`valid_from`/`valid_to`/`event_at` are additive copies. Idempotent on re-run.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response
from paths import DOMAIN_SCHEMAS_DIR, MARKDOWNS_DIR

_MONTHS = {
    m.lower(): i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_ISO_FULL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_PARTIAL_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
_DMY_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
_MDY_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")


def parse_iso(value: str | None) -> str | None:
    """Deterministically parse a date-ish string to ISO-8601 (date), else None.

    Accepts full ISO, partial ISO (year / year-month), '15 March 2026',
    'March 15, 2026'. Returns the input unchanged for partial ISO.
    """
    if not value:
        return None
    v = value.strip()
    if _ISO_FULL_RE.match(v):
        return _ISO_FULL_RE.match(v).group(0)
    if _ISO_PARTIAL_RE.match(v):
        return v
    m = _DMY_RE.match(v)
    if m and m.group(2).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = _MDY_RE.match(v)
    if m and m.group(1).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def _find_header_value(md_text: str, labels: list[str]) -> str | None:
    """Find a label's value in the markdown body.

    Tries two formats, in order, since the real corpus uses the table form
    exclusively (verified against banking_document_corpus/policies/*.md —
    every document uses a "| Field | Value |" metadata table, never colon
    prose):
      1. Markdown table row: "| Label | Value |"
      2. Colon-delimited prose: "**Label:** value" / "Label: value"
    """
    for label in labels:
        table_pat = re.compile(
            r"^\|\s*" + re.escape(label) + r"\s*\|\s*(.+?)\s*\|\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = table_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
        prose_pat = re.compile(
            r"^\**\s*" + re.escape(label) + r"\s*:\**\s*(.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = prose_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
    return None


def lift_document_dates(md_text: str, frontmatter: dict, mapping: dict) -> dict:
    """Return canonical document props from header labels / frontmatter.

    mapping example: {"valid_from": ["Effective Date"], "version": ["Version"]}
    Records time_source: 'header' | 'frontmatter'.
    """
    out: dict = {}
    source = None
    for canon, labels in mapping.items():
        raw = _find_header_value(md_text, labels)
        if raw is not None:
            source = source or "header"
        else:
            for lbl in labels:
                if frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl):
                    raw = str(frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl))
                    source = source or "frontmatter"
                    break
        if raw is None and canon == "valid_from" and frontmatter.get("date"):
            raw = str(frontmatter["date"])
            source = source or "frontmatter"
        if raw is None:
            continue
        if canon == "version":
            out["version"] = raw
        else:
            iso = parse_iso(raw)
            out[canon] = iso if iso else raw
    if out:
        out["time_source"] = source or "header"
    return out


def load_schema(domain: str) -> dict:
    path = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
