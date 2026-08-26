"""Document identity, versioning, and the frontmatter contract (Phase 2).

Full specification: docs/document-identity.md. Read that before touching this
file — every design choice here (why `_content_sha256` is body-only, why
`move` must be silent, why `refuse` doesn't guess) is argued there, not here.

Identity is `_artmind_id` — a uuid7 written into a vault-native document's own
frontmatter on first ingest — never a hash of path or filename, both of which
are mutable. `document_registry.db` (artmind/db.py) is a path <-> id CACHE
used to tell a move from a duplicate; it is not authoritative.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from artmind.db import _registry_row_by_artmind_id, _registry_row_by_path
from paths import ARTMIND_VAULT_DIR, MARKDOWNS_DIR

# ── the frontmatter contract ─────────────────────────────────────────────────
# System: artmind writes these; extraction must never emit them. The
# underscore *is* the rule — it means artmind owns the property. Order here is
# the order they're serialized in, so the file reads sensibly to a human.
SYSTEM_FIELDS = (
    "_artmind_id",
    "_version",
    "_content_sha256",
    "_domain",
    "_status",
    "_valid_from",
    "_valid_to",
    "_valid_time_source",
    "_source_commit",
    "_source_path",
    "_source_type",
    "_ingested_at",
    # Phase 5 (docs/document-identity.md, "Derived-markdown promotion"):
    # present only on a binary source's not-yet-promoted derived markdown —
    # the fingerprint taken at conversion, compared against the CURRENT body
    # on every ingest to detect a human edit. Removed outright on promotion,
    # unlike every other system field (see artmind/derived_markdown.py).
    "_derived_sha256",
)

# Authored: artmind seeds a value once (only if absent), then never touches it
# again — a human's edit to any of these must survive every future ingest.
AUTHORED_FIELDS = (
    "title",
    "project",
    "area",
    "tags",
    "declared_version",
    "created_on",
    "modified_on",
)


def mint_artmind_id() -> str:
    """A uuid7: bare value, full length, no prefix. Time-ordered, so `docs
    list` reads chronologically for free without a separate sort key."""
    return str(uuid.uuid7())


def compute_content_sha256(body: str) -> str:
    """Hash the BODY ONLY — frontmatter is deliberately excluded.

    Otherwise artmind writing `_version: 2` changes the file's bytes, which
    changes the hash, which triggers version 3 on the next ingest, forever.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_path(source: Path) -> str:
    """The registry's lookup key for `source`: vault-relative when a vault is
    configured and the file lives inside it, else the resolved absolute path.

    This is a cache key, not identity — Phase 2's identity is `_artmind_id`,
    full stop. Case-preserving (unlike the old `_canonical_key`'s casefold):
    there is no longer a global cross-case dedup guard for this to serve.
    """
    try:
        resolved = source.resolve()
    except Exception:
        resolved = source
    if ARTMIND_VAULT_DIR is not None:
        try:
            return resolved.relative_to(ARTMIND_VAULT_DIR).as_posix()
        except ValueError:
            pass
    return str(resolved)


def resolve_canonical_path(path_str: str) -> Path:
    """The inverse of `canonical_path`: turn a registry-stored path string
    back into a real filesystem `Path`. A vault-relative string (the common
    case — anything `canonical_path` found inside the configured vault) is
    resolved against `ARTMIND_VAULT_DIR`; an absolute string (a file outside
    the vault, or no vault configured) is returned as-is.
    """
    p = Path(path_str)
    if p.is_absolute():
        return p
    if ARTMIND_VAULT_DIR is None:
        raise ValueError(
            f"{path_str!r} looks vault-relative but no ARTMIND_VAULT_DIR is configured"
        )
    return ARTMIND_VAULT_DIR / p


def _path_exists(path_str: str) -> bool:
    """Does the registry's recorded path still hold a file? The discriminator
    between `move` (gone -> silent, it's a `git mv`) and `refuse` (still
    there -> two live claimants)."""
    if ARTMIND_VAULT_DIR is not None:
        candidate = ARTMIND_VAULT_DIR / path_str
        if candidate.exists():
            return True
    return Path(path_str).exists()


# ── resolution table ─────────────────────────────────────────────────────────

VERDICTS = ("reingest", "move", "refuse", "adopt", "heal", "new")


@dataclass(frozen=True)
class Resolution:
    verdict: str
    artmind_id: str
    prior_path: str | None = None  # populated for move


class IdentityConflict(Exception):
    """A `refuse` verdict raised without `fork`/`adopt`.

    Deliberate: two files sharing one id express two indistinguishable human
    intents ("I copied it to make the next version" vs "I used it as a
    template"), and artmind must not guess which.
    """

    def __init__(self, artmind_id: str, existing_path: str, new_path: str):
        self.artmind_id = artmind_id
        self.existing_path = existing_path
        self.new_path = new_path
        super().__init__(
            f"_artmind_id {artmind_id!r} is already registered to {existing_path!r}, "
            f"and that file still exists — {new_path!r} cannot claim the same identity. "
            "Pass fork=True to mint a fresh id for the newcomer, or adopt=True to "
            "transfer identity to the newcomer (the old claimant is left as-is)."
        )


def resolve_identity(
    source: Path,
    frontmatter_id: str | None,
    *,
    fork: bool = False,
    adopt: bool = False,
) -> Resolution:
    """The six-row resolution table (docs/document-identity.md).

    ``frontmatter_id`` is whatever `_artmind_id` the file's CURRENT
    frontmatter carries (``None`` if absent). Pure with respect to the
    registry — never writes to it; callers apply the verdict (registering,
    moving, or healing frontmatter) themselves.

    Raises ``IdentityConflict`` on `refuse` unless ``fork`` or ``adopt`` is
    set — see the class docstring for what each does.
    """
    path = canonical_path(source)

    if frontmatter_id:
        by_id = _registry_row_by_artmind_id(frontmatter_id)
        if by_id is None:
            # The common case after a registry wipe, an archive restore, or a
            # file from another artmind instance. Minting here would silently
            # fork every document in the vault on the first re-ingest.
            return Resolution(verdict="adopt", artmind_id=frontmatter_id)
        if by_id["path"] == path:
            return Resolution(verdict="reingest", artmind_id=frontmatter_id)
        # Same id, different path: move iff the old path is no longer live.
        if _path_exists(by_id["path"]):
            if fork:
                return Resolution(verdict="new", artmind_id=mint_artmind_id())
            if adopt:
                return Resolution(verdict="move", artmind_id=frontmatter_id, prior_path=by_id["path"])
            raise IdentityConflict(frontmatter_id, by_id["path"], path)
        return Resolution(verdict="move", artmind_id=frontmatter_id, prior_path=by_id["path"])

    by_path = _registry_row_by_path(path)
    if by_path and by_path.get("artmind_id"):
        # Heal is not "the id is unknown" -- it's the opposite: the
        # frontmatter lost its id while the path is still registered.
        return Resolution(verdict="heal", artmind_id=by_path["artmind_id"])
    return Resolution(verdict="new", artmind_id=mint_artmind_id())


# ── versioning ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VersionDecision:
    tier: str  # "content" | "metadata_only"
    version: int
    content_sha256: str


def decide_version(body: str, existing_meta: dict) -> VersionDecision:
    """Compare the incoming body against the file's OWN previously-written
    `_content_sha256` — no registry or graph lookup needed, the file already
    carries its own baseline.

    `metadata_only` covers BOTH "only frontmatter differs" and "nothing
    differs" from the spec's versioning table: writing the (possibly
    byte-identical) frontmatter back and letting git's own diff decide
    whether anything actually changed is simpler and exactly as correct as
    tracking a separate "did frontmatter change" signal would be, since
    artmind's own writes are idempotent (see `build_frontmatter`).
    """
    content_sha256 = compute_content_sha256(body)
    prior_sha = existing_meta.get("_content_sha256")
    prior_version = existing_meta.get("_version")

    if prior_sha is None or content_sha256 != prior_sha:
        new_version = int(prior_version) + 1 if (prior_sha is not None and prior_version) else 1
        return VersionDecision("content", new_version, content_sha256)
    return VersionDecision("metadata_only", int(prior_version) if prior_version else 1, content_sha256)


# ── frontmatter I/O ──────────────────────────────────────────────────────────


def lift_declared_version(body: str) -> str | None:
    """A document's own `| Version | 2.1 |` (or `**Version:** 2.1`) header,
    verbatim — `declared_version` carries no system meaning, so unlike
    `_version` this is never parsed or numerically stripped."""
    from artmind.temporal import _find_header_value

    return _find_header_value(body, ["Version"])


def build_frontmatter(
    existing_meta: dict,
    *,
    artmind_id: str,
    version: int,
    content_sha256: str,
    domain: str,
    status: str = "latest",
    valid_from: str | None = None,
    valid_to: str | None = None,
    valid_time_source: str | None = None,
    source_commit: str | None = None,
    source_path: str,
    source_type: str,
    ingested_at: str,
    body: str | None = None,
) -> dict:
    """Merge the system block onto `existing_meta`.

    Authored fields are seeded via `setdefault` — present once, in the file
    itself, they are never overwritten by any later call. `title` seeds from
    the source filename stem; `created_on` from the ingest timestamp;
    `declared_version` lifts from the body's own "Version" header when `body`
    is given and the document doesn't already declare one.
    """
    out = dict(existing_meta)
    out["_artmind_id"] = artmind_id
    out["_version"] = version
    out["_content_sha256"] = content_sha256
    out["_domain"] = domain
    out["_status"] = status
    if valid_from is not None:
        out["_valid_from"] = valid_from
    if valid_to is not None:
        out["_valid_to"] = valid_to
    if valid_time_source is not None:
        out["_valid_time_source"] = valid_time_source
    if source_commit is not None:
        out["_source_commit"] = source_commit
    out["_source_path"] = source_path
    out["_source_type"] = source_type
    out["_ingested_at"] = ingested_at

    out.setdefault("title", Path(source_path).stem)
    out.setdefault("created_on", ingested_at)
    if body is not None and "declared_version" not in out:
        lifted = lift_declared_version(body)
        if lifted:
            out["declared_version"] = lifted
    return out


def serialize_frontmatter(meta: dict) -> str:
    """Render a frontmatter dict as YAML, system fields first, then authored,
    then anything else verbatim (a human's own custom key must never be
    dropped just because this module doesn't know about it)."""
    known = set(SYSTEM_FIELDS) | set(AUTHORED_FIELDS)
    ordered_keys = (
        [k for k in SYSTEM_FIELDS if k in meta]
        + [k for k in AUTHORED_FIELDS if k in meta]
        + [k for k in meta if k not in known]
    )
    ordered = {k: meta[k] for k in ordered_keys}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


def render_document(meta: dict, body: str) -> str:
    """Reassemble a full markdown file from frontmatter + body, in the exact
    shape `_parse_md_frontmatter` (artmind/ingest.py) parses back apart."""
    return f"---\n{serialize_frontmatter(meta)}---\n\n{body}"


def write_document(path: Path, meta: dict, body: str) -> str:
    text = render_document(meta, body)
    path.write_text(text, encoding="utf-8")
    return text


# ── the one markdown-path resolver ──────────────────────────────────────────


def markdown_path_for(source_type: str, *, vault_path: Path | None = None, stem: str | None = None) -> Path:
    """Where a document's markdown lives, by source type.

    Vault-native (`source_type == "md"`): the vault file itself — Phase 2
    stops copying vault-native markdown into the data dir (Q96; see
    docs/stores-and-repos.md). Binary-derived (pdf/pptx/docx): the docling
    conversion output in the data dir, keyed by filename stem, exactly as
    today. Replaces the four hand-built `MARKDOWNS_DIR / f"{stem}.md"` sites.
    """
    if source_type == "md":
        if vault_path is None:
            raise ValueError("markdown_path_for('md', ...) requires vault_path")
        return vault_path
    if stem is None:
        raise ValueError(f"markdown_path_for({source_type!r}, ...) requires stem")
    return MARKDOWNS_DIR / f"{stem}.md"
