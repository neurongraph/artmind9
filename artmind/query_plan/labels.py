"""The labelled set for `query plan` replay: <vault>/benchmarking/plan_labels.yaml
(spec 2026-10-22 §4.6). Private: it holds real questions, so it lives in the
vault and is committed only to the vault's own repository, never to the checkout.

Validation collects every error rather than stopping at the first, so one run
of `artmind plan labels validate` shows everything to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from artmind.query_plan import routes as routes_mod
from artmind.query_plan.intents import INTENT_IDS

#: Intents that plan a retrieval program; a label with any other intent is graded on intent alone.
RETRIEVAL_INTENTS = ("retrieve", "follow_up_why")


#: Routes designed but not yet in routes.yaml, each with the design that defines it. A label may name one
#: so the question shape is counted before the route exists; replay reports it as `planned`, never as
#: wrong. Remove an id here when its route lands in routes.yaml (a test fails while both hold it).
PLANNED_ROUTES = {
    "episode": "docs/superpowers/specs/2026-11-06-assembly-pass-episodes-design.md §7",
}


def is_nonretrieval(intent: str | None) -> bool:
    return intent is not None and intent not in RETRIEVAL_INTENTS

_ID_RE = re.compile(r"L\d{3,}")
_SOURCE_RE = re.compile(r"benchmark|written|trace:[0-9a-f]{8,32}")
_KEYS = frozenset({"id", "question", "benchmark", "source", "domains", "route", "view", "params", "expect",
                   "expected_documents", "notes", "intent", "slots", "program_kind", "follows"})
#: Slot names a label's `slots:` may grade (the keys of the plan's `slots` block).
SLOT_NAMES = frozenset({"narrator", "search_term", "category", "date_window", "scope", "must_find",
                        "previous_subjects"})
_TOP = frozenset({"version", "benchmark_file", "labels"})


class LabelError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Label:
    id: str
    question: str
    source: str
    domains: tuple[str, ...]
    route: str
    view: str | None
    params: dict
    expect: str = "high"
    expected_documents: tuple[str, ...] = ()
    notes: str = ""
    benchmark: str | None = None
    intent: str | None = None  # one of the eight intents; None = not graded
    slots: dict = field(default_factory=dict)  # slot name -> expected value (see replay.slot_ok)
    program_kind: str | None = None  # recorded only, never graded (spec 2026-11-05 §5.5)
    follows: str | None = None  # a label id (the previous turn), "none" (plan alone), None = derive from the trace

    @property
    def nonretrieval(self) -> bool:
        """Graded on intent only: route and domains (if given) are kept, not graded."""
        return is_nonretrieval(self.intent)

    @property
    def planned(self) -> bool:
        """Names a route that is designed but not built: counted, not graded."""
        return not self.nonretrieval and self.route in PLANNED_ROUTES


def default_path() -> Path | None:
    vault = vault_dir()
    return vault / "benchmarking" / "plan_labels.yaml" if vault else None


def vault_dir() -> Path | None:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return Path(vault) if vault else None


def _view_params() -> dict[str, set[str]]:
    from artmind.kg_views import store

    loaded, _ = store.list_views(None)
    return {v.spec.name: set(v.spec.params) for v in loaded}


def _benchmark_questions(base_dir: Path, name: str, errors: list[str]) -> dict[str, str]:
    from artmind.benchmark import _parse_questions_file

    try:
        return {q["qid"]: q["question"] for q in _parse_questions_file(base_dir / name)}
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"benchmark_file: {exc} (read relative to {base_dir.resolve()})")
        return {}


def _iso(value):
    """YAML turns an unquoted 2025-03-04 into a date; plans carry ISO strings."""
    if isinstance(value, date):  # datetime is a date
        return value.isoformat()
    if isinstance(value, list):
        return [_iso(v) for v in value]
    if isinstance(value, dict):
        return {k: _iso(v) for k, v in value.items()}
    return value


def parse_labels(data, base_dir: Path, *, view_params: dict[str, set[str]] | None = None) -> list[Label]:
    """`base_dir` is the vault root: `benchmark_file` is relative to it."""
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or not isinstance(data.get("labels"), list)):
        raise LabelError(["the labels file must be a mapping with `version: 1` and a `labels` list"])
    errors: list[str] = []
    extra = set(data) - _TOP
    if extra:
        errors.append(f"unknown top-level key(s) {sorted(map(str, extra))}")
    questions: dict[str, str] = {}
    if data.get("benchmark_file") is not None:
        if isinstance(data["benchmark_file"], str) and data["benchmark_file"].strip():
            questions = _benchmark_questions(base_dir, data["benchmark_file"], errors)
        else:
            errors.append("benchmark_file must be a path string")
    route_ids = {r.id for r in routes_mod.load_routes()} | {"view"} | set(PLANNED_ROUTES)
    view_params = _view_params() if view_params is None else view_params
    out: list[Label] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["labels"]):
        if not isinstance(raw, dict):
            errors.append(f"labels[{i}] must be a mapping")
            continue
        lid = raw.get("id")
        where = f"label {lid}" if isinstance(lid, str) else f"labels[{i}]"
        bad = set(raw) - _KEYS
        if bad:
            errors.append(f"{where}: unknown key(s) {sorted(map(str, bad))}")
        if not isinstance(lid, str) or not _ID_RE.fullmatch(lid):
            errors.append(f"{where}: id must be L followed by 3 or more digits")
        elif lid in seen:
            errors.append(f"duplicate label id {lid}")
        else:
            seen.add(lid)
        question = None
        if ("question" in raw) == ("benchmark" in raw):
            errors.append(f"{where}: give exactly one of question / benchmark")
        elif "question" in raw:
            if isinstance(raw["question"], str) and raw["question"].strip():
                question = raw["question"].strip()
            else:
                errors.append(f"{where}: question must be a non-empty string")
        else:
            ref = raw["benchmark"]
            question = questions.get(ref) if isinstance(ref, str) else None
            if question is None:
                errors.append(f"{where}: benchmark {raw['benchmark']!r} is not in benchmark_file")
        source = raw.get("source")
        if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
            errors.append(f"{where}: source must be benchmark, written or trace:<id>")
        intent = raw.get("intent")
        if intent is not None and intent not in INTENT_IDS:
            errors.append(f"{where}: intent must be one of {', '.join(INTENT_IDS)}")
            intent = None
        nonretrieval = is_nonretrieval(intent)
        domains = raw.get("domains")
        if nonretrieval and domains is None:
            domains = []
        elif not isinstance(domains, list) or not domains or not all(isinstance(d, str) and d for d in domains):
            errors.append(f"{where}: domains must be a non-empty list of names")
            domains = []
        route = raw.get("route")
        route_ok = isinstance(route, str) and route in route_ids
        if nonretrieval and route is None:
            pass
        elif not route_ok:
            errors.append(f"{where}: unknown route {route!r}")
        view = raw.get("view")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be a mapping")
            params = {}
        if nonretrieval and (view is not None or params):
            errors.append(f"{where}: a {intent} label plans no retrieval, so it takes no view or params")
            view, params = None, {}
        elif view is not None and not isinstance(view, str):
            errors.append(f"{where}: view must be a saved view name")
            view = None
        elif view is not None:
            if view not in view_params:
                errors.append(f"{where}: no saved view {view!r}")
            elif set(params) - view_params[view]:
                errors.append(f"{where}: view {view} has no param(s) {sorted(set(params) - view_params[view])}")
        elif params:
            errors.append(f"{where}: params need a view")
        if nonretrieval:
            pass
        elif route == "view" and view is None:
            errors.append(f"{where}: route view needs a view")
        elif route_ok and route != "view" and view is not None:
            errors.append(f"{where}: a label with a view must have route view, not {route!r}")
        expect = raw.get("expect", "high")
        if expect not in ("high", "low"):
            errors.append(f"{where}: expect must be high or low")
        slots = raw.get("slots") or {}
        if not isinstance(slots, dict) or set(slots) - SLOT_NAMES:
            errors.append(f"{where}: slots must be a mapping with names from {sorted(SLOT_NAMES)}")
            slots = {}
        program_kind = raw.get("program_kind")
        if program_kind is not None and not (isinstance(program_kind, str) and program_kind.strip()):
            errors.append(f"{where}: program_kind must be a string")
            program_kind = None
        follows = raw.get("follows")
        if follows is not None and not (isinstance(follows, str) and (follows == "none" or _ID_RE.fullmatch(follows))):
            errors.append(f"{where}: follows must be a label id or none")
            follows = None
        documents = raw.get("expected_documents") or []
        if not isinstance(documents, list) or not all(isinstance(d, str) for d in documents):
            errors.append(f"{where}: expected_documents must be a list of paths")
            documents = []
        out.append(Label(lid if isinstance(lid, str) else "", question or "",
                         source if isinstance(source, str) else "", tuple(domains),
                         route if route_ok else "", view, _iso(dict(params)), expect, tuple(documents), str(raw.get("notes") or ""),
                         raw.get("benchmark"), intent, _iso(dict(slots)),
                         program_kind.strip() if program_kind else None, follows))
    errors += _check_threads(out)
    if errors:
        raise LabelError(errors)
    return out


def _check_threads(labels: list[Label]) -> list[str]:
    """`follows` names an existing label, never itself, and the chains have no cycle."""
    errors: list[str] = []
    ids = {lb.id for lb in labels}
    edges: dict[str, str] = {}
    for lb in labels:
        if lb.follows in (None, "none"):
            continue
        if lb.follows not in ids:
            errors.append(f"label {lb.id}: follows {lb.follows!r} is not a label id")
        else:
            edges[lb.id] = lb.follows
    reported: set[str] = set()
    for start in edges:
        path, node = [], start
        while node in edges and node not in path:
            path.append(node)
            node = edges[node]
        if node in path and node not in reported:
            cycle = path[path.index(node):]
            reported.update(cycle)
            errors.append("follows forms a cycle: " + " -> ".join([*cycle, node]))
    return errors


def _yaml_header(exc: yaml.YAMLError) -> str:
    """Line/column only: a YAML message carries a source snippet, which may hold question text."""
    mark = getattr(exc, "problem_mark", None)
    return f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""


def load_labels(path: Path) -> list[Label]:
    """`benchmark_file` is read relative to the vault root (`paths.ARTMIND_VAULT_DIR`
    when set, else the labels file's grandparent: `<vault>/benchmarking/<file>`)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelError([f"{path}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise LabelError([f"{path} is not valid YAML{_yaml_header(exc)}"]) from exc
    return parse_labels(data, vault_dir() or path.resolve().parent.parent)


def check_entities(labels: list[Label]) -> list[str]:
    """Needs Neo4j: every entity param name resolves the way the planner resolves a mention,
    in the label's domains and as the view param's entity class."""
    from artmind.kg_views import store
    from artmind.kg_views.model import ViewError
    from artmind.query_plan import candidates

    errors: list[str] = []
    views: dict[str, object] = {}
    resolved: dict[tuple, bool] = {}
    for label in labels:
        if not label.view:
            continue
        if label.view not in views:
            try:
                views[label.view] = store.load_view(label.view).spec
            except (ViewError, OSError) as exc:
                views[label.view] = exc
        spec = views[label.view]
        if isinstance(spec, Exception):
            errors.append(f"label {label.id}: view {label.view!r} cannot be loaded: {spec}")
            continue
        for name, expected in label.params.items():
            param = spec.params.get(name)
            if param is None or "entity" not in (param.type, param.items):
                continue
            wanted = (param.entity_class or "").casefold()
            for value in (expected if isinstance(expected, list) else [expected]):
                key = (label.view, name, tuple(label.domains), str(value))
                if key not in resolved:
                    text = str(value)
                    mention = candidates.Mention(text=text, start=0, end=len(text), via="capitalised")
                    refs = candidates.resolve_mention(mention, list(label.domains), None)
                    resolved[key] = any(not wanted or str(r.get("entity_class") or "").casefold() == wanted
                                        for r in refs)
                if not resolved[key]:
                    kind = f" as a {param.entity_class}" if param.entity_class else ""
                    errors.append(f"label {label.id}: {name}={value!r} does not resolve{kind} "
                                  f"in {list(label.domains)}")
    return errors
