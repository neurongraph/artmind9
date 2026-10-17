"""Insights snapshots on disk (spec §9.1-§9.2).

`<INSIGHTS_DIR>/<domain>/runs/<created_at>.json` plus `latest.json`. Writes are
atomic: the temp file goes in LOGS_DIR (gitignored, same filesystem), never in
the committed folder, as for review dossiers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from artmind.insights.model import InsightsError, Run

LATEST = "latest.json"
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def insights_root() -> Path:
    import paths

    return Path(paths.INSIGHTS_DIR)


def domain_dir(domain: str) -> Path:
    if not _DOMAIN_RE.match(domain):
        raise InsightsError(f"invalid domain name {domain!r}")
    return insights_root() / domain


def runs_dir(domain: str) -> Path:
    return domain_dir(domain) / "runs"


def run_filename(created_at: str) -> str:
    return created_at.replace(":", "-") + ".json"


def _write_atomic(path: Path, text: str) -> None:
    import paths

    path.parent.mkdir(parents=True, exist_ok=True)
    Path(paths.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".insights-", suffix=".tmp", dir=paths.LOGS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dumps(run: Run) -> str:
    return json.dumps(run.to_dict(), ensure_ascii=False, indent=1, default=str) + "\n"


def write_run(run: Run) -> Path:
    text = dumps(run)
    path = runs_dir(run.domain) / run_filename(run.created_at)
    _write_atomic(path, text)
    _write_atomic(domain_dir(run.domain) / LATEST, text)
    return path


def load_run(path: Path) -> Run:
    try:
        return Run.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InsightsError(f"{path} is unreadable: {exc}") from exc


def load_latest(domain: str) -> Run | None:
    path = domain_dir(domain) / LATEST
    return load_run(path) if path.is_file() else None


def list_runs(domain: str) -> list[Path]:
    root = runs_dir(domain)
    return sorted(root.glob("*.json")) if root.is_dir() else []


def prune(domain: str, keep: int) -> list[str]:
    runs = list_runs(domain)
    doomed = runs[:-keep] if keep > 0 and len(runs) > keep else []
    for path in doomed:
        path.unlink()
    return [p.name for p in doomed]
