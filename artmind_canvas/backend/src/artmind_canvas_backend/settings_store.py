"""Global UI settings — panel geometry, not board state (ADR 0009 / ADR 0015).

Panel widths and collapsed state are *global UI chrome*, deliberately NOT stored
per-board (ADR 0015): switching boards must not make the sidebars lurch. They live
in a single ``settings.json`` under ``$ARTMIND_CANVAS_STATE_DIR`` — the same canvas-owned
state root as the board store, but a separate file with a separate concern.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from artmind_canvas_backend.board_store import canvas_state_dir


class PanelState(BaseModel):
    width: float | None = None  # None → the client's default width
    collapsed: bool = False


class Settings(BaseModel):
    """Global UI chrome. Extend with new panels/prefs as the workspace grows."""

    chat: PanelState = Field(default_factory=PanelState)
    editor: PanelState = Field(default_factory=PanelState)


class SettingsPatch(BaseModel):
    chat: PanelState | None = None
    editor: PanelState | None = None


def _settings_path():
    return canvas_state_dir() / "settings.json"


def get_settings() -> Settings:
    p = _settings_path()
    if not p.is_file():
        return Settings()
    try:
        return Settings.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:
        return Settings()  # ignore a corrupt file rather than fail the UI


def save_settings(patch: SettingsPatch) -> Settings:
    settings = get_settings()
    if patch.chat is not None:
        settings.chat = patch.chat
    if patch.editor is not None:
        settings.editor = patch.editor
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)  # atomic on the same filesystem
    return settings
