"""Per-domain config (spec 2026-11-09 §5.3).

`<ARTMIND_HOME>/domains/plan/<domain>.yaml` is user data (never seeded). A parent
domain's file applies to a child without one (most specific wins, never merged).
This module reads what the CLI needs: the domain-name rule and the narrator. Other keys
(written for the planner removed in 0.13) are ignored.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_DOMAIN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*")
_CLASS_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


class DomainConfigError(ValueError):
    """A per-domain config file cannot be read or breaks the narrator shape."""


def is_domain_name(name) -> bool:
    """True for a dotted domain name whose whole string matches (no trailing newline, no path parts)."""
    return isinstance(name, str) and _DOMAIN_RE.fullmatch(name) is not None


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_PLAN_DIR)


def config_file(domain: str) -> Path | None:
    """The most specific file for `domain`: its own, else its nearest parent's."""
    if not is_domain_name(domain):
        return None
    parts = domain.split(".")
    for i in range(len(parts), 0, -1):
        path = config_dir() / f"{'.'.join(parts[:i])}.yaml"
        if path.is_file():
            return path
    return None


def _class_name(raw: str, source: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip()).upper()
    if not _CLASS_RE.fullmatch(name) or not re.search(r"[A-Z]", name):
        raise DomainConfigError(f"{source}: narrator entity_class {raw!r} is not a usable class name")
    return name


def narrator(domain: str) -> dict | None:
    """`{entity_class, name, aliases}` from the domain's config file, or None when no file or no
    narrator is configured. Raises DomainConfigError for an unreadable file or a malformed narrator."""
    path = config_file(domain)
    if path is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DomainConfigError(f"{path} cannot be read: {exc}") from exc
    raw = data.get("narrator") if isinstance(data, dict) else None
    if raw is None:
        return None
    if (not isinstance(raw, dict) or not {"entity_class", "name"} <= set(raw) <= {"entity_class", "name", "aliases"}
            or not all(isinstance(raw[k], str) and raw[k].strip() for k in raw if k != "aliases")):
        raise DomainConfigError(f"{path}: narrator must be {{entity_class, name, aliases?}} (strings; aliases a list)")
    aliases = raw.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
        raise DomainConfigError(f"{path}: narrator aliases must be a list of non-empty strings")
    seen, unique = set(), []
    for alias in (a.strip() for a in aliases):
        if alias.casefold() not in seen:
            seen.add(alias.casefold())
            unique.append(alias)
    return {"entity_class": _class_name(raw["entity_class"], str(path)), "name": raw["name"].strip(),
            "aliases": unique}
