"""The labelled set for `query plan` replay: <vault>/benchmarking/plan_labels.yaml
(spec 2026-10-22 §4.6). Private: it holds real questions, so it lives in the
vault and is committed only to the vault's own repository, never to the checkout.

Validation collects every error rather than stopping at the first, so one run
of `artmind plan labels validate` shows everything to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from artmind.query_plan import routes as routes_mod

_ID_RE = re.compile(r"L\d{3,}")
_SOURCE_RE = re.compile(r"benchmark|written|trace:[0-9a-f]{8,32}")
_KEYS = frozenset({"id", "question", "benchmark", "source", "domains", "route", "view", "params", "expect",
                   "expected_documents", "notes"})
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
    route_ids = {r.id for r in routes_mod.load_routes()} | {"view"}
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
        domains = raw.get("domains")
        if not isinstance(domains, list) or not domains or not all(isinstance(d, str) and d for d in domains):
            errors.append(f"{where}: domains must be a non-empty list of names")
            domains = []
        route = raw.get("route")
        route_ok = isinstance(route, str) and route in route_ids
        if not route_ok:
            errors.append(f"{where}: unknown route {route!r}")
        view = raw.get("view")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be a mapping")
            params = {}
        if view is not None and not isinstance(view, str):
            errors.append(f"{where}: view must be a saved view name")
            view = None
        elif view is not None:
            if view not in view_params:
                errors.append(f"{where}: no saved view {view!r}")
            elif set(params) - view_params[view]:
                errors.append(f"{where}: view {view} has no param(s) {sorted(set(params) - view_params[view])}")
        elif params:
            errors.append(f"{where}: params need a view")
        if route == "view" and view is None:
            errors.append(f"{where}: route view needs a view")
        elif route_ok and route != "view" and view is not None:
            errors.append(f"{where}: a label with a view must have route view, not {route!r}")
        expect = raw.get("expect", "high")
        if expect not in ("high", "low"):
            errors.append(f"{where}: expect must be high or low")
        documents = raw.get("expected_documents") or []
        if not isinstance(documents, list) or not all(isinstance(d, str) for d in documents):
            errors.append(f"{where}: expected_documents must be a list of paths")
            documents = []
        out.append(Label(lid if isinstance(lid, str) else "", question or "",
                         source if isinstance(source, str) else "", tuple(domains),
                         route if route_ok else "", view, _iso(dict(params)), expect, tuple(documents), str(raw.get("notes") or ""),
                         raw.get("benchmark")))
    if errors:
        raise LabelError(errors)
    return out


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
