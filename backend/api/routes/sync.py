from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import (
    apply_reroute_request,
    apply_urgent_request,
    complete_route_stop,
    complete_truck_loading,
    depart_truck,
    run_solver_and_persist,
    start_truck_loading,
    update_demand_current_stock,
    update_stock_after_shipment,
    update_truck_position,
)

router = APIRouter(tags=["sync"])


class BatchAction(BaseModel):
    action: str
    payload: dict[str, object]
    timestamp: str | None = None


class BatchRequest(BaseModel):
    actions: list[BatchAction]


@router.post("/actions/batch")
def apply_actions_batch(payload: BatchRequest) -> dict[str, object]:
    settings = get_settings()
    results: list[dict[str, object]] = []

    for index, action in enumerate(payload.actions):
        try:
            result = _apply_single_action(
                database_path=settings.database_path,
                action_name=action.action,
                action_payload=action.payload,
            )
            results.append(
                {
                    "index": index,
                    "action": action.action,
                    "timestamp": action.timestamp,
                    "status": "ok",
                    "result": result,
                }
            )
        except (LookupError, ValueError) as error:
            results.append(
                {
                    "index": index,
                    "action": action.action,
                    "timestamp": action.timestamp,
                    "status": "error",
                    "error": str(error),
                }
            )

    return {
        "status": "ok",
        "processed": len(results),
        "successful": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "error"),
        "results": results,
    }


def _apply_single_action(
    *,
    database_path,
    action_name: str,
    action_payload: dict[str, object],
) -> dict[str, object]:
    normalized_action = action_name.strip().lower()

    if normalized_action in {"stock.update", "stock_update", "/api/stock/update"}:
        return update_stock_after_shipment(
            database_path=database_path,
            warehouse_id=_require_str(action_payload, "warehouse_id"),
            product_id=_require_str(action_payload, "product_id"),
            qty_shipped_kg=_require_float(action_payload, "qty_shipped_kg"),
        )

    if normalized_action in {"demand.update", "demand_update", "/api/demand/update"}:
        return update_demand_current_stock(
            database_path=database_path,
            node_id=_require_str(action_payload, "node_id"),
            product_id=_require_str(action_payload, "product_id"),
            current_stock=_require_float(action_payload, "current_stock"),
        )

    if normalized_action in {"urgent", "/api/urgent"}:
        return apply_urgent_request(
            database_path=database_path,
            node_id=_require_str(action_payload, "node_id"),
            product_id=_require_str(action_payload, "product_id"),
            qty=_require_float(action_payload, "qty"),
            departure_time=_optional_str(action_payload, "departure_time"),
        )

    if normalized_action in {"reroute", "/api/reroute"}:
        return apply_reroute_request(
            database_path=database_path,
            node_id=_require_str(action_payload, "node_id"),
            product_id=_require_str(action_payload, "product_id"),
            qty=_require_float(action_payload, "qty"),
            departure_time=_optional_str(action_payload, "departure_time"),
            reroute_reason=_optional_str(action_payload, "reroute_reason"),
            allow_in_progress=_optional_bool(action_payload, "allow_in_progress", default=True),
        )

    if normalized_action in {"solve", "/api/solve"}:
        return run_solver_and_persist(
            database_path=database_path,
            departure_time=_optional_str(action_payload, "departure_time"),
        )

    if normalized_action in {"truck.loading.start", "/api/trucks/loading/start"}:
        return start_truck_loading(
            database_path=database_path,
            truck_id=_require_str(action_payload, "truck_id"),
            updated_at=_optional_str(action_payload, "updated_at"),
        )

    if normalized_action in {"truck.loading.complete", "/api/trucks/loading/complete"}:
        return complete_truck_loading(
            database_path=database_path,
            truck_id=_require_str(action_payload, "truck_id"),
            updated_at=_optional_str(action_payload, "updated_at"),
        )

    if normalized_action in {"truck.depart", "/api/trucks/depart"}:
        return depart_truck(
            database_path=database_path,
            truck_id=_require_str(action_payload, "truck_id"),
            updated_at=_optional_str(action_payload, "updated_at"),
        )

    if normalized_action in {"truck.position", "/api/trucks/position"}:
        return update_truck_position(
            database_path=database_path,
            truck_id=_require_str(action_payload, "truck_id"),
            current_lat=_require_float(action_payload, "current_lat"),
            current_lon=_require_float(action_payload, "current_lon"),
            current_node_id=_optional_str(action_payload, "current_node_id"),
            updated_at=_optional_str(action_payload, "updated_at"),
        )

    if normalized_action in {"route.stop_complete", "/api/routes/stop-complete"}:
        return complete_route_stop(
            database_path=database_path,
            route_id=_require_int(action_payload, "route_id"),
            completed_at=_optional_str(action_payload, "completed_at"),
        )

    raise ValueError(f"Unsupported batch action: {action_name}")


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Field '{key}' must be a non-empty string")
    return value


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Field '{key}' must be a string")
    return value


def _require_float(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"Field '{key}' must be numeric")
    return float(value)


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Field '{key}' must be an integer")
    return value


def _optional_bool(payload: dict[str, object], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"Field '{key}' must be a boolean")
    return value
