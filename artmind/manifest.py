"""The ingest manifest — `.artmind/vault.yaml` (docs/vault.md).

`_meta/schema_mapping.md` in the banking corpus was this feature written as
prose: a table of which schema governs which folder, executed by hand as one
`ingest sync` per folder. Here it is configuration, and it does two jobs:

1. **Which domain** governs a path's extraction.
2. **Whether to ingest the path at all.** An unmapped path is never ingested,
   so an `attachments/` folder needs no separate ignore mechanism, and an
   unmapped `Inbox/` becomes a drafting area where *moving* a note into a
   mapped folder is what says "this is ready".

Separate from `artmind/vault.py` because that module must stay stdlib-only —
`paths.py` imports it at module load for every command. This one needs yaml and
is imported only by ingestion paths, which already pay for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from artmind.vault import MANIFEST, MARKER

# Only `manual` is acted on today. The others are accepted and validated so a
# manifest written for a later artmind does not fail to parse, but nothing
# schedules or hooks anything yet (see the triggers plan).
VALID_TRIGGERS = ("manual", "commit", "schedule")


class ManifestError(Exception):
    """`.artmind/vault.yaml` is unreadable, malformed, or self-contradictory."""


@dataclass(frozen=True)
class Mapping:
    """One `path` glob and the `domain` that governs everything matching it."""

    path: str
    domain: str

    def matches(self, relpath: str) -> bool:
        """Does this mapping cover `relpath` (vault-relative, posix)?

        `PurePath.full_match` gives real recursive-glob semantics, so
        `policies/**` covers `policies/a.md` and `policies/sub/b.md` alike.
        """
        return PurePosixPath(relpath).full_match(self.path)


@dataclass(frozen=True)
class Manifest:
    trigger: str = "manual"
    mappings: list[Mapping] = field(default_factory=list)

    def domain_for(self, relpath: str) -> str | None:
        """The domain governing `relpath`, or None when nothing maps it.

        **First match wins**, in the order written, so the manifest reads
        top-down like a routing table and a specific rule can be placed above a
        general one.
        """
        for mapping in self.mappings:
            if mapping.matches(relpath):
                return mapping.domain
        return None

    def should_ingest(self, relpath: str) -> bool:
        """Whether `relpath` is ingested at all.

        A manifest with NO mappings maps nothing and therefore filters nothing —
        a vault that has not configured mappings behaves exactly as before this
        feature, rather than suddenly ingesting zero files.
        """
        if not self.mappings:
            return True
        return self.domain_for(relpath) is not None


def filter_for_ingest(
    vault_root: "Path | None", path: Path, files: "list[Path]"
) -> "tuple[Manifest | None, list[Path], int]":
    """Load the vault manifest and drop files no mapping covers.

    Filtering applies to a directory WALK only -- naming a file is an
    explicit request and is always honoured, mapped or not. Returns the
    manifest (``None`` outside a vault), the files to actually ingest, and
    how many were skipped by the filter.

    Shared by every surface that can trigger ingestion (the CLI's `ingest
    sync`/`async` and the admin console's `/api/ingest`) so "an unmapped path
    is never ingested" (docs/vault.md) can't drift true on one and false on
    another. Each caller translates a `ManifestError` into its own error type
    (`click.ClickException` for the CLI, `HTTPException` for the admin API).
    """
    if vault_root is None:
        return None, files, 0
    vault_manifest = load(vault_root)

    if not path.is_dir():
        return vault_manifest, files, 0

    kept, skipped = [], 0
    for f in files:
        try:
            rel = f.resolve().relative_to(vault_root).as_posix()
        except ValueError:
            kept.append(f)  # outside the vault; the manifest says nothing
            continue
        if vault_manifest.should_ingest(rel):
            kept.append(f)
        else:
            skipped += 1
    return vault_manifest, kept, skipped


def load(vault_root: Path) -> Manifest:
    """Read `<vault_root>/.artmind/vault.yaml`.

    A missing manifest is the normal state for a vault created before this
    feature, or one being initialised — never an error.
    """
    path = Path(vault_root) / MARKER / MANIFEST
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return Manifest()

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: not valid YAML -- {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")

    ingest = data.get("ingest") or {}
    if not isinstance(ingest, dict):
        raise ManifestError(f"{path}: 'ingest' must be a mapping, got {type(ingest).__name__}")

    trigger = ingest.get("trigger", "manual")
    if trigger not in VALID_TRIGGERS:
        raise ManifestError(
            f"{path}: unknown ingest trigger {trigger!r}. "
            f"Choose from: {', '.join(VALID_TRIGGERS)}."
        )

    raw_mappings = ingest.get("mappings") or []
    if not isinstance(raw_mappings, list):
        raise ManifestError(f"{path}: 'ingest.mappings' must be a list")

    mappings: list[Mapping] = []
    for index, entry in enumerate(raw_mappings):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: ingest.mappings[{index}] must be a mapping")
        if not entry.get("path"):
            raise ManifestError(f"{path}: ingest.mappings[{index}] is missing 'path'")
        if not entry.get("domain"):
            raise ManifestError(
                f"{path}: ingest.mappings[{index}] ({entry['path']}) is missing 'domain'"
            )
        mappings.append(Mapping(path=str(entry["path"]), domain=str(entry["domain"])))

    return Manifest(trigger=trigger, mappings=mappings)
