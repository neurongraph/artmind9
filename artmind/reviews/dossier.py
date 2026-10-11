"""The review dossier file (spec 2026-10-11 §8.2): YAML frontmatter plus a
fixed sequence of `## ` sections. The file is the source of truth for a
review; status lives in its frontmatter.

Only the known section titles start a section, and headings inside fenced
code blocks never do, so a proposal may quote YAML or markdown freely.
"""

import os
import re
import secrets
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

STATUSES = ("open", "in-progress", "fixed", "verified", "dismissed")
STAGES = ("corpus-gap", "synthesis", "routing", "missing-view", "identity", "extraction", "schema")
SECTIONS = ("Symptom", "Diagnosis", "Shape audit", "Proposal", "Proof", "Synthetic example",
            "Regression question", "Agent prompt", "Log")

PRIVACY_PREAMBLE = (
    "> The evidence in this dossier is private corpus data. Never copy names,\n"
    "> quotes, or values from it into code, tests, fixtures, comments, commit\n"
    "> messages, or PR text in the checkout. Build tests from the \"Synthetic\n"
    "> example\" section."
)

_ID_RE = re.compile(r"^rv-\d{4}-\d{2}-\d{2}-[0-9a-f]{6}$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_HEADING_RE = re.compile(r"^## (.+?)\s*$")
_DATE_KEYS = ("created", "updated")


class DossierError(ValueError):
    pass


@dataclass
class Dossier:
    meta: dict
    sections: dict[str, str]
    path: Path | None = None

    @property
    def review_id(self) -> str:
        return self.meta["review_id"]


def new_review_id(today: date | None = None) -> str:
    return f"rv-{(today or date.today()).isoformat()}-{secrets.token_hex(3)}"


def reviews_dir() -> Path:
    import paths

    return paths.REVIEWS_DIR


def dossier_path(review_id: str) -> Path:
    if not _ID_RE.match(review_id or ""):
        raise DossierError(f"not a review id: {review_id!r} (expected rv-YYYY-MM-DD-xxxxxx)")
    return reviews_dir() / f"{review_id}.md"


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else _HEADING_RE.match(line)
        if match and match.group(1) in SECTIONS:
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current, buf = match.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def parse(text: str, path: Path | None = None) -> Dossier:
    text = text.replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise DossierError("a dossier starts with a --- YAML frontmatter block")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise DossierError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict) or "review_id" not in meta:
        raise DossierError("frontmatter must be a mapping with a review_id")
    for key in _DATE_KEYS:
        if isinstance(meta.get(key), date):
            meta[key] = meta[key].isoformat()
    return Dossier(meta=meta, sections=_split_sections(text[match.end():]), path=path)


def render(dossier: Dossier) -> str:
    front = yaml.safe_dump(dossier.meta, sort_keys=False, allow_unicode=True, default_flow_style=None).strip()
    parts = [f"---\n{front}\n---\n"]
    for title in SECTIONS:
        body = (dossier.sections.get(title) or "").strip("\n")
        parts.append(f"\n## {title}\n\n{body}\n" if body else f"\n## {title}\n")
    return "".join(parts)


def load(review_id: str) -> Dossier:
    path = dossier_path(review_id)
    if not path.is_file():
        raise DossierError(f"no dossier {review_id} in {path.parent}")
    return parse(path.read_text(encoding="utf-8"), path)


def iter_dossiers() -> Iterator[tuple[Path, Dossier | None, str | None]]:
    """Every `*.md` in the reviews folder, sorted by name: (path, dossier, error)."""
    directory = reviews_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        try:
            yield path, parse(path.read_text(encoding="utf-8"), path), None
        except (OSError, DossierError) as exc:
            yield path, None, str(exc)


def save(dossier: Dossier) -> Path:
    """Write atomically. The temp file goes in LOGS_DIR (gitignored, same
    filesystem), never in the committed reviews folder."""
    import paths

    path = dossier_path(dossier.review_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dossier.meta["updated"] = date.today().isoformat()
    fd, tmp = tempfile.mkstemp(prefix=".review-", suffix=".tmp", dir=paths.LOGS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render(dossier))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dossier.path = path
    return path


def append_log(dossier: Dossier, line: str, *, today: date | None = None) -> None:
    entry = f"- {(today or date.today()).isoformat()} {line}"
    log = (dossier.sections.get("Log") or "").rstrip("\n")
    dossier.sections["Log"] = f"{log}\n{entry}" if log else entry
