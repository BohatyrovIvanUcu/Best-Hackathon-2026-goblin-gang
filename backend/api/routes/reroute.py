from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import apply_reroute_request

router = APIRouter(tags=["reroute"])


class RerouteRequest(BaseModel):
    node_id: str
    product_id: str
    qty: float
    departure_time: str | None = None
    reroute_reason: str | None = None
    allow_in_progress: bool = True


@router.post("/reroute")
def reroute(payload: RerouteRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return apply_reroute_request(
            database_path=settings.database_path,
            node_id=payload.node_id,
            product_id=payload.product_id,
            qty=payload.qty,
            departure_time=payload.departure_time,
            reroute_reason=payload.reroute_reason,
            allow_in_progress=payload.allow_in_progress,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
