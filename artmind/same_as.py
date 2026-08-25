"""Same-as groups — the one place a human judgment about identity may live.

The aggregate key is purely computed, so it never depends on stored state and
a rebuild is therefore reproducible. Everything that is a *judgment* — "these
two keys denote the same thing" — is exiled here, into a curated file, so that
the deterministic half stays deterministic.

**Groups, not pairs.** Pairs compose transitively and transitive identity
avalanches: on the live corpus, alias closure fused `FCA` + `PRA` +
`Regulatory Authorities` into one five-member component. A group is an
explicit, bounded assertion with one member named canonical.

Phase 3 ships the **seam only**. `same_as.yaml` does not exist yet and this
loader returns an empty list, which makes set 3 of `projection.affected_keys`
inert but present and tested. Phase 6 adds `sameas propose / list / approve /
reject` and the application of groups during the rebuild.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

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


def load_groups(path: Path | None = None) -> list[list[tuple[str, str, str]]]:
    """Curated groups as lists of aggregate keys.

    Tolerant by design: a malformed file yields no groups and a warning rather
    than failing an ingest. A missing group is a missed merge, which is
    visible and recoverable; a failed commit over a curation typo is not.
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
        members = [_parse_key(m) for m in (entry or {}).get("members") or []]
        members = [m for m in members if m]
        if len(members) > 1:
            groups.append(members)
    return groups


def groups_touching(
    keys, groups: list[list[tuple[str, str, str]]] | None = None
) -> list[list[tuple[str, str, str]]]:
    """The groups that contain at least one of `keys`."""
    groups = groups if groups is not None else load_groups()
    wanted = set(keys)
    return [g for g in groups if wanted & set(g)]


def content_hash(path: Path | None = None) -> str:
    """Hash of the curation file, for the Phase 6 `:ProjectionState` drift
    check. Empty string when there is no file — which is itself a stable,
    comparable value."""
    import hashlib

    target = path or SAME_AS_PATH
    if not target.exists():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()
