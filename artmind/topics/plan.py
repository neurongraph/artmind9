"""The topic plan contract (spec 2026-11-10 §5.1): `plan.yaml` + `SKILL.md`, shaped like a
saved view. Pure: no Neo4j, no filesystem.

A plan is the retrieval program behind a topic page (subjects × slices, depth per cell),
run live by `artmind query topics run`. It holds no corpus text, but its subjects are
the user's words, so a plan stays in the vault.

The version guard works on the files themselves: every build records the plan's
`version` and `fingerprint` (definition + SKILL.md) in `built`. A plan whose files
changed while `version` stayed at `built.version` is refused, and so is a `version`
below `built.version`, the same rule `views save` applies to a view.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

FORMAT = 1
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
BY = ("year", "month", "none")
SPLIT = ("domain", "none")
REFRESH = ("manual", "on_stale", "weekly")
MAX_SUBJECTS = 8
MAX_PER_CELL = 50
DEFAULT_PER_CELL = 15
DEFAULT_MAX_DOCS = 150
MAX_DOCS_CAP = 300

_TOP_KEYS = {"format", "slug", "topic", "version", "domains", "subjects", "slices", "per_cell", "max_docs",
             "built", "refresh"}
#: What a version bump covers: everything but the `built` record.
_DEFINITION = ("format", "slug", "topic", "domains", "subjects", "slices", "per_cell", "max_docs", "refresh")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*(\r?\n|\Z)", re.S)


class PlanError(ValueError):
    """A plan breaks the contract."""


@dataclass(frozen=True)
class PlanSpec:
    slug: str
    topic: str
    version: int
    domains: tuple[str, ...]
    subjects: tuple[str, ...]
    by: str = "none"
    split: str = "none"
    per_cell: int = DEFAULT_PER_CELL
    max_docs: int = DEFAULT_MAX_DOCS
    refresh: str = "manual"
    built: dict = field(default_factory=dict, compare=False)

    def definition(self) -> dict:
        return {"format": FORMAT, "slug": self.slug, "topic": self.topic, "domains": list(self.domains),
                "subjects": list(self.subjects), "slices": {"by": self.by, "split": self.split},
                "per_cell": self.per_cell, "max_docs": self.max_docs, "refresh": self.refresh}

    def to_yaml_dict(self) -> dict:
        """plan.yaml's mapping, keys in the documented order."""
        d = self.definition()
        out = {"format": d["format"], "slug": d["slug"], "topic": d["topic"], "version": self.version,
               "domains": d["domains"], "subjects": d["subjects"], "slices": d["slices"],
               "per_cell": d["per_cell"], "max_docs": d["max_docs"]}
        if self.built:
            out["built"] = dict(self.built)
        out["refresh"] = self.refresh
        return out

    def summary(self) -> dict:
        return {"slug": self.slug, "topic": self.topic, "version": self.version, "domains": list(self.domains),
                "subjects": [{"n": i, "text": s} for i, s in enumerate(self.subjects, 1)],
                "slices": {"by": self.by, "split": self.split}, "per_cell": self.per_cell,
                "max_docs": self.max_docs, "refresh": self.refresh,
                "built_at": (self.built or {}).get("at")}


def fingerprint(spec: PlanSpec, skill_md: str) -> str:
    text = json.dumps(spec.definition(), sort_keys=True, ensure_ascii=False) + "\n" + skill_md
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _int(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise PlanError(f"{key} must be an integer between {lo} and {hi}")
    return value


def _strings(value, key: str, limit: int | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PlanError(f"{key} must be a non-empty list")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PlanError(f"every entry of {key} must be a non-empty string")
        out.append(" ".join(item.split()))
    if limit is not None and len(out) > limit:
        raise PlanError(f"{key} holds at most {limit} entries")
    if len(set(out)) != len(out):
        raise PlanError(f"{key} has a duplicate entry")
    return tuple(out)


def parse_plan(data, folder_name: str | None = None) -> PlanSpec:
    if not isinstance(data, dict):
        raise PlanError("plan.yaml must be a mapping")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise PlanError(f"plan.yaml has unknown key(s): {', '.join(unknown)}")
    if data.get("format") != FORMAT:
        raise PlanError(f"format must be {FORMAT}")
    slug = data.get("slug")
    if not isinstance(slug, str) or SLUG_RE.fullmatch(slug) is None:
        raise PlanError("slug must be lowercase letters, digits and hyphens (at most 80)")
    if folder_name is not None and slug != folder_name:
        raise PlanError(f"slug {slug!r} must match the folder name {folder_name!r}")
    topic = data.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise PlanError("topic must be a non-empty string")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PlanError("version must be an integer of at least 1")
    slices = data.get("slices") or {}
    if not isinstance(slices, dict) or set(slices) - {"by", "split"}:
        raise PlanError("slices must be a mapping with `by` and `split`")
    by, split = slices.get("by", "none"), slices.get("split", "none")
    by = "none" if by is None else by
    split = "none" if split is None else split
    if by not in BY:
        raise PlanError(f"slices.by must be one of {', '.join(BY)} (resolved; `auto` is for `topic build`)")
    if split not in SPLIT:
        raise PlanError(f"slices.split must be one of {', '.join(SPLIT)}")
    refresh = data.get("refresh", "manual")
    if refresh not in REFRESH:
        raise PlanError(f"refresh must be one of {', '.join(REFRESH)}")
    built = data.get("built") or {}
    if not isinstance(built, dict):
        raise PlanError("built must be a mapping")
    return PlanSpec(
        slug=slug, topic=" ".join(topic.split()), version=version,
        domains=_strings(data.get("domains"), "domains"),
        subjects=_strings(data.get("subjects"), "subjects", MAX_SUBJECTS),
        by=by, split=split,
        per_cell=_int(data, "per_cell", DEFAULT_PER_CELL, 1, MAX_PER_CELL),
        max_docs=_int(data, "max_docs", DEFAULT_MAX_DOCS, 1, MAX_DOCS_CAP),
        refresh=refresh, built=built,
    )


def check_version(spec: PlanSpec, skill_md: str) -> None:
    """The view rule on a plan's own files: no change without a bump, no downgrade."""
    built = spec.built or {}
    built_version, built_fp = built.get("version"), built.get("fingerprint")
    if not isinstance(built_version, int):
        return
    if spec.version < built_version:
        raise PlanError(f"plan {spec.slug!r}: version {spec.version} is below the built version "
                        f"{built_version} (a downgrade); restore `version: {built_version}` or higher")
    if spec.version == built_version and built_fp and fingerprint(spec, skill_md) != built_fp:
        raise PlanError(f"plan {spec.slug!r} changed (plan.yaml or SKILL.md) but version {spec.version} "
                        f"was not bumped; set `version: {spec.version + 1}` in plan.yaml")


def check_skill_md(text: str, slug: str) -> None:
    import yaml

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise PlanError("SKILL.md must start with a --- frontmatter block (name, description)")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PlanError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("name") != slug:
        raise PlanError(f"SKILL.md frontmatter name must be {slug!r}")
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        raise PlanError("SKILL.md frontmatter needs a description")


def _yaml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def default_skill_md(spec: PlanSpec) -> str:
    """The first SKILL.md `topic build` writes (no LLM); the operator edits it."""
    slices = []
    if spec.by != "none":
        slices.append(f"one cell per {spec.by} of the document date")
    if spec.split == "domain":
        slices.append("one cell per child domain")
    sliced = "; ".join(slices) or "no slices (one cell per subject)"
    subjects = "\n".join(f"{i}. {s} (`--subject {i}`)" for i, s in enumerate(spec.subjects, 1))
    description = f"Topic plan for {spec.topic!r} over {', '.join(spec.domains)}: run it for overview or trend questions."
    return "\n".join([
        "---",
        f"name: {spec.slug}",
        f"description: {_yaml_str(description)}",
        "---",
        f"# {spec.topic}",
        "",
        f"Run `artmind query topics run {spec.slug} --shape brief --compact` to read what the corpus says about",
        "this topic, gathered per subject and cell. Each row is ordinary survey evidence: cite its passage.",
        "",
        "## Subjects",
        "",
        subjects,
        "",
        "## Slices",
        "",
        f"{sliced}; at most {spec.per_cell} documents per cell and {spec.max_docs} in all.",
        "",
        "## When it applies",
        "",
        f"Overview, trend or \"across the corpus\" questions about {spec.topic}. Run one subject",
        "(`--subject N`) or one cell (`--cell <period|domain>`) when the question needs only part.",
        "",
        "## What it does not cover",
        "",
        "Documents that match none of the subjects' words may be missed; a cell marked `partial` has more",
        "matching documents than it shows. Edit this file to say more (bump `version` in plan.yaml).",
        "",
    ])
