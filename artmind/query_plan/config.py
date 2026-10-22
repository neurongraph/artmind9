"""Per-domain plan config: <ARTMIND_HOME>/domains/plan/<domain>.yaml (spec 2026-10-22 §4.5).

User data like views and insights tuning: never seeded, outside schemas/ so it
never moves schema_set_hash(), read on every call. A parent domain's file
applies to every child domain that has no file of its own (most specific wins,
never merged).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FIRST_PERSON = ("i", "me", "my", "mine", "myself")
DEFAULT_THRESHOLD = 0.7
CLASSIFIERS = ("auto", "lexical", "decision")
_KEYS = frozenset({"narrator", "first_person", "confidence_threshold", "classifier", "decision_model"})
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*$")


class PlanConfigError(ValueError):
    """A plan config file breaks a rule of spec §4.5."""


@dataclass(frozen=True)
class Narrator:
    entity_class: str
    name: str


@dataclass(frozen=True)
class PlanConfig:
    narrator: Narrator | None = None
    first_person: tuple[str, ...] = DEFAULT_FIRST_PERSON
    confidence_threshold: float = DEFAULT_THRESHOLD
    classifier: str = "auto"
    decision_model: str | None = None
    source: str | None = None


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_PLAN_DIR)


def config_file(domain: str) -> Path | None:
    """The most specific file for `domain`: its own, else its nearest parent's."""
    if not isinstance(domain, str) or not _DOMAIN_RE.match(domain):
        return None
    parts = domain.split(".")
    for i in range(len(parts), 0, -1):
        path = config_dir() / f"{'.'.join(parts[:i])}.yaml"
        if path.is_file():
            return path
    return None


def parse_config(data, source: str) -> PlanConfig:
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise PlanConfigError(f"{source}: must be a mapping")
    unknown = set(data) - _KEYS
    if unknown:
        raise PlanConfigError(f"{source}: unknown key(s) {sorted(unknown)}")
    narrator = None
    if data.get("narrator") is not None:
        raw = data["narrator"]
        if (not isinstance(raw, dict) or set(raw) != {"entity_class", "name"}
                or not all(isinstance(raw[k], str) and raw[k].strip() for k in raw)):
            raise PlanConfigError(f"{source}: narrator must be {{entity_class, name}} (two strings)")
        narrator = Narrator(raw["entity_class"].strip().upper(), raw["name"].strip())
    first_person = DEFAULT_FIRST_PERSON
    if "first_person" in data:
        raw = data["first_person"]
        if not isinstance(raw, list) or not raw or not all(isinstance(w, str) and w.strip() for w in raw):
            raise PlanConfigError(f"{source}: first_person must be a non-empty list of words")
        first_person = tuple(w.strip().casefold() for w in raw)
    threshold = data.get("confidence_threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise PlanConfigError(f"{source}: confidence_threshold must be a number in (0, 1]")
    classifier = data.get("classifier", "auto")
    if classifier not in CLASSIFIERS:
        raise PlanConfigError(f"{source}: classifier must be one of {list(CLASSIFIERS)}")
    model = data.get("decision_model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise PlanConfigError(f"{source}: decision_model must be a non-empty string")
    return PlanConfig(narrator, first_person, float(threshold), classifier, model.strip() if model else None, source)


def load_config(domain: str) -> PlanConfig:
    path = config_file(domain)
    if path is None:
        return PlanConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PlanConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(data, str(path))


def merge(configs: list[PlanConfig]) -> PlanConfig:
    """Several selected domains: the most cautious threshold, the first narrator,
    first-person list and model found, and the classifier only if they agree."""
    if not configs:
        return PlanConfig()
    if len(configs) == 1:
        return configs[0]
    classifiers = {c.classifier for c in configs}
    return PlanConfig(
        narrator=next((c.narrator for c in configs if c.narrator), None),
        first_person=next((c.first_person for c in configs if c.first_person != DEFAULT_FIRST_PERSON),
                          DEFAULT_FIRST_PERSON),
        confidence_threshold=max(c.confidence_threshold for c in configs),
        classifier=classifiers.pop() if len(classifiers) == 1 else "auto",
        decision_model=next((c.decision_model for c in configs if c.decision_model), None),
    )
