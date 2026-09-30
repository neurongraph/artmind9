"""`artmind ingest pending` (spec 2026-09-30 §6 X2): the mapped files an
ingest of this vault would do work on. Read-only, no LLM.

"Work" is what costs something: extracting a note, converting a binary,
loading a table. Every decision is the ingest's own, reused rather than
re-implemented -- `ingest.plan_vault_native` (body hash against the staged
baseline, a `_domain` change), `ingest.plan_binary` (source hash against the
staged baseline) and `structured.pipeline.is_unchanged` (file hash against
the registry) -- so a file listed here is exactly a file `ingest async
--pending` (X3) would extract, convert or load, and one left out is one it
would skip. A note that only moved (`metadata_only`) is not work.

The files considered are the ones `ingest async <vault>` walks: supported
types, no dot-folders, no `_Inbox`, and only paths a `.artmind/vault.yaml`
mapping covers. A file's domain is its mapping's, falling back to the
worker's own default (`general`) exactly as a job with no `--domain` does.
"""
from __future__ import annotations

from pathlib import Path

#: The domain `worker.py` gives a file in a job submitted without `--domain`
#: that no mapping covers (`row[1] or "general"`).
DEFAULT_DOMAIN = "general"

KINDS = ("notes", "binaries", "tables")


def _classify(path: Path, domain: str) -> tuple[str, str | None]:
    """`(kind, reason)` for one file: `reason` is `new`, `changed`, or None
    when an ingest would do no work on it. Raises whatever the ingest's own
    decision raises for a file it could not ingest."""
    from artmind import ingest
    from artmind.structured import is_structured_source

    if is_structured_source(path):
        from artmind.structured.pipeline import is_unchanged, registered_tables

        _, rows = registered_tables(path, domain)
        if is_unchanged(rows, ingest._compute_sha256(path)):
            return "tables", None
        return "tables", "changed" if any(rows) else "new"
    if ingest._is_vault_native_markdown(path):
        plan = ingest.plan_vault_native(path, domain=domain)
        if plan.tier != "content":
            return "notes", None
        return "notes", "new" if plan.is_new else "changed"
    plan = ingest.plan_binary(path, domain)
    if plan.unchanged:
        return "binaries", None
    return "binaries", "new" if plan.is_new else "changed"


def pending(vault_root: Path) -> dict:
    """`{"notes": [...], "binaries": [...], "tables": [...], "errors": [...]}`.

    Each of the first three lists `{"path", "reason"}` in walk order, with
    `path` vault-relative (posix) and `reason` `new` or `changed`. `errors`
    lists `{"path", "error"}` for a mapped file the ingest could not ingest
    as it stands (an `_artmind_id` collision, an unreadable workbook, a
    `_domain` that is not a folder name): not work, but worth showing.
    Raises `manifest.ManifestError` for a broken `.artmind/vault.yaml`."""
    from artmind.ingest import collect_ingest_files
    from artmind.manifest import filter_for_ingest

    root = Path(vault_root).resolve()
    manifest, files, _ = filter_for_ingest(root, root, collect_ingest_files(root))
    report: dict = {kind: [] for kind in KINDS}
    report["errors"] = []
    for path in files:
        rel = path.resolve().relative_to(root).as_posix()
        domain = manifest.domain_for(rel) or DEFAULT_DOMAIN
        try:
            kind, reason = _classify(path, domain)
        except Exception as e:  # the ingest would fail on this file, not work on it
            report["errors"].append({"path": rel, "error": str(e)})
            continue
        if reason is not None:
            report[kind].append({"path": rel, "reason": reason})
    return report


def pending_files(vault_root: Path, report: dict | None = None) -> list[Path]:
    """Absolute paths of every file `pending` lists as work, in walk order --
    what `ingest async --pending` (X3) enqueues."""
    root = Path(vault_root).resolve()
    report = pending(root) if report is None else report
    return sorted(root / entry["path"] for kind in KINDS for entry in report[kind])
