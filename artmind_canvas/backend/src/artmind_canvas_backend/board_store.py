"""Canvas board persistence — application state, not knowledge (ADR 0009).

A **Board** is a named, saved arrangement of Cards: which Cards are open, their
positions/sizes, each Card's filter spec, and the canvas viewport. ADR 0009
decided this is *application state*, not authoritative knowledge — so it lives in
a canvas-owned state dir, **never** in the Vault (ADR 0002) or the graph.

Storage is one JSON file per board under ``$ARTMIND_CANVAS_STATE_DIR/boards/``
(default ``~/.artmind_canvas/boards/``). Single-user and local: plain JSON is
simple, inspectable, and hand-recoverable — appropriate because losing a layout
is non-catastrophic (it is re-arrangeable), unlike losing knowledge.

This module is pure client-side application state. It touches neither artmind's
ingestion pipeline nor its graph — consistent with the pure-client boundary.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


def canvas_state_dir() -> Path:
    """The canvas-owned state root. Read at call time so it is overridable per run/test."""
    env = os.environ.get("ARTMIND_CANVAS_STATE_DIR")
    root = Path(env).expanduser() if env else Path.home() / ".artmind_canvas"
    return root.resolve()


def _boards_dir() -> Path:
    d = canvas_state_dir() / "boards"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class Size(BaseModel):
    width: float
    height: float


class Viewport(BaseModel):
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class CardInstance(BaseModel):
    """One Card placed on a board. ``props``/``filterSpec`` are opaque here —
    their shape is owned by the client-side Card contract (ADR 0014)."""

    id: str
    cardType: str
    props: dict = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)
    size: Size | None = None
    filterSpec: dict | None = None


class Board(BaseModel):
    id: str
    name: str
    cards: list[CardInstance] = Field(default_factory=list)
    viewport: Viewport | None = None
    createdAt: str
    updatedAt: str


class BoardSummary(BaseModel):
    id: str
    name: str
    updatedAt: str


class BoardPatch(BaseModel):
    """A save. Only the fields present are applied; others are left untouched."""

    name: str | None = None
    cards: list[CardInstance] | None = None
    viewport: Viewport | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(board_id: str) -> Path:
    # Ids are server-generated (uuid4 hex); guard anyway against path tricks.
    if not board_id or "/" in board_id or "\\" in board_id or board_id.startswith("."):
        raise ValueError("invalid board id")
    return _boards_dir() / f"{board_id}.json"


def _write(board: Board) -> None:
    p = _path(board.id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(board.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)  # replace is atomic on the same filesystem


def list_boards() -> list[BoardSummary]:
    out: list[BoardSummary] = []
    for f in _boards_dir().glob("*.json"):
        try:
            b = Board.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue  # skip a corrupt file rather than fail the whole listing
        out.append(BoardSummary(id=b.id, name=b.name, updatedAt=b.updatedAt))
    out.sort(key=lambda s: s.updatedAt, reverse=True)
    return out


def create_board(name: str) -> Board:
    now = _now()
    board = Board(
        id=uuid.uuid4().hex,
        name=name or "Untitled board",
        createdAt=now,
        updatedAt=now,
    )
    _write(board)
    return board


def get_board(board_id: str) -> Board:
    p = _path(board_id)
    if not p.is_file():
        raise FileNotFoundError(board_id)
    return Board.model_validate_json(p.read_text(encoding="utf-8"))


def save_board(board_id: str, patch: BoardPatch) -> Board:
    board = get_board(board_id)
    if patch.name is not None:
        board.name = patch.name
    if patch.cards is not None:
        board.cards = patch.cards
    if patch.viewport is not None:
        board.viewport = patch.viewport
    board.updatedAt = _now()
    _write(board)
    return board


def delete_board(board_id: str) -> None:
    """Idempotent: deleting a missing board is a no-op."""
    p = _path(board_id)
    if p.is_file():
        p.unlink()
