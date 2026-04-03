from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import fetch_stock_data, update_stock_after_shipment

router = APIRouter(tags=["stock"])


class StockUpdateRequest(BaseModel):
    warehouse_id: str
    product_id: str
    qty_shipped_kg: float


@router.get("/stock")
def get_stock() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return fetch_stock_data(settings.database_path)


@router.post("/stock/update")
def update_stock(payload: StockUpdateRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return update_stock_after_shipment(
            database_path=settings.database_path,
            warehouse_id=payload.warehouse_id,
            product_id=payload.product_id,
            qty_shipped_kg=payload.qty_shipped_kg,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
