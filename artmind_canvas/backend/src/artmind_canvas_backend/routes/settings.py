"""Global UI settings endpoints (ADR 0015) — panel geometry, not board state."""

from fastapi import APIRouter

from artmind_canvas_backend.settings_store import (
    Settings,
    SettingsPatch,
    get_settings,
    save_settings,
)

router = APIRouter(prefix="/api/settings")


@router.get("")
async def read_settings() -> Settings:
    return get_settings()


@router.put("")
async def write_settings(patch: SettingsPatch) -> Settings:
    return save_settings(patch)
