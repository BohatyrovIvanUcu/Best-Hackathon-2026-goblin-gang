from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings
from backend.database import fetch_route_by_truck_id, fetch_route_execution_data, fetch_routes_data

router = APIRouter(tags=["routes"])


@router.get("/routes")
def get_routes(
    leg: int | None = Query(default=None),
) -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return fetch_routes_data(settings.database_path, leg=leg)


@router.get("/routes/{truck_id}")
def get_route_by_truck_id(truck_id: str) -> dict[str, object]:
    settings = get_settings()
    try:
        return fetch_route_by_truck_id(settings.database_path, truck_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/routes/{route_id}/execution")
def get_route_execution(route_id: int) -> dict[str, object]:
    settings = get_settings()
    try:
        return fetch_route_execution_data(settings.database_path, route_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
