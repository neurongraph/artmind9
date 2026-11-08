"""The retrieval eval's labelled set: <vault>/benchmarking/retrieval_labels.yaml
(spec 2026-10-31-vector-retrieval-passages-design.md §6.1).

Private: real questions and corpus ids. It stays in the vault, never in a checkout.
Labels name chunk ids (stable across the passage change), never passage ids.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

from artmind.benchmark_labels import vault_dir

KINDS = ("needle", "survey")
SOURCES = ("synthetic", "hand")


class LabelError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class RetrievalLabel:
    id: str
    question: str
    domains: tuple[str, ...]
    kind: str
    relevant: tuple[str, ...] = ()
    relevant_docs: tuple[str, ...] = ()
    source: str = "hand"


def default_path() -> Path | None:
    vault = vault_dir()
    return vault / "benchmarking" / "retrieval_labels.yaml" if vault else None


def _str_list(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return [v.strip() for v in value]


def parse_labels(data) -> list[RetrievalLabel]:
    if not isinstance(data, dict) or not isinstance(data.get("labels"), list):
        raise LabelError(["top level must be a mapping with a `labels` list"])
    errors: list[str] = []
    out: list[RetrievalLabel] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["labels"]):
        where = f"labels[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        lid = str(raw.get("id") or "").strip()
        question = str(raw.get("question") or "").strip()
        domains = _str_list(raw.get("domains"))
        kind = raw.get("kind")
        source = raw.get("source", "hand")
        relevant = _str_list(raw.get("relevant", [])) or []
        relevant_docs = _str_list(raw.get("relevant_docs", [])) or []
        problems = []
        if not lid:
            problems.append(f"{where}: id is required")
        elif lid in seen:
            problems.append(f"{where}: duplicate id {lid!r}")
        if not question:
            problems.append(f"{where}: question is required")
        if not domains:
            problems.append(f"{where}: domains must be a non-empty list of names")
        if kind not in KINDS:
            problems.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        elif kind == "needle" and not relevant:
            problems.append(f"{where}: needle needs a non-empty `relevant` list of chunk ids")
        elif kind == "survey" and not relevant_docs:
            problems.append(f"{where}: survey needs a non-empty `relevant_docs` list of document ids")
        if source not in SOURCES:
            problems.append(f"{where}: source must be one of {', '.join(SOURCES)}")
        if lid:
            seen.add(lid)
        if problems:
            errors.extend(problems)
            continue
        out.append(RetrievalLabel(id=lid, question=question, domains=tuple(domains), kind=kind,
                                  relevant=tuple(relevant), relevant_docs=tuple(relevant_docs),
                                  source=source))
    if errors:
        raise LabelError(errors)
    return out


def load_labels(path: Path) -> list[RetrievalLabel]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LabelError([f"cannot read {path}: {exc}"]) from exc
    return parse_labels(data)


def _to_yaml_entry(label: RetrievalLabel) -> dict:
    entry = {k: v for k, v in asdict(label).items() if v not in ((), "")}
    for key in ("domains", "relevant", "relevant_docs"):
        if key in entry:
            entry[key] = list(entry[key])
    return entry


def _next_id(taken: set[str]) -> str:
    n = 1
    while f"r{n:03d}" in taken:
        n += 1
    return f"r{n:03d}"


def append_labels(path: Path, new: list[RetrievalLabel]) -> list[RetrievalLabel]:
    """Append `new` to the labels file (created if absent). A label with an empty id
    gets the next free `rNNN`. Written atomically. Returns the labels as appended."""
    path = Path(path)
    existing = load_labels(path) if path.exists() else []
    taken = {l.id for l in existing}
    added: list[RetrievalLabel] = []
    for label in new:
        if not label.id or label.id in taken:
            label = replace(label, id=_next_id(taken))
        taken.add(label.id)
        added.append(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"labels": [_to_yaml_entry(l) for l in [*existing, *added]]},
                          sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".retrieval_labels.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return added
