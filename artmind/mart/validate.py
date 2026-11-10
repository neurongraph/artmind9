"""Validate every mart declaration against its domain schema (for `domains validate`)."""
from __future__ import annotations

import paths
from artmind.mart.declaration import MartDeclarationError, load_declaration, validate_declaration


def _schema(domain: str) -> dict:
    """The domain's schema, with a family root's children merged in (a declaration on
    `banking` may name classes declared by `banking.policy` and its siblings)."""
    from artmind.temporal import load_schema

    merged = dict(load_schema(domain) or {})
    types = dict(merged.get("entity_types") or {})
    directory = paths.DOMAIN_SCHEMAS_DIR
    if directory.is_dir():
        for f in sorted(directory.glob(f"{domain}.*_schema.yaml")):
            child = f.name[: -len("_schema.yaml")]
            for name, spec in ((load_schema(child) or {}).get("entity_types") or {}).items():
                types.setdefault(name, spec)
    merged["entity_types"] = types
    return merged


def validate_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    directory = paths.DOMAIN_MART_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        domain = path.stem
        try:
            decl = load_declaration(domain)
        except MartDeclarationError as e:
            out[domain] = {"errors": [str(e)], "warnings": []}
            continue
        errors, warnings = validate_declaration(decl, _schema(domain))
        out[domain] = {"errors": errors, "warnings": warnings}
    return out
