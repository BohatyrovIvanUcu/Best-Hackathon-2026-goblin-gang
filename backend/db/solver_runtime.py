from __future__ import annotations

import time as time_module
from pathlib import Path

from solver.graph import build_graph_from_edges
from solver.io import DemandRecord, Edge, Node, Product, SolverInputs, Truck, WarehouseStockRecord
from solver.routing import SolveResult, solve_network

from backend.db.helpers import connect, parse_datetime_value, parse_setting_value


def run_solver_and_persist(
    database_path: Path,
    departure_time: str | None = None,
) -> dict[str, object]:
    started_at = time_module.perf_counter()
    solver_inputs = load_solver_inputs_from_db(database_path)
    solve_result = solve_network(
        solver_inputs,
        departure_time_override=departure_time,
    )
    persisted = persist_solve_result(database_path, solve_result)
    solve_time_ms = int((time_module.perf_counter() - started_at) * 1000)
    response = build_solve_response(
        database_path=database_path,
        solve_result=solve_result,
        persisted_routes=persisted,
    )
    response["status"] = "ok"
    response["solve_time_ms"] = solve_time_ms
    response["enroute_suggestions"] = []
    return response


def load_solver_inputs_from_db(database_path: Path) -> SolverInputs:
    connection = connect(database_path)
    try:
        nodes = {
            row["id"]: Node(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                capacity_kg=row["capacity_kg"],
                lat=row["lat"],
                lon=row["lon"],
            )
            for row in connection.execute(
                "SELECT id, name, type, capacity_kg, lat, lon FROM nodes ORDER BY id"
            )
        }
        edges = [
            Edge(
                from_id=row["from_id"],
                to_id=row["to_id"],
                distance_km=row["distance_km"],
            )
            for row in connection.execute(
                "SELECT from_id, to_id, distance_km FROM edges ORDER BY from_id, to_id"
            )
        ]
        settings = {
            row["key"]: parse_setting_value(row["key"], row["value"])
            for row in connection.execute(
                "SELECT key, value FROM settings ORDER BY key"
            )
        }
        trucks = {
            row["id"]: Truck(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                capacity_kg=row["capacity_kg"],
                fuel_per_100km=row["fuel_per_100km"],
                depot_node_id=row["depot_node_id"],
                driver_hourly=row["driver_hourly"],
                avg_speed_kmh=row["avg_speed_kmh"],
                amortization_per_km=row["amortization_per_km"],
                maintenance_per_km=row["maintenance_per_km"],
            )
            for row in connection.execute(
                """
                SELECT
                    id,
                    name,
                    type,
                    capacity_kg,
                    fuel_per_100km,
                    depot_node_id,
                    driver_hourly,
                    avg_speed_kmh,
                    amortization_per_km,
                    maintenance_per_km
                FROM trucks
                ORDER BY id
                """
            )
        }
        warehouse_stock = {
            (row["warehouse_id"], row["product_id"]): WarehouseStockRecord(
                warehouse_id=row["warehouse_id"],
                product_id=row["product_id"],
                quantity_kg=row["quantity_kg"],
                reserved_kg=row["reserved_kg"],
            )
            for row in connection.execute(
                """
                SELECT warehouse_id, product_id, quantity_kg, reserved_kg
                FROM warehouse_stock
                ORDER BY warehouse_id, product_id
                """
            )
        }
        demand = {
            (row["node_id"], row["product_id"]): DemandRecord(
                node_id=row["node_id"],
                product_id=row["product_id"],
                current_stock=row["current_stock"],
                min_stock=row["min_stock"],
                requested_qty=row["requested_qty"],
                priority=row["priority"],
                is_urgent=bool(row["is_urgent"]),
                updated_at=parse_datetime_value(row["updated_at"]),
            )
            for row in connection.execute(
                """
                SELECT
                    node_id,
                    product_id,
                    current_stock,
                    min_stock,
                    requested_qty,
                    priority,
                    is_urgent,
                    updated_at
                FROM demand
                ORDER BY node_id, product_id
                """
            )
        }
        products = {
            row["id"]: Product(
                id=row["id"],
                name=row["name"],
                weight_kg=row["weight_kg"],
                length_cm=row["length_cm"],
                width_cm=row["width_cm"],
                height_cm=row["height_cm"],
            )
            for row in connection.execute(
                """
                SELECT id, name, weight_kg, length_cm, width_cm, height_cm
                FROM products
                ORDER BY id
                """
            )
        }
    finally:
        connection.close()

    return SolverInputs(
        nodes=nodes,
        edges=edges,
        graph=build_graph_from_edges(edges),
        trucks=trucks,
        demand=demand,
        warehouse_stock=warehouse_stock,
        products=products,
        settings=settings,
    )


def persist_solve_result(
    database_path: Path,
    solve_result: SolveResult,
) -> list[dict[str, object]]:
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        connection.execute("UPDATE routes SET is_active = 0 WHERE is_active = 1")

        new_routes: list[dict[str, object]] = []
        route_id_map: dict[int, int] = {}
        for route_row in solve_result.routes_table:
            cursor = connection.execute(
                """
                INSERT INTO routes(
                    truck_id,
                    leg,
                    stops,
                    total_km,
                    total_cost,
                    drive_hours,
                    total_elapsed_h,
                    days,
                    departure_time,
                    arrival_time,
                    time_status,
                    time_warning,
                    timeline,
                    created_at,
                    is_active
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    route_row["truck_id"],
                    route_row["leg"],
                    route_row["stops"],
                    route_row["total_km"],
                    route_row["total_cost"],
                    route_row["drive_hours"],
                    route_row["total_elapsed_h"],
                    route_row["days"],
                    route_row["departure_time"],
                    route_row["arrival_time"],
                    route_row["time_status"],
                    route_row["time_warning"],
                    route_row["timeline"],
                    route_row["created_at"],
                    route_row["is_active"],
                ),
            )
            new_id = int(cursor.lastrowid)
            route_id_map[int(route_row["id"])] = new_id
            new_routes.append({"old_id": int(route_row["id"]), "id": new_id})

        for cargo_row in solve_result.route_cargo_table:
            connection.execute(
                """
                INSERT INTO route_cargo(route_id, stop_node_id, product_id, qty_kg)
                VALUES(?, ?, ?, ?)
                """,
                (
                    route_id_map[int(cargo_row["route_id"])],
                    cargo_row["stop_node_id"],
                    cargo_row["product_id"],
                    cargo_row["qty_kg"],
                ),
            )

        connection.executemany(
            """
            UPDATE warehouse_stock
            SET reserved_kg = ?
            WHERE warehouse_id = ? AND product_id = ?
            """,
            [
                (
                    row["reserved_kg"],
                    row["warehouse_id"],
                    row["product_id"],
                )
                for row in solve_result.warehouse_stock_table
            ],
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return new_routes


def build_solve_response(
    database_path: Path,
    solve_result: SolveResult,
    persisted_routes: list[dict[str, object]],
) -> dict[str, object]:
    connection = connect(database_path)
    try:
        node_names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM nodes")
        }
        product_names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM products")
        }
        truck_rows = {
            row["id"]: dict(row)
            for row in connection.execute("SELECT id, name, type FROM trucks")
        }
    finally:
        connection.close()

    persisted_ids = {item["old_id"]: item["id"] for item in persisted_routes}
    route_plans = list(solve_result.route_plans.values())
    response_routes: list[dict[str, object]] = []

    for old_route_id, route_plan in enumerate(route_plans, start=1):
        truck = truck_rows.get(route_plan.truck_id, {})
        cargo_items = []
        for (stop_node_id, product_id), qty_kg in sorted(
            route_plan.cargo_by_stop_product.items(),
            key=lambda item: (item[0][0], item[0][1]),
        ):
            cargo_item = {
                "product_id": product_id,
                "product_name": product_names.get(product_id, product_id),
                "qty_kg": round(qty_kg, 2),
            }
            if route_plan.leg == 2:
                cargo_item["delivery_node"] = stop_node_id
            cargo_items.append(cargo_item)

        response_routes.append(
            {
                "id": persisted_ids.get(old_route_id, old_route_id),
                "truck_id": route_plan.truck_id,
                "truck_name": truck.get("name", route_plan.truck_id),
                "truck_type": truck.get("type", ""),
                "leg": route_plan.leg,
                "stops": list(route_plan.stops),
                "stops_names": [node_names.get(node_id, node_id) for node_id in route_plan.stops],
                "total_km": route_plan.total_km,
                "total_cost": route_plan.total_cost,
                "departure_time": route_plan.departure_time,
                "arrival_time": route_plan.arrival_time,
                "drive_hours": route_plan.drive_hours,
                "total_elapsed_h": route_plan.total_elapsed_h,
                "days": route_plan.days,
                "time_status": route_plan.time_status,
                "time_warning": route_plan.time_warning,
                "timeline": [
                    {
                        "time": event.time,
                        "event": event.event,
                        "node_id": event.node_id,
                        "note": event.note,
                    }
                    for event in route_plan.timeline
                ],
                "cargo": cargo_items,
            }
        )

    summary = build_solve_summary(solve_result, response_routes)
    return {
        "routes": response_routes,
        "summary": summary,
    }


def build_solve_summary(
    solve_result: SolveResult,
    response_routes: list[dict[str, object]],
) -> dict[str, object]:
    assigned_store_ids = {
        item.order.store_id for item in solve_result.assignment.assigned_orders
    }
    unassigned_store_ids = {
        item.store_id for item in solve_result.assignment.unassigned_orders
    }
    all_store_ids = sorted(assigned_store_ids | unassigned_store_ids)
    critical_assigned_store_ids = {
        item.order.store_id
        for item in solve_result.assignment.assigned_orders
        if item.order.priority == "CRITICAL"
    }
    critical_unassigned_store_ids = {
        item.store_id
        for item in solve_result.assignment.unassigned_orders
        if item.priority == "CRITICAL"
    }
    critical_store_ids = critical_assigned_store_ids | critical_unassigned_store_ids

    return {
        "total_routes": len(response_routes),
        "total_km": round(sum(float(route["total_km"]) for route in response_routes), 2),
        "total_cost": round(sum(float(route["total_cost"]) for route in response_routes), 2),
        "stores_covered": len(assigned_store_ids),
        "stores_total": len(all_store_ids),
        "stores_uncovered": sorted(unassigned_store_ids),
        "critical_covered": len(critical_assigned_store_ids),
        "critical_total": len(critical_store_ids),
    }
