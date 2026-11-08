"""Benchmark labels: the expected documents of benchmark questions (spec 2026-11-08 §2.3).

Read from <vault>/benchmarking/plan_labels.yaml. Only `id`, `question` and `expected_documents` are read; every
other key (written by the frozen planner's tooling) is ignored, so this module never imports artmind.query_plan.
Private: the file holds real questions and stays in the vault.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

import yaml


class LabelError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkLabel:
    id: str
    question: str
    expected_documents: tuple[str, ...] = ()


def vault_dir() -> Path | None:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return Path(vault) if vault else None


def default_path() -> Path | None:
    vault = vault_dir()
    return vault / "benchmarking" / "plan_labels.yaml" if vault else None


def load_labels(path: Path) -> list[BenchmarkLabel]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LabelError(f"{path}: {exc}") from exc
    rows = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise LabelError(f"{path}: no `labels:` list")
    out = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise LabelError(f"{path}: label {i} is not a mapping")
        lid = str(row.get("id") or f"#{i}")
        question = str(row.get("question") or "").strip()
        if not question:
            raise LabelError(f"{path}: label {lid} has no question")
        docs = row.get("expected_documents") or []
        if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
            raise LabelError(f"{path}: label {lid}: expected_documents must be a list of paths")
        out.append(BenchmarkLabel(lid, question, tuple(docs)))
    return out


def _norm(value) -> str:
    text = str(value or "").replace("\\", "/").strip().casefold()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _stem(name: str) -> str:
    return posixpath.splitext(posixpath.basename(name))[0]


def match_kind(expected: str, doc: dict) -> str | None:
    """How `doc` matches the expected entry: "path" (equal, or one a trailing path of the other, in
    `path` / `source_path` / `name` / `id`), "basename" (same file name), "title" (the title equals the file
    name without its extension; brief rows carry only that), or None."""
    want = _norm(expected)
    if not want:
        return None
    paths = [_norm(doc.get(k)) for k in ("path", "source_path", "name", "id") if doc.get(k)]
    for have in paths:
        if have == want or have.endswith("/" + want) or (have.count("/") and want.endswith("/" + have)):
            return "path"
    base = posixpath.basename(want)
    if any(posixpath.basename(have) == base for have in paths):
        return "basename"
    title = _norm(doc.get("title"))
    if title and title in (_stem(want), base):
        return "title"
    return None


def reached_documents(expected, docs: list[dict]) -> tuple[list[str], list[str]]:
    """(matched, missed) of the expected entries against the documents."""
    matched, missed = [], []
    for want in expected:
        (matched if any(match_kind(want, d) for d in docs) else missed).append(want)
    return matched, missed
