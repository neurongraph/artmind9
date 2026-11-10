"""Where topic plans live and how they are read and saved (spec 2026-11-10 §5.1).

The same rules as saved views (`kg_views/store.py`): one folder per plan under
`<ARTMIND_HOME>/domains/topic_plans/<slug>/{plan.yaml,SKILL.md}`, read from disk on
every call (a warm `serve` never answers from a stale plan), never seeded, saved
atomically (`atomic_dir.write_dir_atomic`).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from artmind.atomic_dir import is_scratch, recover, write_dir_atomic
from artmind.topics import plan as model
from artmind.topics.plan import PlanError, PlanSpec

PLAN_FILE = "plan.yaml"
SKILL_FILE = "SKILL.md"


@dataclass(frozen=True)
class LoadedPlan:
    spec: PlanSpec
    skill_md: str
    path: Path


def plans_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_TOPIC_PLANS_DIR)


def check_slug(slug: str) -> None:
    if model.SLUG_RE.fullmatch(str(slug or "")) is None:
        raise PlanError(f"{slug!r} is not a valid plan slug (lowercase letters, digits, hyphens)")


def load_plan_dir(path: Path, *, guard: bool = True) -> LoadedPlan:
    path = Path(path)
    yaml_path, skill_path = path / PLAN_FILE, path / SKILL_FILE
    if not yaml_path.is_file():
        raise PlanError(f"{path} has no {PLAN_FILE}")
    if not skill_path.is_file():
        raise PlanError(f"{path} has no {SKILL_FILE}")
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        skill_md = skill_path.read_text(encoding="utf-8")
    except yaml.YAMLError as exc:
        raise PlanError(f"{PLAN_FILE} is not valid YAML: {exc}") from exc
    except (UnicodeDecodeError, OSError) as exc:
        raise PlanError(f"{path} could not be read as UTF-8 text: {exc}") from exc
    spec = model.parse_plan(data, path.name)
    model.check_skill_md(skill_md, spec.slug)
    if guard:
        model.check_version(spec, skill_md)
    return LoadedPlan(spec, skill_md, path)


def load_plan(slug: str) -> LoadedPlan:
    check_slug(slug)
    folder = plans_dir() / slug
    if not folder.is_dir():
        raise PlanError(f"no topic plan named {slug!r} (see `artmind query topics plan --list`)")
    return load_plan_dir(folder)


def plan_folders() -> list[Path]:
    root = plans_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and not is_scratch(p))


def _related(a: str, b: str) -> bool:
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def list_plans(domains: list[str] | None = None) -> tuple[list[LoadedPlan], list[dict]]:
    """(valid plans in scope, [{slug, error}] for folders that fail to load), by slug."""
    good, invalid = [], []
    for folder in plan_folders():
        try:
            loaded = load_plan_dir(folder)
        except (PlanError, OSError) as exc:
            invalid.append({"slug": folder.name, "error": str(exc)})
            continue
        if domains and not any(_related(d, p) for d in domains for p in loaded.spec.domains):
            continue
        good.append(loaded)
    return good, invalid


def existing(slug: str) -> LoadedPlan | None:
    """The saved plan, read without the version guard (a writer compares against it)."""
    folder = plans_dir() / slug
    recover(folder)
    if not folder.is_dir():
        return None
    try:
        return load_plan_dir(folder, guard=False)
    except (PlanError, OSError):
        return None


def next_version(spec: PlanSpec, old: LoadedPlan | None, skill_md: str) -> int:
    """The version a writer saves: the old one when nothing in the definition or SKILL.md
    changed, else one above it."""
    if old is None:
        return max(1, spec.version)
    same = (replace(spec, version=old.spec.version).definition() == old.spec.definition()
            and skill_md == old.skill_md)
    return old.spec.version if same else old.spec.version + 1


def write_plan(spec: PlanSpec, skill_md: str) -> Path:
    """Write both files atomically (the caller has set version and built)."""
    model.check_skill_md(skill_md, spec.slug)
    target = plans_dir() / spec.slug
    target.parent.mkdir(parents=True, exist_ok=True)
    recover(target)
    text = yaml.safe_dump(spec.to_yaml_dict(), sort_keys=False, allow_unicode=True, width=10_000)
    write_dir_atomic(target, {PLAN_FILE: text.encode("utf-8"), SKILL_FILE: skill_md.encode("utf-8")},
                     carry_over=False)
    return target


def travels() -> bool:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return bool(vault) and Path(vault).resolve() in plans_dir().resolve().parents
