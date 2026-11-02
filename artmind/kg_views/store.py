"""Where saved views live and how they are read, saved and deleted.

Views are re-read from disk on every call (as schema_reference.py does for
schemas), so a warm `serve` daemon never answers from a stale view.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from artmind.atomic_dir import is_scratch, recover, write_dir_atomic
from artmind.cli_conventions import Change, Effect, with_effect
from artmind.kg_views.model import NAME_RE, ViewError, ViewSpec, parse_view

VIEW_FILE = "view.yaml"
SKILL_FILE = "SKILL.md"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*(\r?\n|\Z)", re.S)


@dataclass(frozen=True)
class LoadedView:
    spec: ViewSpec
    skill_md: str
    path: Path


def views_dir() -> Path:
    """`<vault>/.artmind/domains/views` inside a vault (paths.ARTMIND_HOME is
    the vault's `.artmind/` there, == VaultLayout.views_dir), else
    `~/.artmind/domains/views`. Read at call time so tests can repoint it."""
    import paths

    return Path(paths.DOMAIN_VIEWS_DIR)


def check_skill_md(text: str, name: str) -> None:
    """SKILL.md must open with frontmatter whose `name` is the view's name."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ViewError(f"{SKILL_FILE} must start with a --- frontmatter block (name, description)")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ViewError(f"{SKILL_FILE} frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("name") != name:
        raise ViewError(f"{SKILL_FILE} frontmatter name must be {name!r}")
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        raise ViewError(f"{SKILL_FILE} frontmatter needs a description")


def load_view_dir(path: Path) -> LoadedView:
    """Load and validate one view folder (the folder name is the view's name)."""
    path = Path(path)
    yaml_path, skill_path = path / VIEW_FILE, path / SKILL_FILE
    if not yaml_path.is_file():
        raise ViewError(f"{path} has no {VIEW_FILE}")
    if not skill_path.is_file():
        raise ViewError(f"{path} has no {SKILL_FILE}")
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skill_md = skill_path.read_text(encoding="utf-8")
    except yaml.YAMLError as exc:
        raise ViewError(f"{VIEW_FILE} is not valid YAML: {exc}") from exc
    except (UnicodeDecodeError, OSError) as exc:
        raise ViewError(f"{path} could not be read as UTF-8 text: {exc}") from exc
    spec = parse_view(data, path.name)
    check_skill_md(skill_md, spec.name)
    return LoadedView(spec=spec, skill_md=skill_md, path=path)


def check_name(name: str) -> None:
    if NAME_RE.fullmatch(name) is None:
        raise ViewError(f"{name!r} is not a valid view name (lowercase letters, digits, underscores)")


def load_view(name: str) -> LoadedView:
    check_name(name)
    folder = views_dir() / name
    if not folder.is_dir():
        raise ViewError(f"no saved view named {name!r} (see `artmind query views list`)")
    return load_view_dir(folder)


def domain_related(requested: str, view_domain: str) -> bool:
    """True when the two domains are equal or one is an ancestor of the other.

    Pure string logic on the dotted hierarchy; list/show therefore need no
    Neo4j. (`expand_domain_family` is only needed where a concrete list is
    bound as `$domains` -- see runner.py.)"""
    return (
        requested == view_domain
        or requested.startswith(view_domain + ".")
        or view_domain.startswith(requested + ".")
    )


def domain_covered(requested: str, view_domains: tuple[str, ...] | list[str]) -> bool:
    """A requested domain is runnable when it is a view domain or a descendant."""
    return any(requested == v or requested.startswith(v + ".") for v in view_domains)


def view_folders() -> list[Path]:
    """Every non-hidden folder under views_dir(), by name (valid or not)."""
    root = views_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and not is_scratch(p))


def list_views(domains: list[str] | None = None) -> tuple[list[LoadedView], list[dict]]:
    """(valid views, [{name, error}] for folders that fail to load), by name."""
    good: list[LoadedView] = []
    invalid: list[dict] = []
    for folder in view_folders():
        try:
            loaded = load_view_dir(folder)
        except (ViewError, OSError) as exc:
            invalid.append({"name": folder.name, "error": str(exc)})
            continue
        if domains and not any(domain_related(d, v) for d in domains for v in loaded.spec.domains):
            continue
        good.append(loaded)
    return good, invalid


def _travels(root: Path) -> bool:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return bool(vault) and Path(vault).resolve() in root.resolve().parents


def save_view(draft_dir: Path, dry_run: bool = False) -> dict:
    """Validate a draft folder, apply the version guard, copy it into views_dir().

    Version guard: if a view of that name exists and the fingerprint (cypher +
    params + presentation) differs, the draft's `version` must be greater.
    There is deliberately no force flag -- bump the version. A dry run runs every
    check and reports the `effect`, but writes nothing.
    """
    try:
        draft = load_view_dir(Path(draft_dir))
    except (OSError, UnicodeDecodeError) as exc:
        raise ViewError(f"cannot read draft {draft_dir}: {exc}") from exc
    name = draft.spec.name
    root = views_dir()
    target = root / name
    recover(target)  # heal a save a crash interrupted, so the guard sees the real saved view
    replaced = False
    if target.is_dir():
        replaced = True
        try:
            existing = load_view_dir(target)
        except (ViewError, OSError):
            existing = None  # a broken saved view cannot be compared; allow the fix
        if existing is not None and draft.spec.version < existing.spec.version:
            raise ViewError(
                f"view {name!r}: saving would downgrade version "
                f"{existing.spec.version} -> {draft.spec.version}"
            )
        if (
            existing is not None
            and existing.spec.fingerprint() != draft.spec.fingerprint()
            and draft.spec.version <= existing.spec.version
        ):
            raise ViewError(
                f"view {name!r} changed (cypher, params or presentation) but version "
                f"{draft.spec.version} is not greater than the saved version "
                f"{existing.spec.version}; bump `version` in {VIEW_FILE}"
            )
    effect = Effect(
        summary=f"{'replace' if replaced else 'save'} view {name!r} at version {draft.spec.version}",
        changes=[Change("view_save", 1, [name])],
        stale_views=[name] if replaced else [],
        reversible=not replaced,
        undo=None if replaced else f"artmind views delete {name}",
        travels=_travels(root),
        records=[str(target)],
    )
    result = {"saved": name, "version": draft.spec.version, "path": str(target), "replaced": replaced}
    if dry_run:
        return with_effect(result, effect, dry_run=True)
    try:
        write_dir_atomic(
            target,
            {
                VIEW_FILE: (draft.path / VIEW_FILE).read_bytes(),
                SKILL_FILE: (draft.path / SKILL_FILE).read_bytes(),
            },
            carry_over=False,
        )
    except OSError as exc:
        raise ViewError(f"could not save view {name!r}: {exc}") from exc
    return with_effect(result, effect)


def delete_view(name: str, dry_run: bool = False) -> dict:
    check_name(name)
    target = views_dir() / name
    if not target.is_dir():
        raise ViewError(f"no saved view named {name!r}")
    effect = Effect(
        summary=f"delete view {name!r}",
        changes=[Change("view_delete", 1, [name])],
        stale_views=[name],
        travels=_travels(target.parent),
        records=[str(target)],
    )
    result = {"deleted": name, "path": str(target)}
    if dry_run:
        return with_effect(result, effect, dry_run=True)
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise ViewError(f"could not delete view {name!r}: {exc}") from exc
    return with_effect(result, effect)
