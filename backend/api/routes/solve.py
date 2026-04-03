from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import apply_urgent_request, run_solver_and_persist

router = APIRouter(tags=["solve"])


class SolveRequest(BaseModel):
    departure_time: str | None = None


class UrgentRequest(BaseModel):
    node_id: str
    product_id: str
    qty: float
    departure_time: str | None = None


@router.post("/solve")
def solve(payload: SolveRequest) -> dict[str, object]:
    settings = get_settings()
    return run_solver_and_persist(
        database_path=settings.database_path,
        departure_time=payload.departure_time,
    )


@router.post("/urgent")
def urgent(payload: UrgentRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return apply_urgent_request(
            database_path=settings.database_path,
            node_id=payload.node_id,
            product_id=payload.product_id,
            qty=payload.qty,
            departure_time=payload.departure_time,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
