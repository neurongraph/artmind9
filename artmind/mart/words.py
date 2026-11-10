"""Word lists for the person dimension: built-in, plus an optional vault-local
`<DOMAIN_MART_DIR>/words/<name>.yaml` whose entries override built-in ones."""
from __future__ import annotations

from importlib import resources

import yaml

import paths


def load(name: str) -> dict:
    base = yaml.safe_load(resources.files("artmind.mart").joinpath("data", f"{name}.yaml").read_text()) or {}
    local = paths.DOMAIN_MART_DIR / "words" / f"{name}.yaml"
    if local.is_file():
        extra = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        for part in ("exact", "patterns"):
            base.setdefault(part, {}).update(extra.get(part) or {})
    return {"exact": base.get("exact") or {}, "patterns": base.get("patterns") or {}}
