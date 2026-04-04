from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import (
    complete_route_stop,
    complete_truck_loading,
    depart_truck,
    start_truck_loading,
    update_truck_position,
)

router = APIRouter(tags=["execution"])


class ExecutionEventRequest(BaseModel):
    updated_at: str | None = None


class PositionUpdateRequest(BaseModel):
    current_lat: float
    current_lon: float
    current_node_id: str | None = None
    updated_at: str | None = None


class StopCompleteRequest(BaseModel):
    completed_at: str | None = None


@router.post("/trucks/{truck_id}/loading/start")
def loading_start(truck_id: str, payload: ExecutionEventRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return start_truck_loading(
            settings.database_path,
            truck_id=truck_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/trucks/{truck_id}/loading/complete")
def loading_complete(truck_id: str, payload: ExecutionEventRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return complete_truck_loading(
            settings.database_path,
            truck_id=truck_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/trucks/{truck_id}/depart")
def depart(truck_id: str, payload: ExecutionEventRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return depart_truck(
            settings.database_path,
            truck_id=truck_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/trucks/{truck_id}/position")
def update_position(truck_id: str, payload: PositionUpdateRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return update_truck_position(
            settings.database_path,
            truck_id=truck_id,
            current_lat=payload.current_lat,
            current_lon=payload.current_lon,
            current_node_id=payload.current_node_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/routes/{route_id}/stop-complete")
def stop_complete(route_id: int, payload: StopCompleteRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return complete_route_stop(
            settings.database_path,
            route_id=route_id,
            completed_at=payload.completed_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
