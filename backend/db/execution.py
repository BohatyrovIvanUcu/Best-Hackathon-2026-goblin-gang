from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.db.helpers import connect, parse_datetime_value

TRUCK_MUTABLE_STATUSES = {"idle", "loading", "loaded"}
ROUTE_MUTABLE_STATUSES = {"planned", "loading"}


def reset_execution_state_for_active_plan(connection) -> None:
    timestamp = _normalize_timestamp()
    active_route_rows = connection.execute(
        """
        SELECT
            r.id,
            r.truck_id,
            r.stops,
            t.depot_node_id,
            t.capacity_kg
        FROM routes AS r
        JOIN trucks AS t ON t.id = r.truck_id
        WHERE r.is_active = 1
        ORDER BY r.id
        """
    ).fetchall()

    connection.execute("DELETE FROM route_cargo_state")
    connection.execute("DELETE FROM route_execution")
    connection.execute("DELETE FROM truck_state")

    chosen_route_by_truck: dict[str, int] = {}
    route_execution_rows: list[tuple[object, ...]] = []
    for row in active_route_rows:
        route_id = int(row["id"])
        chosen_route_by_truck.setdefault(str(row["truck_id"]), route_id)
        stops = json.loads(row["stops"])
        route_execution_rows.append(
            (
                route_id,
                "planned",
                0,
                1 if len(stops) > 1 else None,
                None,
                None,
                timestamp,
            )
        )

    if route_execution_rows:
        connection.executemany(
            """
            INSERT INTO route_execution(
                route_id,
                status,
                last_completed_stop_index,
                next_stop_index,
                started_at,
                completed_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            route_execution_rows,
        )

        connection.execute(
            """
            INSERT INTO route_cargo_state(
                route_id,
                stop_node_id,
                product_id,
                qty_reserved_kg,
                qty_loaded_kg,
                qty_delivered_kg
            )
            SELECT
                route_id,
                stop_node_id,
                product_id,
                qty_kg,
                0,
                0
            FROM route_cargo
            WHERE route_id IN (
                SELECT id FROM routes WHERE is_active = 1
            )
            """
        )

    truck_rows = connection.execute(
        """
        SELECT id, depot_node_id, capacity_kg
        FROM trucks
        ORDER BY id
        """
    ).fetchall()
    connection.executemany(
        """
        INSERT INTO truck_state(
            truck_id,
            status,
            active_route_id,
            current_node_id,
            current_lat,
            current_lon,
            last_completed_stop_index,
            remaining_capacity_kg,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["id"],
                "idle",
                chosen_route_by_truck.get(str(row["id"])),
                row["depot_node_id"],
                None,
                None,
                0,
                float(row["capacity_kg"]),
                timestamp,
            )
            for row in truck_rows
        ],
    )


def start_truck_loading(
    database_path: Path,
    *,
    truck_id: str,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_active_route_context_for_truck(connection, truck_id)
        _require_status(context["truck_status"], {"idle"}, "Truck")
        _require_status(context["route_status"], {"planned"}, "Route")

        connection.execute(
            """
            UPDATE truck_state
            SET status = 'loading', updated_at = ?
            WHERE truck_id = ?
            """,
            (timestamp, truck_id),
        )
        connection.execute(
            """
            UPDATE route_execution
            SET status = 'loading', updated_at = ?
            WHERE route_id = ?
            """,
            (timestamp, context["route_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "truck_id": truck_id,
        "route_id": context["route_id"],
        "truck_status": "loading",
        "route_status": "loading",
    }


def complete_truck_loading(
    database_path: Path,
    *,
    truck_id: str,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_active_route_context_for_truck(connection, truck_id)
        _require_status(context["truck_status"], {"loading"}, "Truck")

        load_row = connection.execute(
            """
            SELECT
                COALESCE(SUM(qty_reserved_kg), 0) AS qty_reserved_kg,
                COALESCE(SUM(qty_loaded_kg), 0) AS qty_loaded_kg
            FROM route_cargo_state
            WHERE route_id = ?
            """,
            (context["route_id"],),
        ).fetchone()
        qty_reserved_kg = float(load_row["qty_reserved_kg"])
        qty_loaded_kg = float(load_row["qty_loaded_kg"])

        if int(context["leg"]) == 2:
            if qty_reserved_kg > 0:
                raise ValueError("Не всі товари видані зі складу. Заверши списання по кожному товару.")
            total_loaded_kg = qty_loaded_kg
        else:
            total_loaded_kg = qty_reserved_kg + qty_loaded_kg
            connection.execute(
                """
                UPDATE route_cargo_state
                SET
                    qty_loaded_kg = qty_loaded_kg + qty_reserved_kg,
                    qty_reserved_kg = 0
                WHERE route_id = ?
                """,
                (context["route_id"],),
            )

        remaining_capacity_kg = round(
            max(0.0, float(context["truck_capacity_kg"]) - total_loaded_kg),
            2,
        )
        connection.execute(
            """
            UPDATE truck_state
            SET
                status = 'loaded',
                remaining_capacity_kg = ?,
                updated_at = ?
            WHERE truck_id = ?
            """,
            (remaining_capacity_kg, timestamp, truck_id),
        )
        connection.execute(
            """
            UPDATE route_execution
            SET status = 'loading', updated_at = ?
            WHERE route_id = ?
            """,
            (timestamp, context["route_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "truck_id": truck_id,
        "route_id": context["route_id"],
        "truck_status": "loaded",
        "route_status": "loading",
        "loaded_kg": total_loaded_kg,
        "remaining_capacity_kg": remaining_capacity_kg,
    }


def mark_inbound_route_arrived(
    database_path: Path,
    *,
    warehouse_id: str,
    route_id: int,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_inbound_warehouse_route_context(connection, warehouse_id=warehouse_id, route_id=route_id)
        if context["warehouse_received_at"] is not None:
            raise ValueError("Поставка для цього складу вже прийнята")
        if context["warehouse_arrived_at"] is not None:
            raise ValueError("Прибуття на склад уже підтверджене")

        warehouse_stop_index = _find_intermediate_stop_index(context["stops"], warehouse_id)
        connection.execute(
            """
            UPDATE route_execution
            SET
                status = 'in_progress',
                started_at = COALESCE(started_at, ?),
                warehouse_arrived_at = ?,
                next_stop_index = ?,
                updated_at = ?
            WHERE route_id = ?
            """,
            (timestamp, timestamp, warehouse_stop_index, timestamp, route_id),
        )
        connection.execute(
            """
            UPDATE truck_state
            SET
                status = 'unloading',
                current_node_id = ?,
                updated_at = ?
            WHERE truck_id = ?
            """,
            (warehouse_id, timestamp, context["truck_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "route_id": route_id,
        "truck_id": context["truck_id"],
        "warehouse_id": warehouse_id,
        "warehouse_arrived_at": timestamp,
        "worker_status": "ARRIVED",
    }


def receive_inbound_route(
    database_path: Path,
    *,
    warehouse_id: str,
    route_id: int,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_inbound_warehouse_route_context(connection, warehouse_id=warehouse_id, route_id=route_id)
        if context["warehouse_arrived_at"] is None:
            raise ValueError("Спершу підтвердь прибуття фури на склад")
        if context["warehouse_received_at"] is not None:
            raise ValueError("Поставка для цього складу вже прийнята")

        warehouse_stop_index = _find_intermediate_stop_index(context["stops"], warehouse_id)
        inbound_rows = connection.execute(
            """
            SELECT
                rcs.product_id,
                COALESCE(p.name, rcs.product_id) AS product_name,
                ROUND(rcs.qty_reserved_kg + rcs.qty_loaded_kg - rcs.qty_delivered_kg, 2) AS qty_to_receive_kg
            FROM route_cargo_state AS rcs
            LEFT JOIN products AS p ON p.id = rcs.product_id
            WHERE rcs.route_id = ? AND rcs.stop_node_id = ?
            ORDER BY rcs.product_id
            """,
            (route_id, warehouse_id),
        ).fetchall()
        if not inbound_rows:
            raise LookupError("Для цього inbound-рейсу не знайдено вантаж на вибраний склад")

        received_items: list[dict[str, object]] = []
        total_received_kg = 0.0
        for row in inbound_rows:
            qty_to_receive_kg = round(float(row["qty_to_receive_kg"]), 2)
            if qty_to_receive_kg <= 0:
                continue
            total_received_kg += qty_to_receive_kg
            connection.execute(
                """
                INSERT INTO warehouse_stock(warehouse_id, product_id, quantity_kg, reserved_kg)
                VALUES(?, ?, ?, 0)
                ON CONFLICT(warehouse_id, product_id) DO UPDATE
                SET quantity_kg = warehouse_stock.quantity_kg + excluded.quantity_kg
                """,
                (warehouse_id, row["product_id"], qty_to_receive_kg),
            )
            received_items.append(
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "qty_received_kg": qty_to_receive_kg,
                }
            )

        connection.execute(
            """
            UPDATE route_cargo_state
            SET
                qty_loaded_kg = qty_loaded_kg + qty_reserved_kg,
                qty_delivered_kg = qty_loaded_kg + qty_reserved_kg,
                qty_reserved_kg = 0
            WHERE route_id = ? AND stop_node_id = ?
            """,
            (route_id, warehouse_id),
        )

        next_stop_index_after = warehouse_stop_index + 1 if warehouse_stop_index + 1 < len(context["stops"]) else None
        if next_stop_index_after is None:
            connection.execute(
                """
                UPDATE route_execution
                SET
                    status = 'completed',
                    last_completed_stop_index = ?,
                    next_stop_index = NULL,
                    warehouse_received_at = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE route_id = ?
                """,
                (warehouse_stop_index, timestamp, timestamp, timestamp, route_id),
            )
            next_active_route_id = _find_next_planned_route_for_truck(
                connection,
                truck_id=str(context["truck_id"]),
                completed_route_id=route_id,
            )
            connection.execute(
                """
                UPDATE truck_state
                SET
                    status = 'idle',
                    active_route_id = ?,
                    current_node_id = ?,
                    last_completed_stop_index = 0,
                    remaining_capacity_kg = ?,
                    updated_at = ?
                WHERE truck_id = ?
                """,
                (
                    next_active_route_id,
                    warehouse_id,
                    float(context["truck_capacity_kg"]),
                    timestamp,
                    context["truck_id"],
                ),
            )
            route_status = "completed"
        else:
            connection.execute(
                """
                UPDATE route_execution
                SET
                    status = 'in_progress',
                    last_completed_stop_index = ?,
                    next_stop_index = ?,
                    warehouse_received_at = ?,
                    updated_at = ?
                WHERE route_id = ?
                """,
                (warehouse_stop_index, next_stop_index_after, timestamp, timestamp, route_id),
            )
            connection.execute(
                """
                UPDATE truck_state
                SET
                    status = 'idle',
                    current_node_id = ?,
                    last_completed_stop_index = ?,
                    remaining_capacity_kg = ?,
                    updated_at = ?
                WHERE truck_id = ?
                """,
                (
                    warehouse_id,
                    warehouse_stop_index,
                    float(context["truck_capacity_kg"]),
                    timestamp,
                    context["truck_id"],
                ),
            )
            route_status = "in_progress"

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "route_id": route_id,
        "truck_id": context["truck_id"],
        "warehouse_id": warehouse_id,
        "warehouse_received_at": timestamp,
        "received_items": received_items,
        "total_received_kg": round(total_received_kg, 2),
        "route_status": route_status,
    }


def issue_outbound_route_item(
    database_path: Path,
    *,
    warehouse_id: str,
    route_id: int,
    stop_node_id: str,
    product_id: str,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_outbound_warehouse_route_context(connection, warehouse_id=warehouse_id, route_id=route_id)
        if context["truck_status"] not in {"idle", "loading"} or context["route_status"] not in {"planned", "loading"}:
            raise ValueError("Видачу товару можна робити лише до відправки машини")

        cargo_row = connection.execute(
            """
            SELECT
                rcs.qty_reserved_kg,
                COALESCE(p.name, rcs.product_id) AS product_name,
                COALESCE(n.name, rcs.stop_node_id) AS stop_node_name
            FROM route_cargo_state AS rcs
            LEFT JOIN products AS p ON p.id = rcs.product_id
            LEFT JOIN nodes AS n ON n.id = rcs.stop_node_id
            WHERE rcs.route_id = ? AND rcs.stop_node_id = ? AND rcs.product_id = ?
            """,
            (route_id, stop_node_id, product_id),
        ).fetchone()
        if cargo_row is None:
            raise LookupError("Товар для цього outbound-рейсу не знайдено")

        qty_to_issue_kg = round(float(cargo_row["qty_reserved_kg"]), 2)
        if qty_to_issue_kg <= 0:
            raise ValueError("Цей товар уже виданий або не потребує списання")

        stock_row = connection.execute(
            """
            SELECT quantity_kg, reserved_kg
            FROM warehouse_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (warehouse_id, product_id),
        ).fetchone()
        if stock_row is None:
            raise LookupError("Рядок залишку для цього товару на складі не знайдено")

        if float(stock_row["quantity_kg"]) < qty_to_issue_kg:
            raise ValueError("На складі недостатньо товару для видачі")

        connection.execute(
            """
            UPDATE warehouse_stock
            SET
                quantity_kg = quantity_kg - ?,
                reserved_kg = MAX(0, reserved_kg - ?)
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (qty_to_issue_kg, qty_to_issue_kg, warehouse_id, product_id),
        )
        connection.execute(
            """
            UPDATE route_cargo_state
            SET
                qty_loaded_kg = qty_loaded_kg + qty_reserved_kg,
                qty_reserved_kg = 0
            WHERE route_id = ? AND stop_node_id = ? AND product_id = ?
            """,
            (route_id, stop_node_id, product_id),
        )
        connection.execute(
            """
            UPDATE route_execution
            SET updated_at = ?
            WHERE route_id = ?
            """,
            (timestamp, route_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "route_id": route_id,
        "truck_id": context["truck_id"],
        "warehouse_id": warehouse_id,
        "stop_node_id": stop_node_id,
        "product_id": product_id,
        "product_name": cargo_row["product_name"],
        "stop_node_name": cargo_row["stop_node_name"],
        "qty_issued_kg": qty_to_issue_kg,
        "updated_at": timestamp,
    }


def depart_truck(
    database_path: Path,
    *,
    truck_id: str,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_active_route_context_for_truck(connection, truck_id)
        _require_status(context["truck_status"], {"loaded"}, "Truck")
        _require_status(context["route_status"], {"planned", "loading"}, "Route")

        current_node_id = _current_node_for_progress(
            stops_json=context["stops"],
            last_completed_stop_index=int(context["last_completed_stop_index"]),
        )

        connection.execute(
            """
            UPDATE truck_state
            SET
                status = 'en_route',
                current_node_id = ?,
                updated_at = ?
            WHERE truck_id = ?
            """,
            (current_node_id, timestamp, truck_id),
        )
        connection.execute(
            """
            UPDATE route_execution
            SET
                status = 'in_progress',
                started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE route_id = ?
            """,
            (timestamp, timestamp, context["route_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "truck_id": truck_id,
        "route_id": context["route_id"],
        "truck_status": "en_route",
        "route_status": "in_progress",
        "current_node_id": current_node_id,
    }


def update_truck_position(
    database_path: Path,
    *,
    truck_id: str,
    current_lat: float,
    current_lon: float,
    current_node_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(updated_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT truck_id
            FROM truck_state
            WHERE truck_id = ?
            """,
            (truck_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Execution state not found for truck_id={truck_id}")

        connection.execute(
            """
            UPDATE truck_state
            SET
                current_lat = ?,
                current_lon = ?,
                current_node_id = COALESCE(?, current_node_id),
                updated_at = ?
            WHERE truck_id = ?
            """,
            (
                float(current_lat),
                float(current_lon),
                current_node_id,
                timestamp,
                truck_id,
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
        "truck_id": truck_id,
        "current_lat": float(current_lat),
        "current_lon": float(current_lon),
        "current_node_id": current_node_id,
        "updated_at": timestamp,
    }


def complete_route_stop(
    database_path: Path,
    *,
    route_id: int,
    completed_at: str | None = None,
) -> dict[str, object]:
    timestamp = _normalize_timestamp(completed_at)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN")
        context = _require_route_progress_context(connection, route_id)
        _require_status(context["route_status"], {"in_progress"}, "Route")
        if int(context["active_route_id"] or 0) != route_id:
            raise ValueError(f"Route {route_id} is not the active route for truck {context['truck_id']}")

        stops = json.loads(context["stops"])
        next_stop_index = context["next_stop_index"]
        if next_stop_index is None:
            raise ValueError(f"Route {route_id} has no remaining stops to complete")
        completed_index = int(next_stop_index)
        if completed_index >= len(stops):
            raise ValueError(f"Route {route_id} next_stop_index is out of range")

        completed_node_id = str(stops[completed_index])
        delivered_qty_row = connection.execute(
            """
            SELECT COALESCE(SUM(qty_loaded_kg - qty_delivered_kg), 0) AS delivered_now_kg
            FROM route_cargo_state
            WHERE route_id = ? AND stop_node_id = ?
            """,
            (route_id, completed_node_id),
        ).fetchone()
        delivered_now_kg = round(float(delivered_qty_row["delivered_now_kg"]), 2)

        connection.execute(
            """
            UPDATE route_cargo_state
            SET qty_delivered_kg = qty_loaded_kg
            WHERE route_id = ? AND stop_node_id = ?
            """,
            (route_id, completed_node_id),
        )

        next_stop_index_after = completed_index + 1 if completed_index + 1 < len(stops) else None
        remaining_capacity_kg = round(
            min(
                float(context["truck_capacity_kg"]),
                float(context["remaining_capacity_kg"]) + delivered_now_kg,
            ),
            2,
        )

        if next_stop_index_after is None:
            connection.execute(
                """
                UPDATE route_execution
                SET
                    status = 'completed',
                    last_completed_stop_index = ?,
                    next_stop_index = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE route_id = ?
                """,
                (completed_index, timestamp, timestamp, route_id),
            )
            next_active_route_id = _find_next_planned_route_for_truck(
                connection,
                truck_id=str(context["truck_id"]),
                completed_route_id=route_id,
            )
            connection.execute(
                """
                UPDATE truck_state
                SET
                    status = 'idle',
                    active_route_id = ?,
                    current_node_id = ?,
                    last_completed_stop_index = 0,
                    remaining_capacity_kg = ?,
                    updated_at = ?
                WHERE truck_id = ?
                """,
                (
                    next_active_route_id,
                    context["depot_node_id"] if next_active_route_id is not None else completed_node_id,
                    float(context["truck_capacity_kg"]),
                    timestamp,
                    context["truck_id"],
                ),
            )
            truck_status = "idle"
            route_status = "completed"
        else:
            connection.execute(
                """
                UPDATE route_execution
                SET
                    last_completed_stop_index = ?,
                    next_stop_index = ?,
                    updated_at = ?
                WHERE route_id = ?
                """,
                (completed_index, next_stop_index_after, timestamp, route_id),
            )
            connection.execute(
                """
                UPDATE truck_state
                SET
                    status = 'en_route',
                    current_node_id = ?,
                    last_completed_stop_index = ?,
                    remaining_capacity_kg = ?,
                    updated_at = ?
                WHERE truck_id = ?
                """,
                (
                    completed_node_id,
                    completed_index,
                    remaining_capacity_kg,
                    timestamp,
                    context["truck_id"],
                ),
            )
            truck_status = "en_route"
            route_status = "in_progress"

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": "ok",
        "route_id": route_id,
        "truck_id": context["truck_id"],
        "completed_stop_index": completed_index,
        "completed_node_id": completed_node_id,
        "delivered_now_kg": delivered_now_kg,
        "remaining_capacity_kg": remaining_capacity_kg,
        "truck_status": truck_status,
        "route_status": route_status,
        "next_stop_index": next_stop_index_after,
    }


def fetch_route_execution_details(
    database_path: Path,
    route_id: int,
) -> dict[str, object]:
    connection = connect(database_path)
    try:
        return _fetch_route_execution_details(connection, route_id)
    finally:
        connection.close()


def _fetch_route_execution_details(connection, route_id: int) -> dict[str, object]:
    route_row = connection.execute(
        """
        SELECT
            r.id,
            r.truck_id,
            r.leg,
            r.supersedes_route_id,
            r.stops,
            r.is_active,
            re.status AS route_status,
            re.last_completed_stop_index,
            re.next_stop_index,
            re.started_at,
            re.warehouse_arrived_at,
            re.warehouse_received_at,
            re.completed_at,
            re.updated_at AS route_updated_at,
            ts.status AS truck_status,
            ts.active_route_id,
            ts.current_node_id,
            ts.current_lat,
            ts.current_lon,
            ts.remaining_capacity_kg,
            ts.updated_at AS truck_updated_at
        FROM routes AS r
        LEFT JOIN route_execution AS re ON re.route_id = r.id
        LEFT JOIN truck_state AS ts ON ts.truck_id = r.truck_id
        WHERE r.id = ?
        """,
        (route_id,),
    ).fetchone()
    if route_row is None:
        raise LookupError(f"Route execution not found for route_id={route_id}")

    stops = json.loads(route_row["stops"])
    next_stop_index = route_row["next_stop_index"]
    cargo_state_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                stop_node_id,
                product_id,
                qty_reserved_kg,
                qty_loaded_kg,
                qty_delivered_kg
            FROM route_cargo_state
            WHERE route_id = ?
            ORDER BY stop_node_id, product_id
            """,
            (route_id,),
        )
    ]

    return {
        "route_id": route_id,
        "truck_id": route_row["truck_id"],
        "leg": route_row["leg"],
        "supersedes_route_id": route_row["supersedes_route_id"],
        "is_active": bool(route_row["is_active"]),
        "route_status": route_row["route_status"],
        "last_completed_stop_index": route_row["last_completed_stop_index"],
        "next_stop_index": next_stop_index,
        "started_at": route_row["started_at"],
        "warehouse_arrived_at": route_row["warehouse_arrived_at"],
        "warehouse_received_at": route_row["warehouse_received_at"],
        "completed_at": route_row["completed_at"],
        "updated_at": route_row["route_updated_at"],
        "locked_prefix": stops[: int(route_row["last_completed_stop_index"] or 0) + 1],
        "remaining_suffix": stops[int(next_stop_index) :] if next_stop_index is not None else [],
        "current_stop_node_id": stops[int(next_stop_index)] if next_stop_index is not None else None,
        "truck_state": {
            "status": route_row["truck_status"],
            "active_route_id": route_row["active_route_id"],
            "current_node_id": route_row["current_node_id"],
            "current_lat": route_row["current_lat"],
            "current_lon": route_row["current_lon"],
            "remaining_capacity_kg": route_row["remaining_capacity_kg"],
            "updated_at": route_row["truck_updated_at"],
        },
        "cargo_state": [
            {
                **row,
                "qty_onboard_kg": round(max(0.0, float(row["qty_loaded_kg"]) - float(row["qty_delivered_kg"])), 2),
            }
            for row in cargo_state_rows
        ],
    }


def _require_active_route_context_for_truck(connection, truck_id: str):
    row = connection.execute(
        """
        SELECT
            ts.truck_id,
            ts.status AS truck_status,
            ts.active_route_id AS route_id,
            ts.last_completed_stop_index,
            r.leg,
            r.stops,
            re.status AS route_status,
            t.capacity_kg AS truck_capacity_kg
        FROM truck_state AS ts
        JOIN trucks AS t ON t.id = ts.truck_id
        LEFT JOIN routes AS r ON r.id = ts.active_route_id AND r.is_active = 1
        LEFT JOIN route_execution AS re ON re.route_id = ts.active_route_id
        WHERE ts.truck_id = ?
        """,
        (truck_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Execution state not found for truck_id={truck_id}")
    if row["route_id"] is None:
        raise ValueError(f"Truck {truck_id} does not have an active route")
    if row["route_status"] is None:
        raise LookupError(f"Execution state not found for active route_id={row['route_id']}")
    return row


def _require_inbound_warehouse_route_context(connection, *, warehouse_id: str, route_id: int):
    row = connection.execute(
        """
        SELECT
            r.id,
            r.truck_id,
            r.leg,
            r.stops,
            re.status AS route_status,
            re.last_completed_stop_index,
            re.next_stop_index,
            re.started_at,
            re.warehouse_arrived_at,
            re.warehouse_received_at,
            ts.active_route_id,
            ts.status AS truck_status,
            ts.remaining_capacity_kg,
            t.capacity_kg AS truck_capacity_kg
        FROM routes AS r
        JOIN route_execution AS re ON re.route_id = r.id
        JOIN trucks AS t ON t.id = r.truck_id
        JOIN truck_state AS ts ON ts.truck_id = r.truck_id
        WHERE r.id = ? AND r.is_active = 1
        """,
        (route_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Active route not found for route_id={route_id}")
    if int(row["leg"]) != 1:
        raise ValueError("Ця дія доступна лише для inbound leg 1")
    if int(row["active_route_id"] or 0) != route_id:
        raise ValueError("Потрібно працювати лише з активним inbound-рейсом")
    stops = json.loads(row["stops"])
    _find_intermediate_stop_index(stops, warehouse_id)
    return {
        **dict(row),
        "stops": stops,
    }


def _require_outbound_warehouse_route_context(connection, *, warehouse_id: str, route_id: int):
    row = connection.execute(
        """
        SELECT
            r.id,
            r.truck_id,
            r.leg,
            r.stops,
            re.status AS route_status,
            ts.active_route_id,
            ts.status AS truck_status
        FROM routes AS r
        JOIN route_execution AS re ON re.route_id = r.id
        JOIN truck_state AS ts ON ts.truck_id = r.truck_id
        WHERE r.id = ? AND r.is_active = 1
        """,
        (route_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Active route not found for route_id={route_id}")
    if int(row["leg"]) != 2:
        raise ValueError("Ця дія доступна лише для outbound leg 2")
    if int(row["active_route_id"] or 0) != route_id:
        raise ValueError("Потрібно працювати лише з активним outbound-рейсом")
    stops = json.loads(row["stops"])
    if not stops or str(stops[0]) != warehouse_id:
        raise ValueError("Обраний рейс не стартує з цього складу")
    return {
        **dict(row),
        "stops": stops,
    }


def _require_route_progress_context(connection, route_id: int):
    row = connection.execute(
        """
        SELECT
            r.id,
            r.truck_id,
            r.stops,
            re.status AS route_status,
            re.last_completed_stop_index,
            re.next_stop_index,
            ts.active_route_id,
            ts.status AS truck_status,
            ts.remaining_capacity_kg,
            t.capacity_kg AS truck_capacity_kg,
            t.depot_node_id
        FROM routes AS r
        JOIN route_execution AS re ON re.route_id = r.id
        JOIN trucks AS t ON t.id = r.truck_id
        LEFT JOIN truck_state AS ts ON ts.truck_id = r.truck_id
        WHERE r.id = ? AND r.is_active = 1
        """,
        (route_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Active route not found for route_id={route_id}")
    return row


def _find_next_planned_route_for_truck(connection, *, truck_id: str, completed_route_id: int) -> int | None:
    row = connection.execute(
        """
        SELECT r.id
        FROM routes AS r
        JOIN route_execution AS re ON re.route_id = r.id
        WHERE
            r.truck_id = ?
            AND r.is_active = 1
            AND r.id <> ?
            AND re.status = 'planned'
        ORDER BY r.id
        LIMIT 1
        """,
        (truck_id, completed_route_id),
    ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def _current_node_for_progress(*, stops_json: str, last_completed_stop_index: int) -> str | None:
    stops = json.loads(stops_json)
    if not stops:
        return None
    bounded_index = max(0, min(last_completed_stop_index, len(stops) - 1))
    return str(stops[bounded_index])


def _find_intermediate_stop_index(stops: list[str], warehouse_id: str) -> int:
    for index, stop_id in enumerate(stops):
        if index == 0:
            continue
        if str(stop_id) == warehouse_id:
            return index
    raise ValueError(f"Склад {warehouse_id} не входить до цього маршруту як точка приймання")


def _normalize_timestamp(raw_value: str | None = None) -> str:
    if raw_value is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parsed = parse_datetime_value(raw_value)
    if not isinstance(parsed, datetime):
        raise ValueError("Timestamp must be in ISO datetime format")
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _require_status(current_status: str | None, allowed_statuses: set[str], entity_name: str) -> None:
    if current_status not in allowed_statuses:
        allowed_text = ", ".join(sorted(allowed_statuses))
        raise ValueError(
            f"{entity_name} status must be one of: {allowed_text}. Current status: {current_status!r}"
        )


__all__ = [
    "ROUTE_MUTABLE_STATUSES",
    "TRUCK_MUTABLE_STATUSES",
    "complete_route_stop",
    "complete_truck_loading",
    "depart_truck",
    "fetch_route_execution_details",
    "issue_outbound_route_item",
    "mark_inbound_route_arrived",
    "receive_inbound_route",
    "reset_execution_state_for_active_plan",
    "start_truck_loading",
    "update_truck_position",
]
