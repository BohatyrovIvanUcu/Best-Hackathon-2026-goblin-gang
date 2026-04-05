from __future__ import annotations

from fastapi import APIRouter

from backend.config import get_settings
from backend.database import fetch_truck_positions_data

router = APIRouter(tags=["trucks"])


@router.get("/trucks/positions")
def get_truck_positions() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return fetch_truck_positions_data(settings.database_path)
