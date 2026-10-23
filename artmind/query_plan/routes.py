"""The routing data `artmind query plan` reads (spec 2026-10-22 §4.3).

`routes.yaml` is the one place routing lives: the planner fills its step
templates, and the test suite checks every command and option a template
names against the Click tree. A route's `description` is the text a decision
model reads for that option, so it describes the question shape.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROUTES_FILE = Path(__file__).with_name("routes.yaml")
RESERVED_IDS = frozenset({"view"})
NEEDS = frozenset({"entity", "two_entities", "document", "date", "class", "store", "insights_snapshot"})
PLACEHOLDERS = frozenset({
    "domains", "entity_id", "entity_name", "other_entity_id", "entity_class", "other_entity_class",
    "document", "class", "question", "keywords",
})
STORE_KINDS = frozenset({"graph", "analytical", "hybrid", "records_plus_guidance"})
TRIGGER_SLOTS = frozenset({"date", "year", "entity", "document"})
FALLBACK_WHEN = frozenset({"zero_rows", "truncated", "no_document", "needs_disambiguation", "error"})
MAX_DESCRIPTION = 300

_KEYS = frozenset({"id", "description", "triggers", "needs", "steps", "optional_steps", "variants", "fallbacks", "read", "evidence"})
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EVIDENCE_RE = re.compile(r"^[SP]\d+$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class RouteError(ValueError):
    """routes.yaml breaks a rule of spec §4.3."""


@dataclass(frozen=True)
class Fallback:
    when: str
    cmd: str


@dataclass(frozen=True)
class Route:
    id: str
    description: str
    triggers: tuple[str, ...]
    needs: tuple[str, ...]
    steps: tuple[str, ...]
    fallbacks: tuple[Fallback, ...]
    read: tuple[str, ...]
    evidence: tuple[str, ...]
    variants: dict = field(default_factory=dict)  # store kind -> step templates, replacing `steps`
    optional_steps: tuple[str, ...] = ()  # rendered after the steps when every placeholder fills

    def steps_for(self, store_kind: str | None) -> tuple[str, ...]:
        return self.variants.get(store_kind, self.steps) if store_kind else self.steps


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def _has_stray_brace(text: str) -> bool:
    return "{" in text or "}" in text


def _strings(value, where: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    value = [] if value is None else value
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RouteError(f"{where} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise RouteError(f"{where} must not be empty")
    return tuple(v.strip() for v in value)


def _template(cmd, where: str) -> str:
    if not isinstance(cmd, str) or not cmd.startswith("artmind "):
        raise RouteError(f"{where} must be a command starting with 'artmind '")
    unknown = placeholders(cmd) - PLACEHOLDERS
    if unknown:
        raise RouteError(f"{where} uses unknown placeholder(s) {sorted(unknown)}")
    if _has_stray_brace(_PLACEHOLDER_RE.sub("", cmd)):
        raise RouteError(f"{where} has a stray brace in {cmd!r}")
    if "--compact" not in cmd.split():
        raise RouteError(f"{where} must pass --compact")
    return cmd


def parse_routes(data) -> tuple[Route, ...]:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("routes"), list):
        raise RouteError("routes.yaml must be a mapping with `version: 1` and a `routes` list")
    out: list[Route] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["routes"]):
        where = f"routes[{i}]"
        if not isinstance(raw, dict):
            raise RouteError(f"{where} must be a mapping")
        extra = set(raw) - _KEYS
        if extra:
            raise RouteError(f"{where} has unknown key(s) {sorted(extra)}")
        rid = raw.get("id")
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            raise RouteError(f"{where}.id must be snake_case")
        if rid in RESERVED_IDS:
            raise RouteError(f"route id {rid!r} is reserved")
        if rid in seen:
            raise RouteError(f"duplicate route id {rid!r}")
        seen.add(rid)
        where = f"route {rid}"
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > MAX_DESCRIPTION:
            raise RouteError(f"{where}: description must be 1-{MAX_DESCRIPTION} characters")
        triggers = _strings(raw.get("triggers"), f"{where}.triggers")
        for trigger in triggers:
            bad = placeholders(trigger) - TRIGGER_SLOTS
            if bad:
                raise RouteError(f"{where}: trigger {trigger!r} uses unknown slot(s) {sorted(bad)}")
            if _has_stray_brace(_PLACEHOLDER_RE.sub("", trigger)):
                raise RouteError(f"{where}: trigger {trigger!r} has a stray brace")
        needs = _strings(raw.get("needs"), f"{where}.needs")
        if set(needs) - NEEDS:
            raise RouteError(f"{where}: unknown needs {sorted(set(needs) - NEEDS)}")
        steps = tuple(
            _template(cmd, f"{where}.steps[{j}]")
            for j, cmd in enumerate(_strings(raw.get("steps"), f"{where}.steps", allow_empty=False))
        )
        optional_steps = tuple(
            _template(cmd, f"{where}.optional_steps[{j}]")
            for j, cmd in enumerate(_strings(raw.get("optional_steps"), f"{where}.optional_steps"))
        )
        variants = {}
        raw_variants = raw.get("variants")
        if raw_variants is not None:
            if not isinstance(raw_variants, dict):
                raise RouteError(f"{where}.variants must be a mapping of store kind to steps")
            for kind, templates in raw_variants.items():
                if kind not in STORE_KINDS:
                    raise RouteError(f"{where}.variants has unknown store kind {kind!r}; use {sorted(STORE_KINDS)}")
                variants[kind] = tuple(
                    _template(cmd, f"{where}.variants.{kind}[{j}]")
                    for j, cmd in enumerate(_strings(templates, f"{where}.variants.{kind}", allow_empty=False))
                )
        fallbacks = []
        for j, fb in enumerate(raw.get("fallbacks") or []):
            if not isinstance(fb, dict) or set(fb) != {"when", "cmd"} or not isinstance(fb["when"], str) or fb["when"] not in FALLBACK_WHEN:
                raise RouteError(f"{where}.fallbacks[{j}] must be {{when, cmd}} with when in {sorted(FALLBACK_WHEN)}")
            fallbacks.append(Fallback(fb["when"], _template(fb["cmd"], f"{where}.fallbacks[{j}]")))
        read = _strings(raw.get("read"), f"{where}.read")
        evidence = _strings(raw.get("evidence"), f"{where}.evidence", allow_empty=False)
        bad_evidence = [e for e in evidence if not _EVIDENCE_RE.match(e)]
        if bad_evidence:
            raise RouteError(f"{where}: evidence ids must look like S<n> or P<n>, got {bad_evidence}")
        out.append(Route(rid, description.strip(), triggers, needs, steps, tuple(fallbacks), read, evidence, variants, optional_steps))
    if not out:
        raise RouteError("routes.yaml defines no routes")
    return tuple(out)


@lru_cache(maxsize=1)
def load_routes() -> tuple[Route, ...]:
    try:
        data = yaml.safe_load(ROUTES_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RouteError(f"routes.yaml is not valid YAML: {exc}") from exc
    return parse_routes(data)


def render(template: str, context: dict) -> str | None:
    """Fill a template's placeholders, shell-quoted; None when any value is missing."""
    missing = False

    def substitute(match: re.Match) -> str:
        nonlocal missing
        value = context.get(match.group(1))
        # Blank values are missing; a leading "-" would be parsed by Click as an option.
        if value is None or not str(value).strip() or str(value).startswith("-"):
            missing = True
            return ""
        return shlex.quote(str(value))

    text = _PLACEHOLDER_RE.sub(substitute, template)
    return None if missing else text
