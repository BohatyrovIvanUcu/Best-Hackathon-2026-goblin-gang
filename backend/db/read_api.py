from __future__ import annotations

import json
from pathlib import Path

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
                r.leg,
                r.stops,
                r.total_km,
                r.total_cost,
                r.drive_hours,
                r.created_at,
                r.is_active
            FROM routes AS r
            JOIN trucks AS t ON t.id = r.truck_id
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
        routes = []
        for row in connection.execute(query, params):
            stops = json.loads(row["stops"])
            routes.append(
                {
                    "id": row["id"],
                    "truck_id": row["truck_id"],
                    "truck_name": row["truck_name"],
                    "truck_type": row["truck_type"],
                    "leg": row["leg"],
                    "stops": stops,
                    "stops_names": [node_names.get(node_id, node_id) for node_id in stops],
                    "total_km": row["total_km"],
                    "total_cost": row["total_cost"],
                    "estimated_hours": row["drive_hours"],
                    "created_at": row["created_at"],
                    "is_active": bool(row["is_active"]),
                }
            )
    finally:
        connection.close()

    return {"routes": routes}


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

        route_row = connection.execute(
            """
            SELECT
                id,
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
        },
    }


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
