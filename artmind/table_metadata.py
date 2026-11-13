"""Hand-written descriptions of a domain's structured tables (text2sql metadata).

Reads and writes `<DOMAIN_METADATA_DIR>/<domain>.meta.yaml`, the sidecar `sql_browser.metadata_for`
merges into every text2sql call. Only `tables.<domain>__<table>.description` and
`.columns.<column>.description` are touched; every other key (values, glossary, rules, prose)
survives a save. An empty text removes the description. No LLM, no store access.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

import paths
from artmind.structured import view_name


def path_for(domain: str) -> Path:
    return Path(paths.DOMAIN_METADATA_DIR) / f"{domain}.meta.yaml"


def _load(domain: str) -> dict:
    p = path_for(domain)
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} is not a mapping")
    return data


def _entry(data: dict, domain: str, table: str) -> dict:
    tables = data.get("tables")
    return (tables or {}).get(view_name(domain, table)) or {} if isinstance(tables, dict) else {}


def read(domain: str, table: str) -> dict:
    """`{"description": str, "columns": {column: description}}` for one table (empty when none)."""
    entry = _entry(_load(domain), domain, table)
    cols = entry.get("columns") if isinstance(entry.get("columns"), dict) else {}
    return {"description": str(entry.get("description") or ""),
            "columns": {c: str(v.get("description") or "") for c, v in cols.items()
                        if isinstance(v, dict) and v.get("description")}}


def describe(domain: str, table: str, text: str, column: str | None = None, *, dry_run: bool = False) -> dict:
    """Set (or, with empty text, remove) a table's or one column's description.

    Returns `{"path", "changed", "before", "after"}`; writes nothing on a dry run or when unchanged.
    """
    text = (text or "").strip()
    data = _load(domain)
    before = (read(domain, table)["columns"].get(column, "") if column else read(domain, table)["description"])
    result = {"path": str(path_for(domain)), "table": table, "column": column,
              "changed": before != text, "before": before, "after": text}
    if dry_run or not result["changed"]:
        return result
    data.setdefault("version", 1)
    tables = data.get("tables") if isinstance(data.get("tables"), dict) else {}
    data["tables"] = tables
    entry = tables.get(view_name(domain, table))
    if not isinstance(entry, dict):
        entry = {}
        tables[view_name(domain, table)] = entry
    if column is None:
        entry["description"] = text
    else:
        cols = entry.get("columns") if isinstance(entry.get("columns"), dict) else {}
        entry["columns"] = cols
        col = cols.get(column) if isinstance(cols.get(column), dict) else {}
        cols[column] = col
        col["description"] = text
    p = path_for(domain)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, p)
    return result
