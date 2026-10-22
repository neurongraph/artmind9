"""The labelled set for `query plan` replay: <vault>/benchmarking/plan_labels.yaml
(spec 2026-10-22 §4.6). Private: it holds real questions, so it lives in the
vault and is committed only to the vault's own repository, never to the checkout.

Validation collects every error rather than stopping at the first, so one run
of `artmind plan labels validate` shows everything to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from artmind.query_plan import routes as routes_mod

_ID_RE = re.compile(r"^L\d{3,}$")
_SOURCE_RE = re.compile(r"^(benchmark|written|trace:[0-9a-f]{8,})$")
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
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return Path(vault) / "benchmarking" / "plan_labels.yaml" if vault else None


def _view_params() -> dict[str, set[str]]:
    from artmind.kg_views import store

    loaded, _ = store.list_views(None)
    return {v.spec.name: set(v.spec.params) for v in loaded}


def _benchmark_questions(base_dir: Path, name: str, errors: list[str]) -> dict[str, str]:
    from artmind.benchmark import _parse_questions_file

    try:
        return {q["qid"]: q["question"] for q in _parse_questions_file(base_dir / name)}
    except OSError as exc:
        errors.append(f"benchmark_file: {exc}")
        return {}


def parse_labels(data, base_dir: Path, *, view_params: dict[str, set[str]] | None = None) -> list[Label]:
    """`base_dir` is the vault root: `benchmark_file` is relative to it."""
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("labels"), list):
        raise LabelError(["the labels file must be a mapping with `version: 1` and a `labels` list"])
    errors: list[str] = []
    extra = set(data) - _TOP
    if extra:
        errors.append(f"unknown top-level key(s) {sorted(extra)}")
    questions = _benchmark_questions(base_dir, data["benchmark_file"], errors) if data.get("benchmark_file") else {}
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
            errors.append(f"{where}: unknown key(s) {sorted(bad)}")
        if not isinstance(lid, str) or not _ID_RE.match(lid):
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
            question = questions.get(raw["benchmark"])
            if question is None:
                errors.append(f"{where}: benchmark {raw['benchmark']!r} is not in benchmark_file")
        source = raw.get("source")
        if not isinstance(source, str) or not _SOURCE_RE.match(source):
            errors.append(f"{where}: source must be benchmark, written or trace:<id>")
        domains = raw.get("domains")
        if not isinstance(domains, list) or not domains or not all(isinstance(d, str) and d for d in domains):
            errors.append(f"{where}: domains must be a non-empty list of names")
            domains = []
        route = raw.get("route")
        if route not in route_ids:
            errors.append(f"{where}: unknown route {route!r}")
        view = raw.get("view")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be a mapping")
            params = {}
        if view is not None:
            if view not in view_params:
                errors.append(f"{where}: no saved view {view!r}")
            elif set(params) - view_params[view]:
                errors.append(f"{where}: view {view} has no param(s) {sorted(set(params) - view_params[view])}")
        elif params:
            errors.append(f"{where}: params need a view")
        if route == "view" and view is None:
            errors.append(f"{where}: route view needs a view")
        expect = raw.get("expect", "high")
        if expect not in ("high", "low"):
            errors.append(f"{where}: expect must be high or low")
        documents = raw.get("expected_documents") or []
        if not isinstance(documents, list) or not all(isinstance(d, str) for d in documents):
            errors.append(f"{where}: expected_documents must be a list of paths")
            documents = []
        out.append(Label(lid if isinstance(lid, str) else "", question or "", source or "", tuple(domains),
                         route or "", view, dict(params), expect, tuple(documents), str(raw.get("notes") or ""),
                         raw.get("benchmark")))
    if errors:
        raise LabelError(errors)
    return out


def load_labels(path: Path) -> list[Label]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelError([f"{path}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise LabelError([f"{path} is not valid YAML: {exc}"]) from exc
    return parse_labels(data, path.resolve().parent.parent)


def check_entities(labels: list[Label]) -> list[str]:
    """Needs Neo4j: every entity param name resolves in its label's domains by name evidence."""
    from artmind import vector_query
    from artmind.kg_views import store

    errors = []
    for label in labels:
        if not label.view:
            continue
        spec = store.load_view(label.view).spec
        for name, expected in label.params.items():
            if spec.params[name].type != "entity":
                continue
            for value in (expected if isinstance(expected, list) else [expected]):
                rows = vector_query.entity_resolve(list(label.domains), str(value), 10).get("rows", [])
                if not any({"exact", "fulltext"} & set(r.get("matched_by") or []) for r in rows):
                    errors.append(f"label {label.id}: {name}={value!r} does not resolve in {list(label.domains)}")
    return errors
