"""The topic map cache (spec 2026-11-10 §7.3): one map answer per (document content sha,
subject text, map prompt version, model), under `logs/topic-map-cache/` (private,
gitignored). A rebuild or refresh maps only documents that are new or changed."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def cache_dir() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "topic-map-cache"


def key(content_sha: str | None, subject: str, prompt_version: int, model: str | None) -> str | None:
    """None when the document has no version to key on (then it is always mapped)."""
    if not content_sha:
        return None
    text = json.dumps([content_sha, " ".join(subject.split()), prompt_version, model or ""], ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def get(k: str | None):
    if k is None:
        return None
    try:
        return json.loads((cache_dir() / f"{k}.json").read_text(encoding="utf-8")).get("answer")
    except (OSError, ValueError, AttributeError):
        return None


def put(k: str | None, answer) -> None:
    if k is None or not isinstance(answer, dict):
        return
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{k}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"answer": answer}, fh, ensure_ascii=False)
        os.replace(tmp, directory / f"{k}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
