from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import (
    fetch_demand_data,
    update_demand_current_stock,
    update_store_priority_override,
)

router = APIRouter(tags=["demand"])


class DemandUpdateRequest(BaseModel):
    node_id: str
    product_id: str
    current_stock: float


class StorePriorityUpdateRequest(BaseModel):
    priority: str | None = None


@router.get("/demand")
def get_demand(
    priority: str | None = Query(default=None),
) -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    normalized_priority = priority.upper() if priority else None
    return fetch_demand_data(settings.database_path, priority=normalized_priority)


@router.post("/demand/update")
def update_demand(payload: DemandUpdateRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return update_demand_current_stock(
            database_path=settings.database_path,
            node_id=payload.node_id,
            product_id=payload.product_id,
            current_stock=payload.current_stock,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/stores/{node_id}/priority")
def update_store_priority(node_id: str, payload: StorePriorityUpdateRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return update_store_priority_override(
            database_path=settings.database_path,
            node_id=node_id,
            priority=payload.priority,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
