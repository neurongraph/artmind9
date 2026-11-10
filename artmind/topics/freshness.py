"""Topic plan freshness (spec 2026-11-10 §8.1). A check's result is stored beside the plan
under `logs/topic-freshness/<slug>.json` (private, gitignored); `stored` reads it."""
from __future__ import annotations

import json
from pathlib import Path


def _dir() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "topic-freshness"


def stored(slug: str) -> dict | None:
    try:
        data = json.loads((_dir() / f"{slug}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
