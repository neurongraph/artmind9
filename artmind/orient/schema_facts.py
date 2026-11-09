"""Facts a domain's schema file declares about itself (spec 2026-11-09 §3, §4.1). No graph access.

`read(domain)` returns None when the domain has no schema here, and raises SchemaFactsError when
the file exists but cannot be parsed, so the caller can name the part in `warnings`.
"""
from __future__ import annotations

import re

PURPOSE_MAX = 200
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SchemaFactsError(ValueError):
    pass


def _squash(text) -> str:
    return " ".join(str(text or "").split())


def _purpose(description: str) -> str | None:
    text = _squash(description)
    if not text:
        return None
    if len(text) <= PURPOSE_MAX:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > PURPOSE_MAX:
            break
        out = candidate
    return out or text[:PURPOSE_MAX].rsplit(" ", 1)[0]


def _first_field(names) -> str | None:
    names = names if isinstance(names, list) else [names]
    return next((str(n) for n in names if n), None)


def _filename_pattern(temporal: dict) -> str | None:
    for rule in ((temporal.get("document_patterns") or {}).get("valid_from") or []):
        if isinstance(rule, dict) and rule.get("filename"):
            return str(rule["filename"])
    return None


def _time_rule(temporal: dict) -> dict:
    document = temporal.get("document") or {}
    rule: dict = {}
    for key in ("valid_from", "version"):
        field = _first_field(document.get(key))
        parts = [f"document field '{field}'"] if field else []
        if key == "valid_from" and (pattern := _filename_pattern(temporal)):
            parts.append(f"filename {pattern}")
        if parts:
            rule[key] = ", or ".join(parts)
    return rule


def read(domain: str) -> dict | None:
    from artmind import temporal

    try:
        schema = temporal.load_schema(domain)
    except Exception as exc:  # a YAML error or unreadable file
        raise SchemaFactsError(f"{domain}: schema unreadable: {exc}") from exc
    if not schema:
        return None
    if not isinstance(schema, dict):
        raise SchemaFactsError(f"{domain}: schema is not a mapping")
    temporal_block = schema.get("temporal") or {}
    classes = {}
    for name, spec in (schema.get("entity_types") or {}).items():
        spec = spec or {}
        classes[str(name)] = {"kind": spec.get("kind"), "definition": _squash(spec.get("description")) or None}
    return {
        "purpose": _purpose(schema.get("description")),
        "time_rule": _time_rule(temporal_block),
        "filename_pattern": _filename_pattern(temporal_block),
        "classes": classes,
    }
