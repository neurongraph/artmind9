"""Machine-local, content-addressed embedding cache (spec 2026-10-31 §3.1).

Key: sha1(embed_key, embedded text). A vector is a pure function of that pair, so
any hit is valid on this machine: a re-commit, a new document version with
unchanged passages, or a rebuilt graph never asks the model again. Lives in the
data dir (`<data>/embedding_cache.sqlite`), never committed. Unusable location ->
the cache is off (logged once) and every call goes to the model.
"""
from __future__ import annotations

import array
import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

from loguru import logger


def cache_path() -> Path:
    import paths

    return Path(paths.ARTMIND_DATA_DIR) / "embedding_cache.sqlite"


class EmbeddingCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else cache_path()
        self._conn: sqlite3.Connection | None = None
        self._disabled = False

    @staticmethod
    def key(embed_key: str, text: str) -> str:
        return hashlib.sha1(f"{embed_key}\0{text}".encode("utf-8")).hexdigest()

    def _db(self) -> sqlite3.Connection | None:
        if self._disabled:
            return None
        if self._conn is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self.path)
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
                )
            except (OSError, sqlite3.Error) as exc:
                logger.warning("Embedding cache off ({}): {}", self.path, exc)
                self._disabled = True
                return None
        return self._conn

    def get(self, key: str) -> list[float] | None:
        db = self._db()
        if db is None:
            return None
        row = db.execute("SELECT vector FROM vectors WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        vec = array.array("d")
        vec.frombytes(row[0])
        return vec.tolist()

    def put(self, key: str, vector: list[float]) -> None:
        db = self._db()
        if db is None:
            return
        db.execute("INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                   (key, array.array("d", vector).tobytes()))
        db.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def cached_embed(cache: EmbeddingCache | None, embed_key: str, text: str,
                 embed: Callable[[str], list[float]]) -> list[float]:
    if cache is None:
        return embed(text)
    key = EmbeddingCache.key(embed_key, text)
    hit = cache.get(key)
    if hit is not None:
        return hit
    vector = embed(text)
    if vector:
        cache.put(key, vector)
    return vector
