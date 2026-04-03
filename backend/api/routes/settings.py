from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.config import get_settings
from backend.database import fetch_settings_data, update_settings_values

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings_endpoint() -> dict[str, dict[str, object]]:
    settings = get_settings()
    return fetch_settings_data(settings.database_path)


@router.put("/settings")
def update_settings_endpoint(payload: dict[str, object]) -> dict[str, object]:
    settings = get_settings()
    try:
        return update_settings_values(settings.database_path, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
