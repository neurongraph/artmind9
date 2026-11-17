"""FastAPI dependency: a web write path on an unmigrated vault answers 409 (spec 2026-11-15 §8.1)."""
from __future__ import annotations

from fastapi import HTTPException

from artmind import data_model


def data_model_gate() -> None:
    """Plain `def` so FastAPI runs it in the threadpool (it may read the graph marker)."""
    try:
        data_model.require_current("web write")
    except data_model.DataModelBehind as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
