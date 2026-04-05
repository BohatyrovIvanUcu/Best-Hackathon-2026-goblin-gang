from __future__ import annotations

from fastapi import APIRouter

from backend.config import get_settings
from backend.database import fetch_network_data

router = APIRouter(tags=["network"])


@router.get("/network")
def get_network() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return fetch_network_data(settings.database_path)
