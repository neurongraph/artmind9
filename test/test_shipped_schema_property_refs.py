"""Shipped schemas point the extractor only at keys their classes declare (spec 2026-11-15 §2.5)."""
import re
from pathlib import Path

import pytest
import yaml

SCHEMAS = Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"
COMMON = {"name", "aliases", "entity_class", "type", "description", "context"}
_DOTTED = re.compile(r"\bproperties\.([a-z][a-z0-9_]*)")
_EXAMPLE_KEY = re.compile(r'"([a-z][a-z0-9_]*)"\s*:')


def _undeclared(schema: dict) -> list[str]:
    types = schema.get("entity_types") or {}
    declared = {c: set((d or {}).get("properties") or {}) for c, d in types.items()}
    every = set().union(*declared.values()) if declared else set()
    found = []
    g = schema.get("guidance") or {}
    for part in ("entities", "properties"):
        for key in _DOTTED.findall(str(g.get(part) or "")):
            if key not in every and key not in COMMON:
                found.append(f"guidance.{part}: {key}")
    for cls, decl in types.items():
        text = f"{(decl or {}).get('description') or ''}\n{(decl or {}).get('guidance') or ''}"
        keys = set(_DOTTED.findall(text))
        keys |= {k for line in text.splitlines() if "RIGHT:" in line for k in _EXAMPLE_KEY.findall(line)}
        found += [f"{cls}: {k}" for k in sorted(keys) if k not in declared[cls] and k not in COMMON]
    return found


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_guidance_names_only_declared_keys(path):
    assert _undeclared(yaml.safe_load(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_every_alias_targets_a_declared_key(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    declared = {p for d in (schema.get("entity_types") or {}).values() for p in ((d or {}).get("properties") or {})}
    assert {a: t for a, t in (schema.get("property_aliases") or {}).items() if t not in declared} == {}


def test_charge_guidance_does_not_invite_column_named_keys():
    schema = yaml.safe_load((SCHEMAS / "contracts_schema.yaml").read_text(encoding="utf-8"))
    assert "table's own column names" not in str(schema["entity_types"]["CHARGE"]["guidance"])
