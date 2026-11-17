"""Fast, memoised reads of domain schema YAML for the query path.

Every `artmind query` call is usually a short-lived subprocess (an agent's Bash),
so a per-process cache rarely hits: what matters is the cost of one parse. The
pure-Python `yaml.SafeLoader` takes ~15 ms per schema file; libyaml's
`CSafeLoader` builds the same objects in ~1.5 ms. A query that reads every
schema (the domain guard) or a family's schemas (brief rows' declared
properties) pays that once per file.

`load(path)` returns a fresh deep copy each call, so callers may mutate it; the
cache is keyed by (path, mtime_ns, size), so an edited file is read again.
"""
from __future__ import annotations

import copy
import functools
from pathlib import Path

import yaml

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(text: str):
    """`yaml.safe_load` through libyaml when it is installed (same result)."""
    return yaml.load(text, Loader=_Loader)


@functools.lru_cache(maxsize=256)
def _load_cached(path: str, mtime_ns: int, size: int):
    return safe_load(Path(path).read_text(encoding="utf-8"))


def load(path: Path | str):
    """The parsed YAML at `path` (a deep copy; callers may mutate it).
    Raises OSError / yaml.YAMLError like a direct read would."""
    st = Path(path).stat()
    return copy.deepcopy(_load_cached(str(path), st.st_mtime_ns, st.st_size))
