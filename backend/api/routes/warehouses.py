from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import (
    fetch_warehouse_dashboard_data,
    fetch_warehouses_data,
    issue_outbound_route_item,
    mark_inbound_route_arrived,
    receive_inbound_route,
)

router = APIRouter(tags=["warehouses"])


class WorkerExecutionRequest(BaseModel):
    updated_at: str | None = None


class OutboundIssueItemRequest(BaseModel):
    stop_node_id: str
    product_id: str
    updated_at: str | None = None


@router.get("/warehouses")
def get_warehouses() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return fetch_warehouses_data(settings.database_path)


@router.get("/warehouses/{warehouse_id}/dashboard")
def get_warehouse_dashboard(warehouse_id: str) -> dict[str, object]:
    settings = get_settings()
    try:
        return fetch_warehouse_dashboard_data(settings.database_path, warehouse_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/warehouses/{warehouse_id}/inbound/{route_id}/arrive")
def inbound_arrive(
    warehouse_id: str,
    route_id: int,
    payload: WorkerExecutionRequest,
) -> dict[str, object]:
    settings = get_settings()
    try:
        return mark_inbound_route_arrived(
            settings.database_path,
            warehouse_id=warehouse_id,
            route_id=route_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/warehouses/{warehouse_id}/inbound/{route_id}/receive")
def inbound_receive(
    warehouse_id: str,
    route_id: int,
    payload: WorkerExecutionRequest,
) -> dict[str, object]:
    settings = get_settings()
    try:
        return receive_inbound_route(
            settings.database_path,
            warehouse_id=warehouse_id,
            route_id=route_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/warehouses/{warehouse_id}/outbound/{route_id}/issue-item")
def outbound_issue_item(
    warehouse_id: str,
    route_id: int,
    payload: OutboundIssueItemRequest,
) -> dict[str, object]:
    settings = get_settings()
    try:
        return issue_outbound_route_item(
            settings.database_path,
            warehouse_id=warehouse_id,
            route_id=route_id,
            stop_node_id=payload.stop_node_id,
            product_id=payload.product_id,
            updated_at=payload.updated_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
