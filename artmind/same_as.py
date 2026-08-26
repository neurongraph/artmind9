"""Same-as groups — the one place a human judgment about identity may live.

The aggregate key is purely computed, so it never depends on stored state and
a rebuild is therefore reproducible. Everything that is a *judgment* — "these
two keys denote the same thing" — is exiled here, into a curated file, so that
the deterministic half stays deterministic.

**Groups, not pairs.** Pairs compose transitively and transitive identity
avalanches: on the live corpus, alias closure fused `FCA` + `PRA` +
`Regulatory Authorities` into one five-member component. A group is an
explicit, bounded assertion with one member named canonical.

**Convention: `group[0]` is the canonical member.** Every function in this
module and every consumer (`projection.py`'s group-aware rebuild) relies on
this ordering rather than carrying a second, parallel "which one is
canonical" value — `load_groups` enforces it on the way in, `save_groups`
preserves it on the way out.

Phase 6 ships `sameas propose / list / approve / reject` (`artmind/sameas.py`)
and the rebuild's application of groups (`projection.py`). This module stays
the curated-file boundary: loading, saving, and validating `same_as.yaml`
itself, nothing about how a group is proposed or applied.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

from artmind.observations import key_string
from paths import ARTMIND_HOME

# Run folder, not data dir: a same-as group is curation a human authored, and
# curation belongs beside `.env` and the schemas rather than among ingestion
# artifacts (see docs/stores-and-repos.md).
SAME_AS_PATH = Path(ARTMIND_HOME) / "same_as.yaml"


def _parse_key(value: str) -> tuple[str, str, str] | None:
    parts = str(value).split("|")
    if len(parts) != 3:
        logger.warning("same_as: ignoring malformed key {!r} (expected 'name|CLASS|domain')", value)
        return None
    return tuple(parts)  # type: ignore[return-value]


def validate_groups(
    groups: list[list[tuple[str, str, str]]]
) -> list[list[tuple[str, str, str]]]:
    """Drop overlap between groups rather than resolve it by closure.

    A key claimed by more than one group is a curation error, not a hint to
    merge the groups — "no union-find, no closure" is the whole point of
    groups over pairs. First group (file order) wins: if its own canonical is
    already claimed, the whole later group is dropped; if a non-canonical
    member is already claimed, only that member drops out of the later group.
    Tolerant by design, like `load_groups` itself — a curation-file mistake is
    a missed merge, which is visible and recoverable, not a failed commit.
    """
    claimed: set[tuple[str, str, str]] = set()
    out: list[list[tuple[str, str, str]]] = []
    for group in groups:
        canonical = group[0]
        if canonical in claimed:
            logger.warning(
                "same_as: canonical {} is already claimed by an earlier group; dropping this group",
                canonical,
            )
            continue
        kept = [canonical]
        for member in group[1:]:
            if member in claimed:
                logger.warning(
                    "same_as: key {} is already claimed by an earlier group; dropping it here",
                    member,
                )
                continue
            kept.append(member)
        claimed.add(canonical)
        if len(kept) > 1:
            claimed.update(kept)
            out.append(kept)
    return out


def load_groups(path: Path | None = None) -> list[list[tuple[str, str, str]]]:
    """Curated groups as lists of aggregate keys, canonical first.

    Tolerant by design: a malformed file, a group missing a valid `canonical`
    field, or overlapping groups yield fewer groups and a warning rather than
    failing an ingest. A missing group is a missed merge, which is visible and
    recoverable; a failed commit over a curation typo is not.
    """
    target = path or SAME_AS_PATH
    if not target.exists():
        return []
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("same_as: could not parse {}: {}", target, e)
        return []

    groups: list[list[tuple[str, str, str]]] = []
    for entry in raw.get("groups") or []:
        entry = entry or {}
        members = [_parse_key(m) for m in entry.get("members") or []]
        members = [m for m in members if m]
        canonical = _parse_key(entry["canonical"]) if entry.get("canonical") else None
        if len(members) < 2:
            continue
        if canonical is None or canonical not in members:
            logger.warning(
                "same_as: group has no valid 'canonical' among its members "
                "(canonical={!r}, members={}); skipping",
                entry.get("canonical"), members,
            )
            continue
        # Canonical first — the convention every consumer relies on.
        ordered = [canonical] + [m for m in members if m != canonical]
        groups.append(ordered)
    return validate_groups(groups)


def save_groups(groups: list[list[tuple[str, str, str]]], path: Path | None = None) -> None:
    """Write curated groups back to `same_as.yaml`. `group[0]` is canonical,
    matching `load_groups`'s convention exactly, so a round-trip is stable."""
    target = path or SAME_AS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "groups": [
            {"canonical": key_string(group[0]), "members": [key_string(m) for m in group]}
            for group in groups
        ]
    }
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def groups_touching(
    keys, groups: list[list[tuple[str, str, str]]] | None = None
) -> list[list[tuple[str, str, str]]]:
    """The groups that contain at least one of `keys`."""
    groups = groups if groups is not None else load_groups()
    wanted = set(keys)
    return [g for g in groups if wanted & set(g)]


def content_hash(path: Path | None = None) -> str:
    """Hash of the curation file, for `:ProjectionState`'s drift check. Empty
    string when there is no file — which is itself a stable, comparable
    value."""
    import hashlib

    target = path or SAME_AS_PATH
    if not target.exists():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()
