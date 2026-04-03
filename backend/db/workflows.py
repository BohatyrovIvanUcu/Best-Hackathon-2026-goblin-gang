from __future__ import annotations

from datetime import datetime
from pathlib import Path

from backend.db.helpers import connect
from backend.db.read_api import fetch_routes_data
from backend.db.solver_runtime import run_solver_and_persist


def mark_demand_as_urgent(
    database_path: Path,
    *,
    node_id: str,
    product_id: str,
    qty: float,
) -> dict[str, object]:
    if qty <= 0:
        raise ValueError("qty must be greater than 0")

    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT node_id, product_id, current_stock, min_stock, requested_qty, is_urgent
            FROM demand
            WHERE node_id = ? AND product_id = ?
            """,
            (node_id, product_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Demand row not found for node_id={node_id} and product_id={product_id}"
            )

        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        requested_qty_before = float(row["requested_qty"])
        requested_qty_after = round(requested_qty_before + qty, 2)

        connection.execute(
            """
            UPDATE demand
            SET requested_qty = ?, priority = 'CRITICAL', is_urgent = 1, updated_at = ?
            WHERE node_id = ? AND product_id = ?
            """,
            (
                requested_qty_after,
                updated_at,
                node_id,
                product_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "node_id": node_id,
        "product_id": product_id,
        "qty": qty,
        "requested_qty_before": requested_qty_before,
        "requested_qty_after": requested_qty_after,
        "priority": "CRITICAL",
        "is_urgent": True,
    }


def apply_urgent_request(
    database_path: Path,
    *,
    node_id: str,
    product_id: str,
    qty: float,
    departure_time: str | None = None,
) -> dict[str, object]:
    before_routes = fetch_routes_data(database_path)["routes"]
    urgent_update = mark_demand_as_urgent(
        database_path,
        node_id=node_id,
        product_id=product_id,
        qty=qty,
    )
    solve_result = run_solver_and_persist(
        database_path=database_path,
        departure_time=departure_time,
    )
    after_routes = solve_result["routes"]

    return {
        "status": "ok",
        "urgent_update": urgent_update,
        "diff": _build_route_diff(before_routes, after_routes),
        "solve": solve_result,
    }


def _build_route_diff(
    before_routes: list[dict[str, object]],
    after_routes: list[dict[str, object]],
) -> dict[str, object]:
    before_map = {
        str(route["truck_id"]): (
            int(route["leg"]),
            tuple(route["stops"]),
        )
        for route in before_routes
    }
    after_map = {
        str(route["truck_id"]): (
            int(route["leg"]),
            tuple(route["stops"]),
        )
        for route in after_routes
    }

    before_ids = set(before_map)
    after_ids = set(after_map)
    changed_trucks = sorted(
        truck_id
        for truck_id in before_ids & after_ids
        if before_map[truck_id] != after_map[truck_id]
    )

    return {
        "before_total_routes": len(before_routes),
        "after_total_routes": len(after_routes),
        "added_trucks": sorted(after_ids - before_ids),
        "removed_trucks": sorted(before_ids - after_ids),
        "changed_trucks": changed_trucks,
    }
