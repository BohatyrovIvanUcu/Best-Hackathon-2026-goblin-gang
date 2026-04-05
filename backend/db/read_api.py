from __future__ import annotations

import json
from pathlib import Path

from backend.db.execution import _fetch_route_execution_details
from backend.db.helpers import connect, parse_setting_value


def fetch_network_data(database_path: Path) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        nodes = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    n.id,
                    n.name,
                    n.type,
                    n.capacity_kg,
                    n.lat,
                    n.lon,
                    store_demand.priority,
                    store_demand.current_stock,
                    store_demand.min_stock
                FROM nodes AS n
                LEFT JOIN (
                    SELECT node_id, priority, current_stock, min_stock
                    FROM (
                        SELECT
                            d.node_id,
                            d.priority,
                            d.current_stock,
                            d.min_stock,
                            ROW_NUMBER() OVER (
                                PARTITION BY d.node_id
                                ORDER BY
                                    CASE d.priority
                                        WHEN 'CRITICAL' THEN 3
                                        WHEN 'ELEVATED' THEN 2
                                        ELSE 1
                                    END DESC,
                                    d.requested_qty DESC,
                                    d.product_id ASC
                            ) AS row_num
                        FROM demand AS d
                    )
                    WHERE row_num = 1
                ) AS store_demand
                    ON store_demand.node_id = n.id
                ORDER BY
                    CASE n.type
                        WHEN 'factory' THEN 1
                        WHEN 'warehouse' THEN 2
                        ELSE 3
                    END,
                    n.id
                """
            )
        ]
        edges = [
            dict(row)
            for row in connection.execute(
                """
                SELECT from_id, to_id, distance_km
                FROM edges
                ORDER BY from_id, to_id
                """
            )
        ]
    finally:
        connection.close()

    return {"nodes": nodes, "edges": edges}


def fetch_stock_data(database_path: Path) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        stock_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    ws.warehouse_id,
                    n.name AS warehouse_name,
                    ws.product_id,
                    p.name AS product_name,
                    ws.quantity_kg,
                    ws.reserved_kg,
                    ROUND(ws.quantity_kg - ws.reserved_kg, 2) AS available_kg
                FROM warehouse_stock AS ws
                JOIN nodes AS n ON n.id = ws.warehouse_id
                JOIN products AS p ON p.id = ws.product_id
                ORDER BY ws.warehouse_id, ws.product_id
                """
            )
        ]
    finally:
        connection.close()

    return {"stock": stock_rows}


def fetch_warehouses_data(database_path: Path) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        warehouses = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, name, type, capacity_kg, lat, lon
                FROM nodes
                WHERE type = 'warehouse'
                ORDER BY name, id
                """
            )
        ]
    finally:
        connection.close()

    return {"warehouses": warehouses}


def fetch_warehouse_dashboard_data(
    database_path: Path,
    warehouse_id: str,
) -> dict[str, object]:
    connection = connect(database_path)
    try:
        warehouse_row = connection.execute(
            """
            SELECT id, name, type, capacity_kg, lat, lon
            FROM nodes
            WHERE id = ? AND type = 'warehouse'
            """,
            (warehouse_id,),
        ).fetchone()
        if warehouse_row is None:
            raise LookupError(f"Склад {warehouse_id} не знайдено")

        node_names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM nodes")
        }
        stock_rows = [
            {
                **dict(row),
                "is_low": float(row["available_kg"]) < 100,
            }
            for row in connection.execute(
                """
                SELECT
                    ws.warehouse_id,
                    ws.product_id,
                    p.name AS product_name,
                    ws.quantity_kg,
                    ws.reserved_kg,
                    ROUND(ws.quantity_kg - ws.reserved_kg, 2) AS available_kg
                FROM warehouse_stock AS ws
                JOIN products AS p ON p.id = ws.product_id
                WHERE ws.warehouse_id = ?
                ORDER BY p.name, ws.product_id
                """,
                (warehouse_id,),
            )
        ]

        active_route_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    r.id,
                    r.truck_id,
                    r.leg,
                    r.stops,
                    r.timeline,
                    r.total_km,
                    r.total_cost,
                    r.departure_time,
                    r.arrival_time,
                    t.name AS truck_name,
                    t.type AS truck_type,
                    re.status AS route_status,
                    re.last_completed_stop_index,
                    re.next_stop_index,
                    re.started_at,
                    re.warehouse_arrived_at,
                    re.warehouse_received_at,
                    re.completed_at,
                    ts.status AS truck_status,
                    ts.current_node_id,
                    ts.remaining_capacity_kg
                FROM routes AS r
                JOIN trucks AS t ON t.id = r.truck_id
                JOIN truck_state AS ts ON ts.truck_id = r.truck_id AND ts.active_route_id = r.id
                LEFT JOIN route_execution AS re ON re.route_id = r.id
                WHERE r.is_active = 1
                ORDER BY r.leg, r.id
                """
            )
        ]
        cargo_state_by_route = _fetch_route_cargo_state_grouped(
            connection,
            [int(row["id"]) for row in active_route_rows],
        )

        inbound: list[dict[str, object]] = []
        outbound: list[dict[str, object]] = []

        for route_row in active_route_rows:
            route_id = int(route_row["id"])
            stops = json.loads(route_row["stops"])
            timeline = json.loads(route_row["timeline"])
            route_cargo_rows = cargo_state_by_route.get(route_id, [])
            if int(route_row["leg"]) == 1:
                warehouse_stop_index = _find_warehouse_stop_index(stops, warehouse_id)
                if warehouse_stop_index is None or route_row["warehouse_received_at"] is not None:
                    continue
                pending_items = []
                for item in route_cargo_rows:
                    if item["stop_node_id"] != warehouse_id:
                        continue
                    qty_pending_kg = round(
                        max(
                            0.0,
                            float(item["qty_reserved_kg"]) + float(item["qty_loaded_kg"]) - float(item["qty_delivered_kg"]),
                        ),
                        2,
                    )
                    if qty_pending_kg <= 0:
                        continue
                    pending_items.append(
                        {
                            "product_id": item["product_id"],
                            "product_name": item["product_name"],
                            "qty_kg": qty_pending_kg,
                        }
                    )
                if not pending_items:
                    continue

                worker_status = _inbound_worker_status(route_row)
                inbound.append(
                    {
                        "route_id": route_id,
                        "truck_id": route_row["truck_id"],
                        "truck_name": route_row["truck_name"],
                        "truck_type": route_row["truck_type"],
                        "from_node_id": stops[warehouse_stop_index - 1] if warehouse_stop_index > 0 else None,
                        "from_node_name": node_names.get(stops[warehouse_stop_index - 1], stops[warehouse_stop_index - 1])
                        if warehouse_stop_index > 0
                        else None,
                        "scheduled_time": _scheduled_time_for_stop_index(stops, timeline, warehouse_stop_index),
                        "route_status": route_row["route_status"],
                        "truck_status": route_row["truck_status"],
                        "worker_status": worker_status,
                        "warehouse_arrived_at": route_row["warehouse_arrived_at"],
                        "warehouse_received_at": route_row["warehouse_received_at"],
                        "can_arrive": route_row["warehouse_arrived_at"] is None,
                        "can_receive": route_row["warehouse_arrived_at"] is not None
                        and route_row["warehouse_received_at"] is None,
                        "items": pending_items,
                    }
                )
                continue

            if int(route_row["leg"]) != 2 or not stops or str(stops[0]) != warehouse_id:
                continue

            total_reserved_kg = 0.0
            total_loaded_kg = 0.0
            outbound_items = []
            for item in route_cargo_rows:
                qty_reserved_kg = round(float(item["qty_reserved_kg"]), 2)
                qty_loaded_kg = round(float(item["qty_loaded_kg"]), 2)
                qty_delivered_kg = round(float(item["qty_delivered_kg"]), 2)
                total_reserved_kg += qty_reserved_kg
                total_loaded_kg += qty_loaded_kg
                if item["stop_node_id"] == warehouse_id:
                    continue
                outbound_items.append(
                    {
                        "stop_node_id": item["stop_node_id"],
                        "stop_node_name": item["stop_node_name"],
                        "product_id": item["product_id"],
                        "product_name": item["product_name"],
                        "qty_reserved_kg": qty_reserved_kg,
                        "qty_loaded_kg": qty_loaded_kg,
                        "qty_delivered_kg": qty_delivered_kg,
                        "is_issued": qty_reserved_kg <= 0,
                    }
                )

            outbound.append(
                {
                    "route_id": route_id,
                    "truck_id": route_row["truck_id"],
                    "truck_name": route_row["truck_name"],
                    "truck_type": route_row["truck_type"],
                    "route_status": route_row["route_status"],
                    "truck_status": route_row["truck_status"],
                    "worker_status": _outbound_worker_status(route_row),
                    "next_stop_name": node_names.get(
                        stops[int(route_row["next_stop_index"])] if route_row["next_stop_index"] is not None else None,
                        stops[int(route_row["next_stop_index"])] if route_row["next_stop_index"] is not None else None,
                    ),
                    "scheduled_departure": route_row["departure_time"],
                    "scheduled_arrival": route_row["arrival_time"],
                    "total_km": route_row["total_km"],
                    "total_cost": route_row["total_cost"],
                    "total_reserved_kg": round(total_reserved_kg, 2),
                    "total_loaded_kg": round(total_loaded_kg, 2),
                    "is_blocked": total_reserved_kg > 0,
                    "can_start_loading": route_row["truck_status"] == "idle" and route_row["route_status"] == "planned",
                    "can_complete_loading": route_row["truck_status"] == "loading"
                    and route_row["route_status"] == "loading"
                    and total_reserved_kg <= 0,
                    "can_depart": route_row["truck_status"] == "loaded" and route_row["route_status"] == "loading",
                    "items": outbound_items,
                }
            )
    finally:
        connection.close()

    alerts = _build_warehouse_alerts(
        warehouse_name=str(warehouse_row["name"]),
        stock_rows=stock_rows,
        inbound=inbound,
        outbound=outbound,
    )
    summary = {
        "inbound_count": len(inbound),
        "outbound_count": len(outbound),
        "low_stock_count": sum(1 for row in stock_rows if row["is_low"]),
        "waiting_receive_count": sum(1 for route in inbound if route["can_receive"]),
        "blocked_outbound_count": sum(1 for route in outbound if route["is_blocked"]),
    }

    return {
        "warehouse": dict(warehouse_row),
        "summary": summary,
        "inbound": inbound,
        "outbound": outbound,
        "stock": stock_rows,
        "alerts": alerts,
    }


def fetch_demand_data(
    database_path: Path,
    priority: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        query = """
            SELECT
                d.node_id,
                n.name AS node_name,
                d.product_id,
                p.name AS product_name,
                d.current_stock,
                d.min_stock,
                d.requested_qty,
                d.priority,
                d.manual_priority_override,
                d.is_urgent,
                d.updated_at
            FROM demand AS d
            JOIN nodes AS n ON n.id = d.node_id
            JOIN products AS p ON p.id = d.product_id
        """
        params: list[object] = []
        if priority is not None:
            query += " WHERE d.priority = ?"
            params.append(priority)
        query += """
            ORDER BY
                CASE d.priority
                    WHEN 'CRITICAL' THEN 3
                    WHEN 'ELEVATED' THEN 2
                    ELSE 1
                END DESC,
                d.requested_qty DESC,
                d.node_id,
                d.product_id
        """
        rows = []
        for row in connection.execute(query, params):
            item = dict(row)
            item["is_urgent"] = bool(item["is_urgent"])
            rows.append(item)
    finally:
        connection.close()

    return {"demand": rows}


def fetch_settings_data(database_path: Path) -> dict[str, dict[str, object]]:
    connection = connect(database_path)
    try:
        settings = {
            row["key"]: parse_setting_value(row["key"], row["value"])
            for row in connection.execute(
                "SELECT key, value FROM settings ORDER BY key"
            )
        }
    finally:
        connection.close()

    return {"settings": settings}


def fetch_routes_data(
    database_path: Path,
    leg: int | None = None,
) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        query = """
            SELECT
                r.id,
                r.truck_id,
                t.name AS truck_name,
                t.type AS truck_type,
                r.supersedes_route_id,
                r.leg,
                r.stops,
                r.total_km,
                r.total_cost,
                r.drive_hours,
                r.created_at,
                r.is_active,
                re.status AS route_status,
                re.last_completed_stop_index,
                re.next_stop_index,
                re.started_at,
                re.warehouse_arrived_at,
                re.warehouse_received_at,
                re.completed_at,
                ts.status AS truck_status,
                ts.active_route_id,
                ts.current_node_id,
                ts.current_lat,
                ts.current_lon,
                ts.remaining_capacity_kg,
                ts.updated_at AS truck_updated_at
            FROM routes AS r
            JOIN trucks AS t ON t.id = r.truck_id
            LEFT JOIN route_execution AS re ON re.route_id = r.id
            LEFT JOIN truck_state AS ts ON ts.truck_id = r.truck_id
            WHERE r.is_active = 1
        """
        params: list[object] = []
        if leg is not None:
            query += " AND r.leg = ?"
            params.append(leg)
        query += " ORDER BY r.leg, r.id"

        node_names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM nodes")
        }
        settings = {
            row["key"]: parse_setting_value(row["key"], row["value"])
            for row in connection.execute("SELECT key, value FROM settings ORDER BY key")
        }
        routes = []
        for row in connection.execute(query, params):
            stops = json.loads(row["stops"])
            next_stop_index = row["next_stop_index"]
            current_stop_node_id = (
                stops[int(next_stop_index)]
                if next_stop_index is not None and int(next_stop_index) < len(stops)
                else None
            )
            routes.append(
                {
                    "id": row["id"],
                    "truck_id": row["truck_id"],
                    "truck_name": row["truck_name"],
                    "truck_type": row["truck_type"],
                    "supersedes_route_id": row["supersedes_route_id"],
                    "leg": row["leg"],
                    "stops": stops,
                    "stops_names": [node_names.get(node_id, node_id) for node_id in stops],
                    "total_km": row["total_km"],
                    "total_cost": row["total_cost"],
                    "estimated_hours": row["drive_hours"],
                    "created_at": row["created_at"],
                    "is_active": bool(row["is_active"]),
                    "execution": {
                        "route_status": row["route_status"],
                        "last_completed_stop_index": row["last_completed_stop_index"],
                        "next_stop_index": next_stop_index,
                        "started_at": row["started_at"],
                        "warehouse_arrived_at": row["warehouse_arrived_at"],
                        "warehouse_received_at": row["warehouse_received_at"],
                        "completed_at": row["completed_at"],
                        "locked_prefix": stops[: int(row["last_completed_stop_index"] or 0) + 1],
                        "current_stop_node_id": current_stop_node_id,
                        "current_stop_name": node_names.get(current_stop_node_id, current_stop_node_id)
                        if current_stop_node_id is not None
                        else None,
                        "is_current_route_for_truck": row["active_route_id"] == row["id"],
                        "truck_state": {
                            "status": row["truck_status"],
                            "active_route_id": row["active_route_id"],
                            "current_node_id": row["current_node_id"],
                            "current_node_name": node_names.get(row["current_node_id"], row["current_node_id"])
                            if row["current_node_id"] is not None
                            else None,
                            "current_lat": row["current_lat"],
                            "current_lon": row["current_lon"],
                            "remaining_capacity_kg": row["remaining_capacity_kg"],
                            "updated_at": row["truck_updated_at"],
                        },
                    },
                }
            )
    finally:
        connection.close()

    return {
        "routes": routes,
        "cost_summary": _build_cost_summary(routes, settings),
    }


def fetch_route_by_truck_id(
    database_path: Path,
    truck_id: str,
) -> dict[str, object]:
    connection = connect(database_path)
    try:
        truck_row = connection.execute(
            """
            SELECT id, name, type, depot_node_id
            FROM trucks
            WHERE id = ?
            """,
            (truck_id,),
        ).fetchone()
        if truck_row is None:
            raise LookupError(f"Вантажівку {truck_id} не знайдено або маршрут ще не побудовано")

        active_route_row = connection.execute(
            """
            SELECT active_route_id
            FROM truck_state
            WHERE truck_id = ?
            """,
            (truck_id,),
        ).fetchone()
        active_route_id = (
            int(active_route_row["active_route_id"])
            if active_route_row is not None and active_route_row["active_route_id"] is not None
            else None
        )

        if active_route_id is not None:
            route_row = connection.execute(
                """
                SELECT
                    id,
                    truck_id,
                    supersedes_route_id,
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
                    created_at
                FROM routes
                WHERE id = ? AND is_active = 1
                """,
                (active_route_id,),
            ).fetchone()
        else:
            route_row = None

        if route_row is None:
            route_row = connection.execute(
            """
            SELECT
                id,
                truck_id,
                supersedes_route_id,
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
                created_at
            FROM routes
            WHERE truck_id = ? AND is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (truck_id,),
        ).fetchone()
        if route_row is None:
            raise LookupError(f"Вантажівку {truck_id} не знайдено або маршрут ще не побудовано")

        node_rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id, name, type, lat, lon FROM nodes"
            )
        }
        product_names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM products")
        }
        priority_map = {
            row["node_id"]: row["priority"]
            for row in connection.execute(
                """
                SELECT node_id, priority
                FROM (
                    SELECT
                        node_id,
                        priority,
                        ROW_NUMBER() OVER (
                            PARTITION BY node_id
                            ORDER BY
                                CASE priority
                                    WHEN 'CRITICAL' THEN 3
                                    WHEN 'ELEVATED' THEN 2
                                    ELSE 1
                                END DESC,
                                requested_qty DESC,
                                product_id ASC
                        ) AS row_num
                    FROM demand
                )
                WHERE row_num = 1
                """
            )
        }
        cargo_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT product_id, stop_node_id, qty_kg
                FROM route_cargo
                WHERE route_id = ?
                ORDER BY stop_node_id, product_id
                """,
                (route_row["id"],),
            )
        ]
    finally:
        connection.close()

    stops = json.loads(route_row["stops"])
    timeline = json.loads(route_row["timeline"])
    stop_details = _build_route_stop_details(
        stops=stops,
        timeline=timeline,
        node_rows=node_rows,
        product_names=product_names,
        priority_map=priority_map,
        cargo_rows=cargo_rows,
    )

    return {
        "truck_id": truck_row["id"],
        "truck_name": truck_row["name"],
        "truck_type": truck_row["type"],
        "depot_node_id": truck_row["depot_node_id"],
        "route": {
            "id": route_row["id"],
            "supersedes_route_id": route_row["supersedes_route_id"],
            "leg": route_row["leg"],
            "stops": stops,
            "stops_details": stop_details,
            "total_km": route_row["total_km"],
            "total_cost": route_row["total_cost"],
            "departure_time": route_row["departure_time"],
            "arrival_time": route_row["arrival_time"],
            "drive_hours": route_row["drive_hours"],
            "total_elapsed_h": route_row["total_elapsed_h"],
            "days": route_row["days"],
            "time_status": route_row["time_status"],
            "time_warning": route_row["time_warning"],
            "created_at": route_row["created_at"],
            "execution": fetch_route_execution_data(database_path, int(route_row["id"])),
        },
    }


def fetch_route_execution_data(
    database_path: Path,
    route_id: int,
) -> dict[str, object]:
    connection = connect(database_path)
    try:
        return _fetch_route_execution_details(connection, route_id)
    finally:
        connection.close()


def _fetch_route_cargo_state_grouped(
    connection,
    route_ids: list[int],
) -> dict[int, list[dict[str, object]]]:
    if not route_ids:
        return {}

    placeholders = ", ".join(["?"] * len(route_ids))
    rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT
                rcs.route_id,
                rcs.stop_node_id,
                COALESCE(n.name, rcs.stop_node_id) AS stop_node_name,
                rcs.product_id,
                COALESCE(p.name, rcs.product_id) AS product_name,
                rcs.qty_reserved_kg,
                rcs.qty_loaded_kg,
                rcs.qty_delivered_kg
            FROM route_cargo_state AS rcs
            LEFT JOIN nodes AS n ON n.id = rcs.stop_node_id
            LEFT JOIN products AS p ON p.id = rcs.product_id
            WHERE rcs.route_id IN ({placeholders})
            ORDER BY rcs.route_id, rcs.stop_node_id, rcs.product_id
            """,
            route_ids,
        )
    ]
    grouped: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(int(row["route_id"]), []).append(row)
    return grouped


def _build_cost_summary(
    routes: list[dict[str, object]],
    settings: dict[str, object],
) -> dict[str, object]:
    total_km = round(sum(float(route["total_km"]) for route in routes), 2)
    estimated_total_cost = round(sum(float(route["total_cost"]) for route in routes), 2)

    fuel_price = float(settings.get("fuel_price", 0.0) or 0.0)
    driver_hourly = float(settings.get("driver_hourly_default", 0.0) or 0.0)
    avg_speed = float(settings.get("avg_speed_default", 1.0) or 1.0)
    amortization = float(settings.get("amortization_default", 0.0) or 0.0)
    maintenance = float(settings.get("maintenance_default", 0.0) or 0.0)

    # The main-screen explainer uses a simple “typical route truck” baseline.
    baseline_fuel_per_100km = 22.0
    fuel_cost_per_km = round((baseline_fuel_per_100km * fuel_price) / 100.0, 2)
    driver_cost_per_km = round(driver_hourly / avg_speed, 2) if avg_speed > 0 else 0.0
    total_cost_per_km = round(
        fuel_cost_per_km + driver_cost_per_km + amortization + maintenance,
        2,
    )

    return {
        "formula_inputs": {
            "fuel_price": fuel_price,
            "driver_hourly_default": driver_hourly,
            "avg_speed_default": avg_speed,
            "amortization_default": amortization,
            "maintenance_default": maintenance,
            "baseline_fuel_per_100km": baseline_fuel_per_100km,
        },
        "per_km": {
            "fuel_cost_per_km": fuel_cost_per_km,
            "driver_cost_per_km": driver_cost_per_km,
            "amortization_per_km": amortization,
            "maintenance_per_km": maintenance,
            "total_cost_per_km": total_cost_per_km,
        },
        "totals": {
            "total_km": total_km,
            "estimated_total_cost": estimated_total_cost,
        },
    }


def _find_warehouse_stop_index(stops: list[str], warehouse_id: str) -> int | None:
    for index, stop_id in enumerate(stops):
        if index == 0:
            continue
        if str(stop_id) == warehouse_id:
            return index
    return None


def _scheduled_time_for_stop_index(
    stops: list[str],
    timeline: list[dict[str, object]],
    stop_index: int,
) -> str | None:
    matched = _match_timeline_events_to_stops(stops, timeline)
    if stop_index >= len(matched):
        return None
    event = matched[stop_index]
    return str(event.get("time")) if event and event.get("time") is not None else None


def _inbound_worker_status(route_row: dict[str, object]) -> str:
    if route_row["warehouse_received_at"] is not None:
        return "Прийнята"
    if route_row["warehouse_arrived_at"] is not None:
        return "Очікує приймання"
    return "Очікується"


def _outbound_worker_status(route_row: dict[str, object]) -> str:
    truck_status = str(route_row["truck_status"] or "")
    route_status = str(route_row["route_status"] or "")
    if truck_status == "loading":
        return "Завантаження"
    if truck_status == "loaded":
        return "Готова до виїзду"
    if truck_status == "en_route" or route_status == "in_progress":
        return "У дорозі"
    if route_status == "completed":
        return "Завершена"
    return "Очікує комплектування"


def _build_warehouse_alerts(
    *,
    warehouse_name: str,
    stock_rows: list[dict[str, object]],
    inbound: list[dict[str, object]],
    outbound: list[dict[str, object]],
) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []

    for row in stock_rows:
        if row["is_low"]:
            alerts.append(
                {
                    "level": "warning",
                    "code": "low_stock",
                    "text": f"Мало доступного запасу: {row['product_name']} ({row['available_kg']} кг) на {warehouse_name}.",
                }
            )

    waiting_receive = [route for route in inbound if route["can_receive"]]
    if waiting_receive:
        alerts.append(
            {
                "level": "info",
                "code": "inbound_waiting",
                "text": f"Є {len(waiting_receive)} вхідн. поставок, які вже прибули й чекають приймання.",
            }
        )

    blocked_outbound = [route for route in outbound if route["is_blocked"]]
    if blocked_outbound:
        alerts.append(
            {
                "level": "warning",
                "code": "outbound_blocked",
                "text": f"Є {len(blocked_outbound)} вихідн. рейсів, які не можна випустити без завершення комплектування.",
            }
        )

    if not alerts:
        alerts.append(
            {
                "level": "success",
                "code": "all_clear",
                "text": "Критичних локальних проблем не бачу. Можна працювати по черзі.",
            }
        )

    return alerts


def fetch_truck_positions_data(database_path: Path) -> dict[str, list[dict[str, object]]]:
    connection = connect(database_path)
    try:
        node_coords = {
            row["id"]: {"lat": row["lat"], "lon": row["lon"]}
            for row in connection.execute("SELECT id, lat, lon FROM nodes")
        }

        rows = list(connection.execute(
            """
            SELECT
                r.id AS route_id,
                r.truck_id,
                t.name AS truck_name,
                r.stops,
                r.timeline,
                r.departure_time,
                r.drive_hours,
                r.total_elapsed_h,
                re.status AS route_status,
                re.last_completed_stop_index,
                re.next_stop_index,
                re.started_at,
                ts.status AS truck_status,
                ts.current_lat,
                ts.current_lon
            FROM routes AS r
            JOIN trucks AS t ON t.id = r.truck_id
            LEFT JOIN route_execution AS re ON re.route_id = r.id
            LEFT JOIN truck_state AS ts ON ts.truck_id = r.truck_id
            WHERE r.is_active = 1
            ORDER BY r.id
            """
        ))
    finally:
        connection.close()

    trucks = []
    for row in rows:
        stops = json.loads(row["stops"])
        timeline = json.loads(row["timeline"]) if row["timeline"] else []
        stops_coords = [
            {"node_id": node_id, **node_coords.get(node_id, {"lat": None, "lon": None})}
            for node_id in stops
        ]
        trucks.append({
            "route_id": row["route_id"],
            "truck_id": row["truck_id"],
            "truck_name": row["truck_name"],
            "stops": stops,
            "stops_coords": stops_coords,
            "timeline": timeline,
            "departure_time": row["departure_time"],
            "drive_hours": row["drive_hours"],
            "total_elapsed_h": row["total_elapsed_h"],
            "route_status": row["route_status"],
            "last_completed_stop_index": row["last_completed_stop_index"],
            "next_stop_index": row["next_stop_index"],
            "started_at": row["started_at"],
            "truck_status": row["truck_status"],
            "current_lat": row["current_lat"],
            "current_lon": row["current_lon"],
        })

    return {"trucks": trucks}


def _build_route_stop_details(
    *,
    stops: list[str],
    timeline: list[dict[str, object]],
    node_rows: dict[str, dict[str, object]],
    product_names: dict[str, str],
    priority_map: dict[str, str],
    cargo_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    timeline_positions = _match_timeline_events_to_stops(stops, timeline)
    stop_details: list[dict[str, object]] = []

    for index, node_id in enumerate(stops):
        node = node_rows.get(node_id, {})
        event = timeline_positions[index] if index < len(timeline_positions) else None
        event_name = str(event.get("event")) if event else ""
        detail: dict[str, object] = {
            "node_id": node_id,
            "node_name": node.get("name", node_id),
            "type": node.get("type"),
            "lat": node.get("lat"),
            "lon": node.get("lon"),
            "action": _map_route_action(event_name, index, len(stops)),
            "scheduled_time": event.get("time") if event else None,
        }

        node_type = node.get("type")
        if node_type == "store":
            detail["priority"] = priority_map.get(node_id)
            detail["cargo_to_unload"] = [
                {
                    "product_id": row["product_id"],
                    "product_name": product_names.get(row["product_id"], row["product_id"]),
                    "qty_kg": row["qty_kg"],
                }
                for row in cargo_rows
                if row["stop_node_id"] == node_id
            ]
        else:
            detail["cargo_to_load"] = _build_route_load_items(
                stop_index=index,
                cargo_rows=cargo_rows,
                product_names=product_names,
            )

        stop_details.append(detail)

    return stop_details


def _match_timeline_events_to_stops(
    stops: list[str],
    timeline: list[dict[str, object]],
) -> list[dict[str, object] | None]:
    matched: list[dict[str, object] | None] = []
    timeline_cursor = 0

    for node_id in stops:
        matched_event: dict[str, object] | None = None
        while timeline_cursor < len(timeline):
            candidate = timeline[timeline_cursor]
            timeline_cursor += 1
            if candidate.get("node_id") == node_id and candidate.get("event") in {
                "departure",
                "arrival",
                "return",
            }:
                matched_event = candidate
                break
        matched.append(matched_event)

    return matched


def _map_route_action(event_name: str, stop_index: int, total_stops: int) -> str:
    if event_name == "departure" and stop_index == 0:
        return "departure"
    if event_name == "return" or stop_index == total_stops - 1:
        return "return"
    return "delivery"


def _build_route_load_items(
    *,
    stop_index: int,
    cargo_rows: list[dict[str, object]],
    product_names: dict[str, str],
) -> list[dict[str, object]]:
    if stop_index != 0:
        return []

    return [
        {
            "product_id": row["product_id"],
            "product_name": product_names.get(row["product_id"], row["product_id"]),
            "qty_kg": row["qty_kg"],
            "for_store": row["stop_node_id"],
        }
        for row in cargo_rows
    ]
