"""Output budget and result handles (spec 2026-11-05-bounded-output-design §5).

Stdlib only. `_echo_json` calls `fit` with the serialiser it prints with: a payload within
`ARTMIND_OUTPUT_BUDGET` characters is returned as is; a larger one is stored whole under
`<logs>/results/<handle>.json` and a cut copy (the command's main list shortened from the
tail) is returned. `artmind query page` reads the file back. Result files hold private corpus
text, live only in gitignored `logs/`, and are pruned after 24 hours by the next write.
"""

import contextlib
import contextvars
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

BUDGET_ENV = "ARTMIND_OUTPUT_BUDGET"
MAX_STRING = 2000
RESULT_TTL_S = 24 * 3600
_HANDLE_RE = re.compile(r"[0-9a-f]{12}")
_FALLBACK_KEYS = ("documents", "results", "rows")

_UNSET = object()
_override: contextvars.ContextVar = contextvars.ContextVar("artmind_output_budget", default=_UNSET)


class BudgetError(ValueError):
    pass


@contextlib.contextmanager
def budget_override(value: str | None):
    """`serve` applies the caller's budget for one request, never its own env."""
    token = _override.set(value)
    try:
        yield
    finally:
        _override.reset(token)


def parse_budget(raw: str | None) -> int:
    """Characters; unset, empty or 0 = off. A bad value raises BudgetError naming the variable."""
    if raw is None or not str(raw).strip():
        return 0
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = -1
    if value < 0:
        raise BudgetError(f"{BUDGET_ENV} must be a whole number of characters (0 = off), got {raw!r}")
    return value


def current_budget() -> int:
    value = _override.get()
    return parse_budget(os.environ.get(BUDGET_ENV) if value is _UNSET else value)


def is_handle(text: str) -> bool:
    return bool(_HANDLE_RE.fullmatch(text or ""))


def list_key(payload: dict, declared: str | None) -> str | None:
    if declared and isinstance(payload.get(declared), list):
        return declared
    for key in _FALLBACK_KEYS:
        if isinstance(payload.get(key), list):
            return key
    return None


def _shorten(value: Any, path: str, hits: list[str]) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            hits.append(path or "(value)")
            return value[:MAX_STRING] + "…"
        return value
    if isinstance(value, list):
        return [_shorten(v, f"{path}[{i}]", hits) for i, v in enumerate(value)]
    if isinstance(value, dict):
        return {k: _shorten(v, f"{path}.{k}" if path else str(k), hits) for k, v in value.items()}
    return value


def _with_warning(payload: dict, line: str) -> dict:
    existing = payload.get("warnings")
    existing = existing if isinstance(existing, list) else ([] if existing is None else [existing])
    return {**payload, "warnings": [*existing, line]}


def cut(payload: dict, budget: int, dumps: Callable[[Any], str], declared: str | None,
        handle: str) -> dict:
    """The payload cut to `budget` characters, pointing at `handle` for the rest."""
    key = list_key(payload, declared)
    label = f"output budget {budget:,} characters"

    def build(base: dict, rows: list | None) -> dict:
        out = dict(base)
        if key is not None and rows is not None:
            out[key] = rows
            out["rows_total"] = len(payload[key])
            out["rows_shown"] = len(rows)
            out["next"] = f"artmind query page {handle} --from {len(rows)}"
            out = _with_warning(out, f"{label}: showing {len(rows)} of {len(payload[key])} {key}")
        else:
            out["next"] = f"artmind query page {handle}"
        out["truncated"] = True
        out["complete"] = False
        out["handle"] = handle
        return out

    rows_all = payload[key] if key is not None else None
    shown: list | None = None
    if rows_all is not None:
        lo, hi = 0, len(rows_all)
        while lo < hi:  # largest n whose cut payload fits
            mid = (lo + hi + 1) // 2
            if len(dumps(build(payload, rows_all[:mid]))) <= budget:
                lo = mid
            else:
                hi = mid - 1
        shown = rows_all[:lo]
        candidate = build(payload, shown)
        if shown and len(dumps(candidate)) <= budget:
            return candidate
        # No whole row fits (one row larger than the budget, e.g. a hub entity's
        # entity-context): never answer with zero rows. Shorten long strings and keep
        # as many shortened rows as fit, at least the first one.

    hits: list[str] = []
    shortened = _shorten(payload, "", hits)
    if key is not None and rows_all:
        short_rows = shortened[key]
        lo, hi = 1, len(short_rows)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(dumps(build(shortened, short_rows[:mid]))) <= budget:
                lo = mid
            else:
                hi = mid - 1
        shown = short_rows[:lo]
    out = build(shortened, shown)
    if hits:
        names = ", ".join(hits[:5]) + (f" and {len(hits) - 5} more" if len(hits) > 5 else "")
        out = _with_warning(out, f"{label}: shortened to {MAX_STRING:,} characters: {names}")
    return out


def prune_files(results_dir: Path, max_age_s: float | None = RESULT_TTL_S, dry_run: bool = False) -> int:
    """Remove result files older than `max_age_s` (None = all). Never raises."""
    count = 0
    try:
        files = [p for p in Path(results_dir).glob("*.json") if is_handle(p.stem)]
    except OSError:
        return 0
    now = time.time()
    for path in files:
        try:
            if max_age_s is not None and now - path.stat().st_mtime <= max_age_s:
                continue
            if not dry_run:
                path.unlink()
            count += 1
        except OSError:
            continue
    return count


def store(results_dir: Path, payload: dict, dumps: Callable[[Any], str]) -> str:
    """Write the full payload atomically; return its handle."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    prune_files(results_dir)
    handle = secrets.token_hex(6)
    fd, tmp = tempfile.mkstemp(dir=results_dir, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(payload))
        os.replace(tmp, results_dir / f"{handle}.json")
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return handle


def load(results_dir: Path, handle: str) -> dict:
    if not is_handle(handle):
        raise BudgetError(f"{handle!r} is not a result handle (12 hex characters)")
    path = Path(results_dir) / f"{handle}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BudgetError(f"no stored result {handle} (results are kept 24 hours; re-run the command)") from None
    except (OSError, ValueError) as exc:
        raise BudgetError(f"stored result {handle} is unreadable: {exc}") from None


def fit(payload: Any, budget: int, dumps: Callable[[Any], str], declared: str | None,
        results_dir: Path) -> tuple[Any, int, int, str | None]:
    """(payload to print, payload_chars, shown_chars, handle or None)."""
    n = len(dumps(payload))
    if not budget or not isinstance(payload, dict) or n <= budget:
        return payload, n, n, None
    handle = store(results_dir, payload, dumps)
    out = cut(payload, budget, dumps, declared, handle)
    return out, n, len(dumps(out)), handle
