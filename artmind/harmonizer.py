"""Schema harmonizer: sync child domain schemas against their parent.

Pre-redesign this did regex block-surgery on three prose blobs. Now that
`entity_types` is a map of structured declarations (see docs/redesign-phase-
plan.md Phase 1), harmonizing a child against its parent is what it always
should have been: a dict merge. A class missing from the child is copied
across whole -- kind, description, properties, relates_to, guidance -- so the
child stays a self-contained superset of the parent, exactly as before.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from artmind.schema_validate import load_meta, validate_schema
from paths import DOMAIN_SCHEMAS_DIR


@dataclass
class HarmonizeResult:
    domain: str
    status: str           # "in_sync" | "updated" | "dry_run" | "error"
    added: list[str] = field(default_factory=list)
    error: str = ""


def _str_presenter(dumper, data):
    """Dump multi-line strings as YAML block literals (`|`) instead of one
    long quoted line -- schemas are meant to be hand-read and hand-edited."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.add_representer(str, _str_presenter, Dumper=yaml.SafeDumper)


def _load_schema(schema_path: Path) -> dict:
    return yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}


def _write_schema(schema_path: Path, data: dict) -> None:
    schema_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def harmonize_schema(
    child_name: str,
    dry_run: bool = False,
    validate: bool = True,
) -> HarmonizeResult:
    """Harmonize a single child schema against its parent."""
    parent_name = child_name.rsplit(".", 1)[0]
    child_path = DOMAIN_SCHEMAS_DIR / f"{child_name}_schema.yaml"
    parent_path = DOMAIN_SCHEMAS_DIR / f"{parent_name}_schema.yaml"

    if not child_path.exists():
        return HarmonizeResult(child_name, "error", error=f"Child schema not found: {child_path}")
    if not parent_path.exists():
        return HarmonizeResult(child_name, "error", error=f"Parent schema not found: {parent_path}")

    child = _load_schema(child_path)
    parent = _load_schema(parent_path)

    parent_types = parent.get("entity_types") or {}
    child_types = child.get("entity_types") or {}
    if not isinstance(parent_types, dict) or not isinstance(child_types, dict):
        return HarmonizeResult(
            child_name, "error",
            error="entity_types must be a map -- run the Phase 1 migration on this schema first",
        )

    missing = set(parent_types) - set(child_types)
    if not missing:
        return HarmonizeResult(child_name, "in_sync")

    if dry_run:
        return HarmonizeResult(child_name, "dry_run", added=sorted(missing))

    for cls in missing:
        child_types[cls] = parent_types[cls]
    child["entity_types"] = child_types

    if validate:
        errors = validate_schema(child, load_meta(), schema_name=child_name)
        if errors:
            return HarmonizeResult(child_name, "error", error="; ".join(errors))

    _write_schema(child_path, child)
    return HarmonizeResult(child_name, "updated", added=sorted(missing))


def harmonize_all(dry_run: bool = False) -> list[HarmonizeResult]:
    """Harmonize all child schemas found in DOMAIN_SCHEMAS_DIR."""
    results = []
    for schema_file in sorted(DOMAIN_SCHEMAS_DIR.glob("*_schema.yaml")):
        data = yaml.safe_load(schema_file.read_text(encoding="utf-8")) or {}
        name = data.get("name", "")
        if "." in name:
            results.append(harmonize_schema(name, dry_run=dry_run))
    return results
