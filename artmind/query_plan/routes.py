"""The routing data `artmind query plan` reads (spec 2026-10-22 §4.3).

`routes.yaml` is the one place routing lives: the planner fills its step
templates, and the test suite checks every command and option a template
names against the Click tree. A route's `description` is the text a decision
model reads for that option, so it describes the question shape.

Version 2 (spec 2026-11-05 §5) adds `program:` / `guards:` (see `program.py`).
Every route, v1 or v2, exposes `Route.program` and `Route.guards`; a v1 route is
read as a program of sequential `run` nodes with its fallbacks as guards. The v1
fields (`steps`, `fallbacks`, `variants`, `optional_steps`, `steps_for`) are
unchanged, and a program route's `steps` is its run nodes flattened in order.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from artmind.query_plan import program as program_mod
from artmind.query_plan.program import Guard, Node, ProgramChoice, ProgramError

ROUTES_FILE = Path(__file__).with_name("routes.yaml")
RESERVED_IDS = frozenset({"view"})
NEEDS = frozenset({"entity", "two_entities", "document", "date", "class", "store", "insights_snapshot"})
#: `{domains}` is the selected set, comma-joined (every `--domain` here is repeatable and comma-split);
#: `{domain}` is one selected domain, for a command that takes exactly one (`query insights list`):
#: the planner renders such a template once per selected domain.
PLACEHOLDERS = frozenset({
    "domains", "store_domains", "domain", "entity_id", "entity_name", "other_entity_id", "entity_class", "other_entity_class",
    "document", "class", "question", "keywords",
})
#: Placeholders only a v2 `program:` template may use; the v1 planner does not supply them.
V2_PLACEHOLDERS = frozenset({"search_term", "date_window", "entity_class_list"})
ALL_PLACEHOLDERS = PLACEHOLDERS | V2_PLACEHOLDERS
SUPPORTED_VERSIONS = (1, 2)
STORE_KINDS = frozenset({"graph", "analytical", "hybrid", "records_plus_guidance"})
TRIGGER_SLOTS = frozenset({"date", "year", "entity", "document"})
FALLBACK_WHEN = frozenset({"zero_rows", "truncated", "no_document", "needs_disambiguation", "error"})
MAX_DESCRIPTION = 300

_KEYS = frozenset({"id", "description", "triggers", "needs", "steps", "optional_steps", "variants", "fallbacks", "read", "evidence", "program", "programs", "kind", "guards"})
_CHOICE_KEYS = frozenset({"kind", "when", "program", "guards"})
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EVIDENCE_RE = re.compile(r"^[SP]\d+$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*\.[a-z_]+|[a-z_]+)\}")


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
    program: tuple[Node, ...] = ()  # every route: authored (v2) or converted from steps (v1)
    guards: tuple[Guard, ...] = ()
    authored_program: bool = False  # True when routes.yaml wrote `program:` or `programs:` itself
    kind: str = "sequence"  # program kind of the default choice (v1 routes: sequence)
    programs: tuple[ProgramChoice, ...] = ()  # every route: one or more choices; the last is the default
    chooses: bool = False  # True when routes.yaml wrote `programs:` (the planner picks one at plan time)

    def steps_for(self, store_kind: str | None) -> tuple[str, ...]:
        return self.variants.get(store_kind, self.steps) if store_kind else self.steps


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def bound_placeholders(template: str) -> set[tuple[str, str]]:
    """`{node.output}` placeholders (step bindings, P3 §4): filled by the executor, never by the planner."""
    return {tuple(p.split(".", 1)) for p in placeholders(template) if "." in p}


def _has_stray_brace(text: str) -> bool:
    return "{" in text or "}" in text


def _strings(value, where: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    value = [] if value is None else value
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RouteError(f"{where} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise RouteError(f"{where} must not be empty")
    return tuple(v.strip() for v in value)


def _template(cmd, where: str, allowed: frozenset = PLACEHOLDERS, *, bound: bool = False) -> str:
    if not isinstance(cmd, str) or not cmd.startswith("artmind "):
        raise RouteError(f"{where} must be a command starting with 'artmind '")
    unknown = {p for p in placeholders(cmd) if bound is False or "." not in p} - allowed
    if unknown:
        raise RouteError(f"{where} uses unknown placeholder(s) {sorted(unknown)}")
    if _has_stray_brace(_PLACEHOLDER_RE.sub("", cmd)):
        raise RouteError(f"{where} has a stray brace in {cmd!r}")
    if "--compact" not in cmd.split():
        raise RouteError(f"{where} must pass --compact")
    return cmd


def _kind(value, where: str) -> str:
    if value not in program_mod.PROGRAM_KINDS or value == "sequence":
        raise ProgramError(f"{where} must be one of {[k for k in program_mod.PROGRAM_KINDS if k != 'sequence']}")
    return value


def _parse_program(raw, where: str):
    return program_mod.parse_program(raw, where, lambda cmd, w, extra: _template(cmd, w, ALL_PLACEHOLDERS | extra, bound=True))


def _parse_choices(raw, where: str) -> tuple[ProgramChoice, ...]:
    """`programs:` = [{kind, when?, program, guards?}, ...]; only the last entry may omit `when`."""
    if not isinstance(raw, list) or not raw:
        raise ProgramError(f"{where}.programs must be a non-empty list")
    out = []
    for i, entry in enumerate(raw):
        loc = f"{where}.programs[{i}]"
        if not isinstance(entry, dict) or set(entry) - _CHOICE_KEYS or not {"kind", "program"} <= set(entry):
            raise ProgramError(f"{loc} must be {{kind, program[, when][, guards]}}")
        last = i == len(raw) - 1
        if "when" not in entry and not last:
            raise ProgramError(f"{loc}: only the last entry may omit `when` (it is the default)")
        when = program_mod.parse_when(entry["when"], f"{loc}.when") if "when" in entry else None
        program = _parse_program(entry["program"], f"{loc}.program")
        guards = program_mod.parse_guards(entry.get("guards"), program, f"{loc}.guards")
        out.append(ProgramChoice(_kind(entry["kind"], f"{loc}.kind"), when, program, guards))
    return tuple(out)


def parse_routes(data) -> tuple[Route, ...]:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] not in SUPPORTED_VERSIONS or not isinstance(data.get("routes"), list):
        raise RouteError("routes.yaml must be a mapping with `version: 1` or `version: 2` and a `routes` list")
    version = data["version"]
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
        forms = [k for k in ("steps", "program", "programs") if k in raw]
        if len(forms) > 1:
            raise RouteError(f"{where}: give exactly one of `steps:`, `program:`, `programs:` (found {forms})")
        chooses = "programs" in raw
        has_program = "program" in raw or chooses
        if has_program:
            if version < 2:
                raise RouteError(f"{where}: `{forms[0]}:` needs `version: 2`")
            clash = [k for k in ("fallbacks", "variants", "optional_steps") if k in raw]
            if clash:
                raise RouteError(f"{where}: give `{forms[0]}:` or `steps:`/`fallbacks:`, not both (found {clash})")
        elif "guards" in raw:
            raise RouteError(f"{where}: `guards:` only goes with `program:`")
        if "kind" in raw and "program" not in raw:
            raise RouteError(f"{where}: `kind:` only goes with a single `program:` (`programs:` entries carry their own)")
        if chooses and "guards" in raw:
            raise RouteError(f"{where}: with `programs:`, put `guards:` inside each entry")
        steps = ()
        if not has_program:
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
        try:
            if chooses:
                choices = _parse_choices(raw["programs"], where)
            elif has_program:
                kind = _kind(raw.get("kind", program_mod.DEFAULT_KIND), f"{where}.kind")
                program = _parse_program(raw["program"], f"{where}.program")
                guards = program_mod.parse_guards(raw.get("guards"), program, f"{where}.guards")
                choices = (ProgramChoice(kind, None, program, guards),)
            else:
                program, guards = program_mod.program_from_v1(steps, [(f.when, f.cmd) for f in fallbacks])
                choices = (ProgramChoice("sequence", None, program, guards),)
        except ProgramError as exc:
            raise RouteError(str(exc)) from exc
        default = choices[-1]
        program, guards = default.program, default.guards
        if has_program:
            steps = tuple(n.run for n in program_mod.run_nodes(program))
        out.append(Route(rid, description.strip(), triggers, needs, steps, tuple(fallbacks), read, evidence, variants,
                         optional_steps, program, guards, has_program, default.kind, choices, chooses))
    if not out:
        raise RouteError("routes.yaml defines no routes")
    ids = frozenset(r.id for r in out)
    for route in out:
        try:
            for choice in route.programs:
                program_mod.validate_replacements(choice.guards, route.id, ids, f"route {route.id}")
        except ProgramError as exc:
            raise RouteError(str(exc)) from exc
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
        name = match.group(1)
        if "." in name:  # a step binding: the executor fills it (P3 §4.3)
            return match.group(0)
        value = context.get(name)
        # Blank values are missing; a leading "-" would be parsed by Click as an option.
        if value is None or not str(value).strip() or str(value).startswith("-"):
            missing = True
            return ""
        return shlex.quote(str(value))

    text = _PLACEHOLDER_RE.sub(substitute, template)
    return None if missing else text
