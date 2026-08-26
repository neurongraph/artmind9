"""Derived-markdown promotion (docs/document-identity.md, "Derived-markdown
promotion"). Phase 5 work inherited from Phase 2 (docs/redesign-phase-plan.md,
Phase 5 "D").

A binary source's docling conversion lands in the vault as a genuine
vault-native document, at ``<vault>/_derived/<domain>/<stem>.md`` — carrying
its own ``_artmind_id``, versioned, editable in the user's editor, git-
committed like anything else. Repairing a mangled table is the first thing
anyone does to converted output, and re-running conversion would clobber
that edit — so an edit **promotes** the document to vault-native: the binary
stops being the source, and reconversion is refused from then on.

Two independent signals, checked on every ingest of the original binary:

- ``markdown_edited`` — the vault file's current body no longer hashes to
  ``_derived_sha256``, the fingerprint taken at the last conversion. This is
  the promotion trigger, full stop, regardless of whether the binary changed
  too.
- ``binary_changed`` — the original file's bytes no longer hash to what the
  registry (``artmind/db.py``, the path <-> id cache) last recorded for it.
  Independent of the above; needed only to tell a plain reconversion apart
  from the collision case.

This module is pure — the decision only, no filesystem or git I/O. The
orchestration (docling, registry rows, git commits) lives in
``artmind/ingest.py``, which is where a binary's other conversion machinery
already lives.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmind.document_identity import compute_content_sha256

# Where a domain's binary sources' derived markdown lives. Deterministic by
# design (domain + the original's filename stem) -- per docs/document-
# identity.md, "sources that cannot carry frontmatter", a binary stays
# path-keyed by filename+domain (unchanged from Phase 2's `_canonical_key`),
# so relocating its derived doc never needs a registry round-trip: the path
# to check is computable directly from the incoming file.
_DERIVED_SUBDIR = "_derived"


def derived_markdown_path(vault_dir: Path, domain: str, stem: str) -> Path:
    """Where `stem`'s (not-yet-promoted) derived markdown lives for `domain`."""
    return vault_dir / _DERIVED_SUBDIR / domain / f"{stem}.md"


def is_promoted(meta: dict) -> bool:
    """Has this document already been promoted out of `_derived/`?

    Promotion overwrites `_source_type` from the original binary's kind
    (`pptx`, `pdf`, ...) to `md` (docs/document-identity.md's promote table)
    — that field is the one durable signal, since the file itself may since
    have moved anywhere in the vault.
    """
    return meta.get("_source_type") == "md"


@dataclass(frozen=True)
class PromotionDecision:
    # "convert"   -- (re)run docling and overwrite the derived body; safe,
    #                nobody has edited it.
    # "no_op"     -- neither the binary nor the derived markdown changed.
    # "promote"   -- a human edited the derived markdown; stop deriving it.
    # "collision" -- both changed; artmind must not guess which side wins.
    action: str
    markdown_edited: bool
    binary_changed: bool


def decide(*, markdown_edited: bool, binary_changed: bool) -> PromotionDecision:
    """The 2x2 from docs/document-identity.md's "Detect"/"Collision" rules,
    for a derived document that already exists (a brand-new binary has no
    2x2 to run — see `derived_markdown_path` not existing, the caller's own
    "convert" case for a first-ever ingest)."""
    if markdown_edited and binary_changed:
        return PromotionDecision("collision", markdown_edited=True, binary_changed=True)
    if markdown_edited:
        return PromotionDecision("promote", markdown_edited=True, binary_changed=False)
    if binary_changed:
        return PromotionDecision("convert", markdown_edited=False, binary_changed=True)
    return PromotionDecision("no_op", markdown_edited=False, binary_changed=False)


def markdown_was_edited(current_body: str, derived_sha256: str | None) -> bool:
    """`derived_sha256 is None` (frontmatter lost the fingerprint, or this is
    somehow the first check) reads as "edited" — the conservative direction:
    it routes to `promote`, never to a silent reconversion that could
    clobber a hand-fix nobody can prove didn't happen."""
    if derived_sha256 is None:
        return True
    return compute_content_sha256(current_body) != derived_sha256
